from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, g

from medical_app.builder import build_catalog
from medical_app.catalog import Catalog
from medical_app.practice import PracticeStore
from medical_app.study_export import create_study_export_blueprint


def _services(sample_source_tree: Path, tmp_path: Path):
    manifest = build_catalog(sample_source_tree, tmp_path / "builds")
    catalog = Catalog(tmp_path / "builds" / manifest["build_id"] / "catalog.sqlite3")
    alice = PracticeStore(tmp_path / "state" / "app.sqlite3", catalog, "usr_alice")
    alice.initialize()
    bob = PracticeStore(alice.database_path, catalog, "usr_bob")
    bob.initialize()
    return catalog, alice, bob


def test_personal_study_export_is_streamed_and_account_scoped(
    sample_source_tree: Path, tmp_path: Path
):
    catalog, alice, bob = _services(sample_source_tree, tmp_path)
    collection = next(
        item
        for item in catalog.list_collections()
        if item["relative_path"].endswith("第一章.xlsx")
    )
    items = catalog.list_collection_questions(collection["collection_id"])
    own_item = items[0]
    other_item = items[1]

    session = alice.create_session(count=1, filters={"question_type": "单选题"})
    session_item = session["items"][0]
    alice.add_favorite(
        occurrence_id=own_item["occurrence_id"], question_id=own_item["question_id"]
    )
    alice.save_note(own_item["occurrence_id"], "=不要执行的表格内容")
    alice.submit_answer(
        session["session_id"], position=session_item["position"], answer="B"
    )

    bob.add_favorite(
        occurrence_id=other_item["occurrence_id"], question_id=other_item["question_id"]
    )
    bob.save_note(other_item["occurrence_id"], "Bob 的私有笔记")

    app = Flask(__name__)

    @app.before_request
    def authenticate():
        g.current_user = app.config.get("CURRENT_USER")

    app.register_blueprint(create_study_export_blueprint(alice.database_path))
    client = app.test_client()
    assert client.get("/api/study/export").status_code == 401

    app.config["CURRENT_USER"] = {
        "user_id": "usr_alice",
        "username": "Alice",
        "role": "user",
    }
    jsonl = client.get("/api/study/export?format=jsonl")
    assert jsonl.status_code == 200
    assert jsonl.mimetype == "application/x-ndjson"
    assert jsonl.headers["Cache-Control"] == "private, no-store"
    assert jsonl.headers["X-Accel-Buffering"] == "no"
    assert 'medical-study-jsonl.jsonl' in jsonl.headers["Content-Disposition"]
    records = [json.loads(line) for line in jsonl.get_data(as_text=True).splitlines()]
    assert {record["record_type"] for record in records} >= {
        "study_item",
        "note",
        "progress",
        "session",
        "session_question",
    }
    progress = next(record for record in records if record["record_type"] == "progress")
    assert progress["wrong_count"] == 1
    assert not any(
        record["record_type"] in {"study_item", "note"}
        and record.get("occurrence_id") == other_item["occurrence_id"]
        for record in records
    )
    assert all("profile_id" not in record for record in records)

    csv_response = client.get("/api/study/export?format=csv")
    csv_body = csv_response.get_data(as_text=True)
    assert csv_response.status_code == 200
    assert csv_body.startswith("\ufeffrecord_type,")
    assert "'=不要执行的表格内容" in csv_body
    assert client.get("/api/study/export?format=xml").status_code == 400

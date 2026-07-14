from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask

from medical_v2.builder import build_catalog
from medical_v2.catalog import Catalog
from medical_v2.practice import PracticeConflict, PracticeStore
from medical_v2.practice_api import create_practice_blueprint


def _services(sample_source_tree: Path, tmp_path: Path):
    manifest = build_catalog(sample_source_tree, tmp_path / "builds")
    catalog_path = tmp_path / "builds" / manifest["build_id"] / "catalog.sqlite3"
    catalog = Catalog(catalog_path)
    store = PracticeStore(tmp_path / "state" / "practice.sqlite3", catalog)
    store.initialize()
    return catalog, store


def test_practice_session_hides_answers_and_tracks_wrong_questions(
    sample_source_tree: Path, tmp_path: Path
):
    _, store = _services(sample_source_tree, tmp_path)
    session = store.create_session(count=1, filters={"question_type": "单选题"})

    assert session["summary"]["total"] == 1
    item = session["items"][0]
    assert "answer" not in item["question"]
    assert "explanation" not in item["question"]

    result = store.submit_answer(
        session["session_id"], position=item["position"], answer="B", elapsed_ms=1200
    )
    assert result["is_correct"] is False
    assert result["correct_answer"] == "A"
    assert result["explanation"]

    completed = store.get_session(session["session_id"])
    assert completed["status"] == "complete"
    assert completed["summary"]["wrong"] == 1
    assert "answer" not in completed["items"][0]["question"]

    wrong = store.wrong_questions()
    assert len(wrong) == 1
    assert wrong[0]["progress"]["wrong_count"] == 1

    with pytest.raises(PracticeConflict):
        store.submit_answer(session["session_id"], position=1, answer="A")


def test_practice_rejects_invalid_choice_and_serializes_concurrent_submissions(
    sample_source_tree: Path, tmp_path: Path
):
    _, store = _services(sample_source_tree, tmp_path)
    session = store.create_session(count=1, filters={"question_type": "单选题"})
    session_id = session["session_id"]

    with pytest.raises(ValueError):
        store.submit_answer(session_id, position=1, answer="Z")

    def submit(answer: str):
        try:
            return store.submit_answer(session_id, position=1, answer=answer)
        except PracticeConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ("A", "B")))

    assert results.count("conflict") == 1
    assert sum(isinstance(result, dict) for result in results) == 1
    progress = store.wrong_questions()
    health = store.health()
    assert health["attempts"] == 1
    assert len(progress) in {0, 1}


def test_practice_blueprint_round_trip(sample_source_tree: Path, tmp_path: Path):
    catalog, store = _services(sample_source_tree, tmp_path)
    app = Flask(__name__)
    app.register_blueprint(create_practice_blueprint(catalog.database_path, store.database_path))
    client = app.test_client()

    assert client.get("/api/v2/practice/health").status_code == 200
    created = client.post(
        "/api/v2/practice/sessions",
        json={"count": 1, "filters": {"question_type": "单选题"}},
    )
    assert created.status_code == 201
    session = created.get_json()
    assert "answer" not in session["items"][0]["question"]

    submitted = client.post(
        f"/api/v2/practice/sessions/{session['session_id']}/answers",
        json={"position": 1, "answer": "B"},
    )
    assert submitted.status_code == 200
    assert submitted.get_json()["is_correct"] is False
    assert client.get("/api/v2/practice/wrong").get_json()["count"] == 1
    assert client.post("/api/v2/practice/sessions", json=[]).status_code == 400

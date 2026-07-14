from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sqlite3

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


def test_collection_practice_preserves_file_order_and_outline_progress(
    sample_source_tree: Path, tmp_path: Path
):
    catalog, store = _services(sample_source_tree, tmp_path)
    collection = next(
        item
        for item in catalog.list_collections()
        if item["relative_path"].endswith("第一章.xlsx")
    )
    outline = store.collection_outline(collection["collection_id"])
    assert [item["collection_position"] for item in outline["items"]] == [1, 2, 3, 4]
    assert outline["summary"] == {
        "total": 4,
        "answered": 0,
        "correct": 0,
        "wrong": 0,
        "ungraded": 0,
    }

    session = store.create_session(
        collection_id=collection["collection_id"],
        count=None,
        start_position=2,
        order_mode="sequential",
    )
    assert [item["collection_position"] for item in session["items"]] == [2, 3, 4]
    assert all("answer" not in item["question"] for item in session["items"])

    first = session["items"][0]
    result = store.submit_answer(session["session_id"], position=1, answer="任意文本")
    assert result["is_correct"] is False
    refreshed = store.get_session(session["session_id"])
    assert refreshed["items"][0]["submission"]["correct_answer"]
    assert "answer" not in refreshed["items"][1]["question"]
    updated_outline = store.collection_outline(collection["collection_id"])
    assert updated_outline["items"][1]["status"] == "wrong"


def test_practice_schema_v1_migrates_without_losing_state(
    sample_source_tree: Path, tmp_path: Path
):
    manifest = build_catalog(sample_source_tree, tmp_path / "builds")
    catalog_path = tmp_path / "builds" / manifest["build_id"] / "catalog.sqlite3"
    catalog = Catalog(catalog_path)
    question_id = catalog.sample_questions(count=1)[0]["question_id"]
    database_path = tmp_path / "state" / "practice.sqlite3"
    database_path.parent.mkdir(parents=True)

    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE practice_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE practice_sessions (
                session_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                collection_id TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL DEFAULT 'practice',
                filters_json TEXT NOT NULL DEFAULT '{}',
                total_questions INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE practice_session_questions (
                session_id TEXT NOT NULL REFERENCES practice_sessions(session_id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                question_id TEXT NOT NULL,
                submitted_answer TEXT,
                normalized_submitted_answer TEXT,
                is_correct INTEGER,
                answered_at TEXT,
                elapsed_ms INTEGER,
                PRIMARY KEY (session_id, position),
                UNIQUE (session_id, question_id)
            );
            CREATE TABLE question_progress (
                profile_id TEXT NOT NULL,
                question_id TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                correct_count INTEGER NOT NULL DEFAULT 0,
                wrong_count INTEGER NOT NULL DEFAULT 0,
                ungraded_count INTEGER NOT NULL DEFAULT 0,
                streak INTEGER NOT NULL DEFAULT 0,
                last_answer TEXT NOT NULL DEFAULT '',
                last_result INTEGER,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (profile_id, question_id)
            );
            CREATE INDEX idx_session_questions_question
            ON practice_session_questions(question_id);
            CREATE INDEX idx_progress_wrong
            ON question_progress(profile_id, wrong_count, last_seen_at);
            """
        )
        connection.execute(
            "INSERT INTO practice_meta(key, value) VALUES ('schema_version', '1')"
        )
        connection.execute(
            """
            INSERT INTO practice_sessions(
                session_id, profile_id, total_questions, created_at
            ) VALUES ('legacy-session', 'local', 1, '2026-01-01T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO practice_session_questions(
                session_id, position, question_id, submitted_answer,
                normalized_submitted_answer, is_correct, answered_at, elapsed_ms
            ) VALUES (
                'legacy-session', 1, ?, 'A', 'A', 1,
                '2026-01-01T00:01:00+00:00', 900
            )
            """,
            (question_id,),
        )
        connection.commit()
    finally:
        connection.close()

    store = PracticeStore(database_path, catalog)
    health = store.initialize()
    assert health["schema_version"] == 2

    connection = sqlite3.connect(database_path)
    try:
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(practice_session_questions)"
            )
        }
        assert {"occurrence_id", "collection_position"} <= columns
        row = connection.execute(
            """
            SELECT submitted_answer, is_correct, elapsed_ms
            FROM practice_session_questions
            WHERE session_id = 'legacy-session' AND position = 1
            """
        ).fetchone()
        assert row == ("A", 1, 900)
        assert connection.execute(
            "SELECT value FROM practice_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == "2"

        # Schema v2 intentionally permits the same canonical question to occur
        # more than once in one source file/session.
        connection.execute(
            """
            INSERT INTO practice_session_questions(
                session_id, position, question_id
            ) VALUES ('legacy-session', 2, ?)
            """,
            (question_id,),
        )
        connection.commit()
    finally:
        connection.close()


def test_file_practice_uses_occurrence_version_and_scoped_progress(
    sample_source_tree: Path, tmp_path: Path
):
    catalog, store = _services(sample_source_tree, tmp_path)
    collections = catalog.list_collections()
    conflict_collection = next(
        item for item in collections if item["relative_path"].endswith("重复题.xlsx")
    )
    canonical_collection = next(
        item for item in collections if item["relative_path"].endswith("第一章.xlsx")
    )

    session = store.create_session(
        collection_id=conflict_collection["collection_id"],
        count=None,
        order_mode="sequential",
    )
    question = session["items"][0]["question"]
    assert question["sources"][0]["relative_path"].endswith("重复题.xlsx")
    assert "answer" not in question

    # The exact source occurrence intentionally says B while the canonical
    # version says A. File practice must grade against the source occurrence.
    result = store.submit_answer(session["session_id"], position=1, answer="B")
    assert result["is_correct"] is True
    assert result["correct_answer"] == "B"
    assert store.collection_outline(conflict_collection["collection_id"])["items"][0][
        "status"
    ] == "correct"

    # The same canonical question in another file remains untouched.
    canonical_outline = store.collection_outline(canonical_collection["collection_id"])
    assert canonical_outline["items"][0]["source_code"] == "2020N1A"
    assert canonical_outline["items"][0]["status"] == "unanswered"

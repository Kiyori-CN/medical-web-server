from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .catalog import Catalog
from .normalize import display_text, normalize_answer


PRACTICE_SCHEMA_VERSION = 1


class PracticeUnavailable(RuntimeError):
    pass


class PracticeNotFound(LookupError):
    pass


class PracticeConflict(RuntimeError):
    pass


PRACTICE_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS practice_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS practice_sessions (
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

CREATE TABLE IF NOT EXISTS practice_session_questions (
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

CREATE TABLE IF NOT EXISTS question_progress (
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

CREATE INDEX IF NOT EXISTS idx_session_questions_question
ON practice_session_questions(question_id);

CREATE INDEX IF NOT EXISTS idx_progress_wrong
ON question_progress(profile_id, wrong_count, last_seen_at);
"""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class PracticeStore:
    def __init__(self, database_path: str | Path, catalog: Catalog, profile_id: str = "local"):
        self.database_path = Path(database_path).resolve()
        self.catalog = catalog
        self.profile_id = display_text(profile_id) or "local"

    def initialize(self) -> dict[str, Any]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(PRACTICE_SCHEMA_SQL)
            connection.execute(
                "INSERT OR REPLACE INTO practice_meta(key, value) VALUES ('schema_version', ?)",
                (str(PRACTICE_SCHEMA_VERSION),),
            )
            connection.commit()
        finally:
            connection.close()
        return self.health()

    def connect(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise PracticeUnavailable(f"Practice database is not initialized: {self.database_path}")
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def health(self) -> dict[str, Any]:
        if not self.database_path.is_file():
            return {
                "status": "uninitialized",
                "schema_version": PRACTICE_SCHEMA_VERSION,
            }
        connection = self.connect()
        try:
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            sessions = connection.execute("SELECT COUNT(*) FROM practice_sessions").fetchone()[0]
            attempts = connection.execute(
                "SELECT COALESCE(SUM(attempts), 0) FROM question_progress"
            ).fetchone()[0]
        finally:
            connection.close()
        return {
            "status": "healthy" if integrity == "ok" else "unhealthy",
            "schema_version": PRACTICE_SCHEMA_VERSION,
            "sessions": sessions,
            "attempts": attempts,
        }

    def create_session(
        self,
        *,
        count: int = 10,
        collection_id: str = "",
        filters: dict[str, Any] | None = None,
        mode: str = "practice",
    ) -> dict[str, Any]:
        filters = filters or {}
        questions = self.catalog.sample_questions(
            count=count,
            collection_id=display_text(collection_id),
            canonical_type=filters.get("question_type", ""),
            exam=filters.get("exam", ""),
            collection_kind=filters.get("collection_kind", ""),
            subject=filters.get("subject", ""),
            year=filters.get("year"),
        )
        if not questions:
            raise PracticeNotFound("No questions matched the requested practice scope")
        session_id = f"sess_{secrets.token_hex(16)}"
        connection = self.connect()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO practice_sessions(
                        session_id, profile_id, collection_id, mode,
                        filters_json, total_questions, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        self.profile_id,
                        display_text(collection_id),
                        display_text(mode) or "practice",
                        json.dumps(filters, ensure_ascii=False, sort_keys=True),
                        len(questions),
                        _utc_now(),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO practice_session_questions(session_id, position, question_id)
                    VALUES (?, ?, ?)
                    """,
                    [
                        (session_id, position, question["question_id"])
                        for position, question in enumerate(questions, start=1)
                    ],
                )
        finally:
            connection.close()
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        connection = self.connect()
        try:
            session = connection.execute(
                "SELECT * FROM practice_sessions WHERE session_id = ? AND profile_id = ?",
                (display_text(session_id), self.profile_id),
            ).fetchone()
            if session is None:
                raise PracticeNotFound("Practice session not found")
            rows = connection.execute(
                """
                SELECT position, question_id, submitted_answer, is_correct, answered_at, elapsed_ms
                FROM practice_session_questions
                WHERE session_id = ? ORDER BY position
                """,
                (session["session_id"],),
            ).fetchall()
        finally:
            connection.close()

        questions = self.catalog.get_questions(
            [row["question_id"] for row in rows], include_solution=False
        )
        by_id = {question["question_id"]: question for question in questions}
        items = []
        answered = 0
        correct = 0
        wrong = 0
        for row in rows:
            question = by_id.get(row["question_id"])
            if question is None:
                continue
            item = {"position": row["position"], "question": question, "submission": None}
            if row["answered_at"]:
                answered += 1
                if row["is_correct"] == 1:
                    correct += 1
                elif row["is_correct"] == 0:
                    wrong += 1
                item["submission"] = {
                    "answer": row["submitted_answer"],
                    "is_correct": _nullable_bool(row["is_correct"]),
                    "answered_at": row["answered_at"],
                    "elapsed_ms": row["elapsed_ms"],
                }
            items.append(item)
        return {
            "session_id": session["session_id"],
            "status": session["status"],
            "mode": session["mode"],
            "collection_id": session["collection_id"],
            "filters": json.loads(session["filters_json"]),
            "created_at": session["created_at"],
            "completed_at": session["completed_at"],
            "summary": {
                "total": session["total_questions"],
                "answered": answered,
                "correct": correct,
                "wrong": wrong,
                "ungraded": answered - correct - wrong,
            },
            "items": items,
        }

    def submit_answer(
        self,
        session_id: str,
        *,
        position: int,
        answer: str,
        elapsed_ms: int | None = None,
    ) -> dict[str, Any]:
        session_id = display_text(session_id)
        position = int(position)
        submitted_answer = display_text(answer)
        if not submitted_answer:
            raise ValueError("answer must not be empty")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT ps.status, psq.question_id, psq.answered_at
                FROM practice_sessions ps
                JOIN practice_session_questions psq ON psq.session_id = ps.session_id
                WHERE ps.session_id = ? AND ps.profile_id = ? AND psq.position = ?
                """,
                (session_id, self.profile_id, position),
            ).fetchone()
            if row is None:
                raise PracticeNotFound("Practice question not found")
            if row["answered_at"]:
                raise PracticeConflict("This question has already been answered")
            if row["status"] != "active":
                raise PracticeConflict("Practice session is not active")

            question = self.catalog.get_question(row["question_id"], include_solution=True)
            if question is None:
                raise PracticeNotFound("Catalog question not found")
            question_type = question["question_type"]
            normalized_submitted = normalize_answer(submitted_answer, question_type)
            normalized_correct = normalize_answer(question.get("answer", ""), question_type)
            if question_type in {"单选题", "多选题"}:
                valid_labels = {option["label"] for option in question.get("options", [])}
                if (
                    not normalized_submitted
                    or any(label not in valid_labels for label in normalized_submitted)
                    or (question_type == "单选题" and len(normalized_submitted) != 1)
                ):
                    raise ValueError("answer contains an invalid option label")
            gradable = question_type not in {
                "简答题",
                "名词解释",
                "论述题",
                "知识点",
                "未知题型",
            } and bool(normalized_correct)
            is_correct = normalized_submitted == normalized_correct if gradable else None
            answered_at = _utc_now()
            elapsed_value = None if elapsed_ms is None else max(0, int(elapsed_ms))

            update_cursor = connection.execute(
                """
                UPDATE practice_session_questions
                SET submitted_answer = ?, normalized_submitted_answer = ?,
                    is_correct = ?, answered_at = ?, elapsed_ms = ?
                WHERE session_id = ? AND position = ? AND answered_at IS NULL
                """,
                (
                    submitted_answer,
                    normalized_submitted,
                    None if is_correct is None else int(is_correct),
                    answered_at,
                    elapsed_value,
                    session_id,
                    position,
                ),
            )
            if update_cursor.rowcount != 1:
                raise PracticeConflict("This question has already been answered")
            correct_increment = int(is_correct is True)
            wrong_increment = int(is_correct is False)
            ungraded_increment = int(is_correct is None)
            connection.execute(
                """
                INSERT INTO question_progress(
                    profile_id, question_id, attempts, correct_count,
                    wrong_count, ungraded_count, streak, last_answer,
                    last_result, last_seen_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, question_id) DO UPDATE SET
                    attempts = attempts + 1,
                    correct_count = correct_count + excluded.correct_count,
                    wrong_count = wrong_count + excluded.wrong_count,
                    ungraded_count = ungraded_count + excluded.ungraded_count,
                    streak = CASE
                        WHEN excluded.last_result = 1 THEN streak + 1
                        WHEN excluded.last_result = 0 THEN 0
                        ELSE streak
                    END,
                    last_answer = excluded.last_answer,
                    last_result = excluded.last_result,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    self.profile_id,
                    row["question_id"],
                    correct_increment,
                    wrong_increment,
                    ungraded_increment,
                    correct_increment,
                    submitted_answer,
                    None if is_correct is None else int(is_correct),
                    answered_at,
                ),
            )
            remaining = connection.execute(
                """
                SELECT COUNT(*) FROM practice_session_questions
                WHERE session_id = ? AND answered_at IS NULL
                """,
                (session_id,),
            ).fetchone()[0]
            if remaining == 0:
                connection.execute(
                    """
                    UPDATE practice_sessions
                    SET status = 'complete', completed_at = ?
                    WHERE session_id = ?
                    """,
                    (answered_at, session_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        return {
            "session_id": session_id,
            "position": position,
            "question_id": question["question_id"],
            "submitted_answer": submitted_answer,
            "is_correct": is_correct,
            "correct_answer": question.get("answer", ""),
            "point": question.get("point", ""),
            "explanation": question.get("explanation", ""),
            "answered_at": answered_at,
        }

    def wrong_questions(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                """
                SELECT question_id, attempts, correct_count, wrong_count,
                       streak, last_answer, last_result, last_seen_at
                FROM question_progress
                WHERE profile_id = ? AND wrong_count > 0
                ORDER BY last_seen_at DESC, question_id
                LIMIT ? OFFSET ?
                """,
                (self.profile_id, max(1, min(int(limit), 100)), max(0, int(offset))),
            ).fetchall()
        finally:
            connection.close()
        questions = self.catalog.get_questions(
            [row["question_id"] for row in rows], include_solution=False
        )
        by_id = {question["question_id"]: question for question in questions}
        return [
            {
                "question": by_id[row["question_id"]],
                "progress": {
                    "attempts": row["attempts"],
                    "correct_count": row["correct_count"],
                    "wrong_count": row["wrong_count"],
                    "streak": row["streak"],
                    "last_answer": row["last_answer"],
                    "last_result": _nullable_bool(row["last_result"]),
                    "last_seen_at": row["last_seen_at"],
                },
            }
            for row in rows
            if row["question_id"] in by_id
        ]


def _nullable_bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .catalog import Catalog
from .normalize import display_text, normalize_answer


PRACTICE_SCHEMA_VERSION = 2


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
    occurrence_id INTEGER,
    collection_position INTEGER,
    submitted_answer TEXT,
    normalized_submitted_answer TEXT,
    is_correct INTEGER,
    answered_at TEXT,
    elapsed_ms INTEGER,
    PRIMARY KEY (session_id, position)
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
            self._migrate_session_questions(connection)
            connection.execute(
                "INSERT OR REPLACE INTO practice_meta(key, value) VALUES ('schema_version', ?)",
                (str(PRACTICE_SCHEMA_VERSION),),
            )
            connection.commit()
        finally:
            connection.close()
        return self.health()

    @staticmethod
    def _migrate_session_questions(connection: sqlite3.Connection) -> None:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(practice_session_questions)")
        }
        if "occurrence_id" in columns and "collection_position" in columns:
            return
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            DROP INDEX IF EXISTS idx_session_questions_question;
            ALTER TABLE practice_session_questions RENAME TO practice_session_questions_v1;
            CREATE TABLE practice_session_questions (
                session_id TEXT NOT NULL REFERENCES practice_sessions(session_id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                question_id TEXT NOT NULL,
                occurrence_id INTEGER,
                collection_position INTEGER,
                submitted_answer TEXT,
                normalized_submitted_answer TEXT,
                is_correct INTEGER,
                answered_at TEXT,
                elapsed_ms INTEGER,
                PRIMARY KEY (session_id, position)
            );
            INSERT INTO practice_session_questions(
                session_id, position, question_id, submitted_answer,
                normalized_submitted_answer, is_correct, answered_at, elapsed_ms
            )
            SELECT session_id, position, question_id, submitted_answer,
                   normalized_submitted_answer, is_correct, answered_at, elapsed_ms
            FROM practice_session_questions_v1;
            DROP TABLE practice_session_questions_v1;
            CREATE INDEX idx_session_questions_question
            ON practice_session_questions(question_id);
            COMMIT;
            """
        )
        connection.execute("PRAGMA foreign_keys = ON")

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
        count: int | None = 10,
        collection_id: str = "",
        filters: dict[str, Any] | None = None,
        mode: str = "practice",
        order_mode: str = "sequential",
        start_position: int = 1,
    ) -> dict[str, Any]:
        filters = filters or {}
        collection_id = display_text(collection_id)
        if collection_id:
            selected_items = self.catalog.select_practice_items(
                collection_id=collection_id,
                count=count,
                start_position=start_position,
                order_mode=order_mode,
                canonical_type=filters.get("question_type", ""),
            )
        else:
            question_count = 10 if count in (None, 0, "") else int(count)
            questions = self.catalog.sample_questions(
                count=question_count,
                canonical_type=filters.get("question_type", ""),
                exam=filters.get("exam", ""),
                collection_kind=filters.get("collection_kind", ""),
                subject=filters.get("subject", ""),
                year=filters.get("year"),
            )
            selected_items = [
                {
                    "question_id": question["question_id"],
                    "occurrence_id": None,
                    "collection_position": None,
                }
                for question in questions
            ]
        if not selected_items:
            raise PracticeNotFound("No questions matched the requested practice scope")
        session_filters = {
            **filters,
            "order_mode": order_mode,
            "start_position": max(1, int(start_position)),
        }
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
                        collection_id,
                        display_text(mode) or "practice",
                        json.dumps(session_filters, ensure_ascii=False, sort_keys=True),
                        len(selected_items),
                        _utc_now(),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO practice_session_questions(
                        session_id, position, question_id, occurrence_id, collection_position
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            session_id,
                            position,
                            item["question_id"],
                            item.get("occurrence_id"),
                            item.get("collection_position"),
                        )
                        for position, item in enumerate(selected_items, start=1)
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
                SELECT position, question_id, occurrence_id, collection_position,
                       submitted_answer, is_correct, answered_at, elapsed_ms
                FROM practice_session_questions
                WHERE session_id = ? ORDER BY position
                """,
                (session["session_id"],),
            ).fetchall()
        finally:
            connection.close()

        occurrence_ids = [row["occurrence_id"] for row in rows if row["occurrence_id"] is not None]
        occurrence_questions = self.catalog.get_occurrence_questions(
            occurrence_ids, include_solution=False
        )
        by_occurrence = {
            question["occurrence_id"]: question for question in occurrence_questions
        }
        canonical_ids = list(
            dict.fromkeys(row["question_id"] for row in rows if row["occurrence_id"] is None)
        )
        by_id = {
            question["question_id"]: question
            for question in self.catalog.get_questions(canonical_ids, include_solution=False)
        }
        answered_occurrence_ids = [
            row["occurrence_id"]
            for row in rows
            if row["answered_at"] and row["occurrence_id"] is not None
        ]
        solved_by_occurrence = {
            question["occurrence_id"]: question
            for question in self.catalog.get_occurrence_questions(
                answered_occurrence_ids, include_solution=True
            )
        }
        answered_ids = list(
            dict.fromkeys(
                row["question_id"]
                for row in rows
                if row["answered_at"] and row["occurrence_id"] is None
            )
        )
        solved_by_id = {
            question["question_id"]: question
            for question in self.catalog.get_questions(answered_ids, include_solution=True)
        }
        items = []
        answered = 0
        correct = 0
        wrong = 0
        for row in rows:
            question = (
                by_occurrence.get(row["occurrence_id"])
                if row["occurrence_id"] is not None
                else by_id.get(row["question_id"])
            )
            if question is None:
                continue
            item = {
                "position": row["position"],
                "occurrence_id": row["occurrence_id"],
                "collection_position": row["collection_position"],
                "question": question,
                "submission": None,
            }
            if row["answered_at"]:
                answered += 1
                if row["is_correct"] == 1:
                    correct += 1
                elif row["is_correct"] == 0:
                    wrong += 1
                solved = (
                    solved_by_occurrence.get(row["occurrence_id"], {})
                    if row["occurrence_id"] is not None
                    else solved_by_id.get(row["question_id"], {})
                )
                item["submission"] = {
                    "answer": row["submitted_answer"],
                    "is_correct": _nullable_bool(row["is_correct"]),
                    "answered_at": row["answered_at"],
                    "elapsed_ms": row["elapsed_ms"],
                    "correct_answer": solved.get("answer", ""),
                    "point": solved.get("point", ""),
                    "explanation": solved.get("explanation", ""),
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

    def collection_outline(self, collection_id: str) -> dict[str, Any]:
        collection_id = display_text(collection_id)
        collection = self.catalog.get_collection(collection_id)
        if collection is None:
            raise PracticeNotFound("Collection not found")
        outline = self.catalog.collection_outline(collection_id)
        progress_by_occurrence: dict[int, dict[str, Any]] = {}
        connection = self.connect()
        try:
            for row in connection.execute(
                """
                SELECT psq.occurrence_id, psq.submitted_answer, psq.is_correct,
                       psq.answered_at, psq.rowid AS attempt_rowid
                FROM practice_session_questions psq
                JOIN practice_sessions ps ON ps.session_id = psq.session_id
                WHERE ps.profile_id = ? AND ps.collection_id = ?
                  AND psq.occurrence_id IS NOT NULL AND psq.answered_at IS NOT NULL
                ORDER BY psq.answered_at, attempt_rowid
                """,
                (self.profile_id, collection_id),
            ):
                progress = progress_by_occurrence.setdefault(
                    row["occurrence_id"],
                    {
                        "attempts": 0,
                        "correct_count": 0,
                        "wrong_count": 0,
                        "ungraded_count": 0,
                        "streak": 0,
                    },
                )
                progress["attempts"] += 1
                if row["is_correct"] == 1:
                    progress["correct_count"] += 1
                    progress["streak"] += 1
                elif row["is_correct"] == 0:
                    progress["wrong_count"] += 1
                    progress["streak"] = 0
                else:
                    progress["ungraded_count"] += 1
                progress["last_answer"] = row["submitted_answer"]
                progress["last_result"] = row["is_correct"]
                progress["last_seen_at"] = row["answered_at"]
            active_session = connection.execute(
                """
                SELECT session_id, mode, total_questions, created_at
                FROM practice_sessions
                WHERE profile_id = ? AND collection_id = ? AND status = 'active'
                ORDER BY created_at DESC LIMIT 1
                """,
                (self.profile_id, collection_id),
            ).fetchone()
        finally:
            connection.close()

        summary = {"total": len(outline), "answered": 0, "correct": 0, "wrong": 0, "ungraded": 0}
        items = []
        for item in outline:
            progress = progress_by_occurrence.get(item["occurrence_id"])
            if progress is None:
                status = "unanswered"
                progress_payload = None
            else:
                summary["answered"] += 1
                if progress["last_result"] == 1:
                    status = "correct"
                    summary["correct"] += 1
                elif progress["last_result"] == 0:
                    status = "wrong"
                    summary["wrong"] += 1
                else:
                    status = "ungraded"
                    summary["ungraded"] += 1
                progress_payload = {
                    "attempts": progress["attempts"],
                    "correct_count": progress["correct_count"],
                    "wrong_count": progress["wrong_count"],
                    "ungraded_count": progress["ungraded_count"],
                    "streak": progress["streak"],
                    "last_answer": progress["last_answer"],
                    "last_result": _nullable_bool(progress["last_result"]),
                    "last_seen_at": progress["last_seen_at"],
                }
            items.append({**item, "status": status, "progress": progress_payload})
        return {
            "collection": collection,
            "summary": summary,
            "active_session": dict(active_session) if active_session else None,
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
                SELECT ps.status, psq.question_id, psq.occurrence_id, psq.answered_at
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

            if row["occurrence_id"] is not None:
                occurrence_questions = self.catalog.get_occurrence_questions(
                    [row["occurrence_id"]], include_solution=True
                )
                question = occurrence_questions[0] if occurrence_questions else None
            else:
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
            "occurrence_id": row["occurrence_id"],
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

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .auth import public_avatar_url
from .catalog import Catalog
from .normalize import display_text, normalize_answer


APP_SCHEMA_REVISION = 2
COMMENT_MAX_LENGTH = 2000
NOTE_MAX_LENGTH = 10000


class PracticeUnavailable(RuntimeError):
    pass


class PracticeNotFound(LookupError):
    pass


class PracticeConflict(RuntimeError):
    pass


class PracticeForbidden(PermissionError):
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
    completed_at TEXT,
    duration_ms INTEGER,
    saved_kind TEXT NOT NULL DEFAULT ''
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

CREATE TABLE IF NOT EXISTS collection_question_progress (
    profile_id TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    occurrence_id INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    correct_count INTEGER NOT NULL DEFAULT 0,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    ungraded_count INTEGER NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0,
    last_answer TEXT NOT NULL DEFAULT '',
    last_result INTEGER,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (profile_id, collection_id, occurrence_id)
);

CREATE TABLE IF NOT EXISTS study_items (
    profile_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('favorite', 'wrong')),
    item_key TEXT NOT NULL,
    occurrence_id INTEGER,
    question_id TEXT NOT NULL,
    collection_id TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL DEFAULT '',
    wrong_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (profile_id, kind, item_key)
);

CREATE TABLE IF NOT EXISTS question_comments (
    comment_id TEXT PRIMARY KEY,
    occurrence_id INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS question_notes (
    profile_id TEXT NOT NULL,
    occurrence_id INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (profile_id, occurrence_id)
);

CREATE INDEX IF NOT EXISTS idx_session_questions_question
ON practice_session_questions(question_id);

CREATE INDEX IF NOT EXISTS idx_progress_wrong
ON question_progress(profile_id, wrong_count, last_seen_at);

CREATE INDEX IF NOT EXISTS idx_study_items_library
ON study_items(profile_id, kind, relative_path, occurrence_id);

CREATE INDEX IF NOT EXISTS idx_collection_question_progress
ON collection_question_progress(profile_id, collection_id, last_seen_at);

CREATE INDEX IF NOT EXISTS idx_question_comments_occurrence
ON question_comments(occurrence_id, created_at, comment_id);

CREATE INDEX IF NOT EXISTS idx_question_comments_author
ON question_comments(author_id, updated_at DESC, comment_id);

CREATE INDEX IF NOT EXISTS idx_question_notes_profile
ON question_notes(profile_id, updated_at DESC, occurrence_id);
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
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(PRACTICE_SCHEMA_SQL)
            self._ensure_current_columns(connection)
            self._backfill_collection_progress(connection)
            connection.execute(
                "INSERT OR REPLACE INTO practice_meta(key, value) VALUES ('schema_revision', ?)",
                (str(APP_SCHEMA_REVISION),),
            )
            connection.commit()
        finally:
            connection.close()
        return self.health()

    @staticmethod
    def _ensure_current_columns(connection: sqlite3.Connection) -> None:
        session_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(practice_sessions)")
        }
        for name, declaration in (
            ("duration_ms", "INTEGER"),
            ("saved_kind", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in session_columns:
                connection.execute(
                    f"ALTER TABLE practice_sessions ADD COLUMN {name} {declaration}"
                )
        question_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(practice_session_questions)")
        }
        for name, declaration in (
            ("occurrence_id", "INTEGER"),
            ("collection_position", "INTEGER"),
        ):
            if name not in question_columns:
                connection.execute(
                    f"ALTER TABLE practice_session_questions ADD COLUMN {name} {declaration}"
                )

    @staticmethod
    def _backfill_collection_progress(connection: sqlite3.Connection) -> None:
        initialized = connection.execute(
            "SELECT 1 FROM practice_meta WHERE key = 'collection_progress_initialized'"
        ).fetchone()
        if initialized:
            return
        connection.execute("DELETE FROM collection_question_progress")
        rows = connection.execute(
            """
            SELECT ps.profile_id, ps.collection_id, psq.occurrence_id,
                   psq.submitted_answer, psq.is_correct, psq.answered_at
            FROM practice_session_questions psq
            JOIN practice_sessions ps ON ps.session_id = psq.session_id
            WHERE ps.collection_id != '' AND psq.occurrence_id IS NOT NULL
              AND psq.answered_at IS NOT NULL
            ORDER BY ps.profile_id, ps.collection_id, psq.occurrence_id,
                     psq.answered_at, psq.rowid
            """
        )
        progress: dict[tuple[str, str, int], dict[str, Any]] = {}
        for row in rows:
            key = (row["profile_id"], row["collection_id"], row["occurrence_id"])
            item = progress.setdefault(
                key,
                {
                    "attempts": 0,
                    "correct_count": 0,
                    "wrong_count": 0,
                    "ungraded_count": 0,
                    "streak": 0,
                },
            )
            item["attempts"] += 1
            if row["is_correct"] == 1:
                item["correct_count"] += 1
                item["streak"] += 1
            elif row["is_correct"] == 0:
                item["wrong_count"] += 1
                item["streak"] = 0
            else:
                item["ungraded_count"] += 1
            item["last_answer"] = row["submitted_answer"] or ""
            item["last_result"] = row["is_correct"]
            item["last_seen_at"] = row["answered_at"]
        connection.executemany(
            """
            INSERT INTO collection_question_progress(
                profile_id, collection_id, occurrence_id, attempts,
                correct_count, wrong_count, ungraded_count, streak,
                last_answer, last_result, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    profile_id,
                    collection_id,
                    occurrence_id,
                    item["attempts"],
                    item["correct_count"],
                    item["wrong_count"],
                    item["ungraded_count"],
                    item["streak"],
                    item["last_answer"],
                    item["last_result"],
                    item["last_seen_at"],
                )
                for (profile_id, collection_id, occurrence_id), item in progress.items()
            ],
        )
        connection.execute(
            "INSERT INTO practice_meta(key, value) VALUES ('collection_progress_initialized', 'true')"
        )

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
                "schema_revision": APP_SCHEMA_REVISION,
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
            "schema_revision": APP_SCHEMA_REVISION,
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
        saved_kind: str = "",
    ) -> dict[str, Any]:
        filters = filters or {}
        collection_id = display_text(collection_id)
        mode = display_text(mode) or "practice"
        if mode not in {"practice", "speed", "test", "memorize"}:
            raise ValueError("mode must be practice, speed, test, or memorize")
        saved_kind = display_text(saved_kind).lower()
        if saved_kind not in {"", "favorite", "wrong"}:
            raise ValueError("saved_kind must be favorite or wrong")
        if saved_kind:
            selected_items = self._select_saved_items(
                saved_kind,
                collection_id=collection_id,
                count=count,
                start_position=start_position,
                canonical_type=filters.get("question_type", ""),
            )
        elif collection_id:
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
                        filters_json, total_questions, created_at, saved_kind
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        self.profile_id,
                        collection_id,
                        mode,
                        json.dumps(session_filters, ensure_ascii=False, sort_keys=True),
                        len(selected_items),
                        _utc_now(),
                        saved_kind,
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

        reveal_all = session["mode"] == "memorize" or session["status"] == "complete"
        reveal_answer_feedback = session["mode"] != "test" or session["status"] == "complete"
        occurrence_ids = [row["occurrence_id"] for row in rows if row["occurrence_id"] is not None]
        occurrence_questions = self.catalog.get_occurrence_questions(
            occurrence_ids, include_solution=reveal_all
        )
        by_occurrence = {
            question["occurrence_id"]: question for question in occurrence_questions
        }
        canonical_ids = list(
            dict.fromkeys(row["question_id"] for row in rows if row["occurrence_id"] is None)
        )
        by_id = {
            question["question_id"]: question
            for question in self.catalog.get_questions(
                canonical_ids, include_solution=reveal_all
            )
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
        } if reveal_answer_feedback else {}
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
        } if reveal_answer_feedback else {}
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
                    "is_correct": _nullable_bool(row["is_correct"])
                    if reveal_answer_feedback
                    else None,
                    "answered_at": row["answered_at"],
                    "elapsed_ms": row["elapsed_ms"],
                    "correct_answer": solved.get("answer", "")
                    if reveal_answer_feedback
                    else "",
                    "point": solved.get("point", "") if reveal_answer_feedback else "",
                    "explanation": solved.get("explanation", "")
                    if reveal_answer_feedback
                    else "",
                }
            items.append(item)
        summary_correct = correct if reveal_answer_feedback else None
        summary_wrong = wrong if reveal_answer_feedback else None
        summary_ungraded = (answered - correct - wrong) if reveal_answer_feedback else None
        return {
            "session_id": session["session_id"],
            "status": session["status"],
            "mode": session["mode"],
            "collection_id": session["collection_id"],
            "saved_kind": session["saved_kind"],
            "filters": json.loads(session["filters_json"]),
            "created_at": session["created_at"],
            "completed_at": session["completed_at"],
            "duration_ms": session["duration_ms"],
            "solution_visible": reveal_all,
            "summary": {
                "total": session["total_questions"],
                "answered": answered,
                "correct": summary_correct,
                "wrong": summary_wrong,
                "ungraded": summary_ungraded,
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
                SELECT occurrence_id, attempts, correct_count, wrong_count,
                       ungraded_count, streak, last_answer, last_result, last_seen_at
                FROM collection_question_progress
                WHERE profile_id = ? AND collection_id = ?
                """,
                (self.profile_id, collection_id),
            ):
                progress_by_occurrence[row["occurrence_id"]] = dict(row)
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

    def reset_collection_progress(self, collection_id: str) -> dict[str, Any]:
        collection_id = display_text(collection_id)
        collection = self.catalog.get_collection(collection_id)
        if collection is None:
            raise PracticeNotFound("Collection not found")
        connection = self.connect()
        try:
            with connection:
                cursor = connection.execute(
                    """
                    DELETE FROM collection_question_progress
                    WHERE profile_id = ? AND collection_id = ?
                    """,
                    (self.profile_id, collection_id),
                )
        finally:
            connection.close()
        total = len(self.catalog.collection_outline(collection_id))
        return {
            "collection_id": collection_id,
            "reset_count": cursor.rowcount,
            "summary": {
                "total": total,
                "answered": 0,
                "correct": 0,
                "wrong": 0,
                "ungraded": 0,
            },
        }

    def list_comments(
        self, occurrence_id: int, *, viewer_is_admin: bool = False
    ) -> dict[str, Any]:
        question = self._resolve_occurrence_question(occurrence_id)
        connection = self.connect()
        try:
            rows = connection.execute(
                """
                SELECT comment_id, occurrence_id, question_id, author_id,
                       author_name, body, created_at, updated_at
                FROM question_comments
                WHERE occurrence_id = ?
                ORDER BY created_at, comment_id
                LIMIT 200
                """,
                (int(occurrence_id),),
            ).fetchall()
        finally:
            connection.close()
        items = self._decorate_comments(rows, viewer_is_admin=viewer_is_admin)
        return {
            "occurrence_id": int(occurrence_id),
            "question_id": question["question_id"],
            "count": len(items),
            "items": items,
        }

    def create_comment(
        self, occurrence_id: int, *, author_name: str, body: object
    ) -> dict[str, Any]:
        question = self._resolve_occurrence_question(occurrence_id)
        normalized_body = _comment_body(body)
        now = _utc_now()
        item = {
            "comment_id": f"cmt_{secrets.token_hex(12)}",
            "occurrence_id": int(occurrence_id),
            "question_id": question["question_id"],
            "author_id": self.profile_id,
            "author_name": display_text(author_name) or "用户",
            "body": normalized_body,
            "created_at": now,
            "updated_at": now,
            "can_delete": True,
        }
        connection = self.connect()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO question_comments(
                        comment_id, occurrence_id, question_id, author_id,
                        author_name, body, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["comment_id"],
                        item["occurrence_id"],
                        item["question_id"],
                        item["author_id"],
                        item["author_name"],
                        item["body"],
                        item["created_at"],
                        item["updated_at"],
                    ),
                )
        finally:
            connection.close()
        return item

    def delete_comment(self, comment_id: str, *, is_admin: bool = False) -> bool:
        comment_id = display_text(comment_id)
        if not comment_id:
            raise PracticeNotFound("Comment not found")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT author_id FROM question_comments WHERE comment_id = ?",
                (comment_id,),
            ).fetchone()
            if row is None:
                raise PracticeNotFound("Comment not found")
            if row["author_id"] != self.profile_id and not is_admin:
                raise PracticeForbidden("You cannot delete this comment")
            cursor = connection.execute(
                "DELETE FROM question_comments WHERE comment_id = ?", (comment_id,)
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return cursor.rowcount == 1

    def update_comment(self, comment_id: str, body: object) -> dict[str, Any]:
        comment_id = display_text(comment_id)
        normalized_body = _comment_body(body)
        if not comment_id:
            raise PracticeNotFound("Comment not found")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM question_comments WHERE comment_id = ?",
                (comment_id,),
            ).fetchone()
            if row is None:
                raise PracticeNotFound("Comment not found")
            if row["author_id"] != self.profile_id:
                raise PracticeForbidden("You cannot edit this comment")
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "UPDATE question_comments SET body = ?, updated_at = ? WHERE comment_id = ?",
                (normalized_body, now, comment_id),
            )
            connection.commit()
            updated = connection.execute(
                "SELECT * FROM question_comments WHERE comment_id = ?",
                (comment_id,),
            ).fetchone()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self._decorate_comments([updated], viewer_is_admin=False)[0]

    def my_comments(
        self, *, query: str = "", limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        return self._comment_feed(
            author_id=self.profile_id,
            query=query,
            limit=limit,
            offset=offset,
            viewer_is_admin=False,
        )

    def all_comments(
        self, *, query: str = "", limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        return self._comment_feed(
            author_id="",
            query=query,
            limit=limit,
            offset=offset,
            viewer_is_admin=True,
        )

    def _comment_feed(
        self,
        *,
        author_id: str,
        query: str,
        limit: int,
        offset: int,
        viewer_is_admin: bool,
    ) -> dict[str, Any]:
        normalized_query = display_text(query)
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        clauses: list[str] = []
        parameters: list[Any] = []
        if author_id:
            clauses.append("author_id = ?")
            parameters.append(author_id)
        if normalized_query:
            clauses.append("(body LIKE ? OR author_name LIKE ?)")
            like = f"%{normalized_query}%"
            parameters.extend((like, like))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self.connect()
        try:
            total = connection.execute(
                f"SELECT COUNT(*) FROM question_comments {where}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT comment_id, occurrence_id, question_id, author_id,
                       author_name, body, created_at, updated_at
                FROM question_comments {where}
                ORDER BY updated_at DESC, comment_id DESC
                LIMIT ? OFFSET ?
                """,
                (*parameters, limit, offset),
            ).fetchall()
        finally:
            connection.close()
        return {
            "count": len(rows),
            "total": int(total),
            "offset": offset,
            "query": normalized_query,
            "items": self._decorate_comments(
                rows, viewer_is_admin=viewer_is_admin, include_question=True
            ),
        }

    def _decorate_comments(
        self,
        rows: list[sqlite3.Row],
        *,
        viewer_is_admin: bool,
        include_question: bool = False,
    ) -> list[dict[str, Any]]:
        author_ids = sorted({row["author_id"] for row in rows})
        avatar_by_author: dict[str, dict[str, str]] = {}
        if author_ids:
            connection = self.connect()
            try:
                has_users = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
                ).fetchone()
                if has_users:
                    placeholders = ",".join("?" for _ in author_ids)
                    for user in connection.execute(
                        f"SELECT user_id, avatar, updated_at FROM users WHERE user_id IN ({placeholders})",
                        author_ids,
                    ):
                        avatar_by_author[user["user_id"]] = {
                            "avatar": user["avatar"],
                            "avatar_url": public_avatar_url(
                                user["user_id"], user["avatar"], user["updated_at"]
                            ),
                        }
            finally:
                connection.close()
        questions: dict[int, dict[str, Any]] = {}
        if include_question and rows:
            occurrence_ids = sorted({int(row["occurrence_id"]) for row in rows})
            questions = {
                int(question["occurrence_id"]): question
                for question in self.catalog.get_occurrence_questions(
                    occurrence_ids, include_solution=False
                )
            }
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.update(avatar_by_author.get(row["author_id"], {}))
            item["can_edit"] = row["author_id"] == self.profile_id
            item["can_delete"] = bool(
                viewer_is_admin or row["author_id"] == self.profile_id
            )
            item["edited"] = row["updated_at"] != row["created_at"]
            if include_question:
                item["question"] = _question_reference(
                    questions.get(int(row["occurrence_id"]))
                )
            items.append(item)
        return items

    def get_note(self, occurrence_id: int) -> dict[str, Any]:
        question = self._resolve_occurrence_question(occurrence_id)
        connection = self.connect()
        try:
            row = connection.execute(
                """
                SELECT profile_id, occurrence_id, question_id, body, created_at, updated_at
                FROM question_notes WHERE profile_id = ? AND occurrence_id = ?
                """,
                (self.profile_id, int(occurrence_id)),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return {
                "occurrence_id": int(occurrence_id),
                "question_id": question["question_id"],
                "body": "",
                "exists": False,
            }
        return {**dict(row), "exists": True}

    def save_note(self, occurrence_id: int, body: object) -> dict[str, Any]:
        question = self._resolve_occurrence_question(occurrence_id)
        normalized_body = _note_body(body)
        now = _utc_now()
        connection = self.connect()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO question_notes(
                        profile_id, occurrence_id, question_id, body, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(profile_id, occurrence_id) DO UPDATE SET
                        question_id = excluded.question_id,
                        body = excluded.body,
                        updated_at = excluded.updated_at
                    """,
                    (
                        self.profile_id,
                        int(occurrence_id),
                        question["question_id"],
                        normalized_body,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT profile_id, occurrence_id, question_id, body, created_at, updated_at
                    FROM question_notes WHERE profile_id = ? AND occurrence_id = ?
                    """,
                    (self.profile_id, int(occurrence_id)),
                ).fetchone()
        finally:
            connection.close()
        return {**dict(row), "exists": True}

    def delete_note(self, occurrence_id: int) -> bool:
        self._resolve_occurrence_question(occurrence_id)
        connection = self.connect()
        try:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM question_notes WHERE profile_id = ? AND occurrence_id = ?",
                    (self.profile_id, int(occurrence_id)),
                )
        finally:
            connection.close()
        return cursor.rowcount == 1

    def my_notes(
        self, *, query: str = "", limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        normalized_query = display_text(query)
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        where = "WHERE profile_id = ?"
        parameters: list[Any] = [self.profile_id]
        if normalized_query:
            where += " AND body LIKE ?"
            parameters.append(f"%{normalized_query}%")
        connection = self.connect()
        try:
            total = connection.execute(
                f"SELECT COUNT(*) FROM question_notes {where}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT occurrence_id, question_id, body, created_at, updated_at
                FROM question_notes {where}
                ORDER BY updated_at DESC, occurrence_id
                LIMIT ? OFFSET ?
                """,
                (*parameters, limit, offset),
            ).fetchall()
        finally:
            connection.close()
        occurrence_ids = [int(row["occurrence_id"]) for row in rows]
        questions = {
            int(question["occurrence_id"]): question
            for question in self.catalog.get_occurrence_questions(
                occurrence_ids, include_solution=False
            )
        }
        items = [
            {
                **dict(row),
                "question": _question_reference(
                    questions.get(int(row["occurrence_id"]))
                ),
            }
            for row in rows
        ]
        return {"count": len(items), "total": int(total), "offset": offset, "items": items}

    def _resolve_occurrence_question(self, occurrence_id: int) -> dict[str, Any]:
        try:
            normalized_id = int(occurrence_id)
        except (TypeError, ValueError) as error:
            raise ValueError("occurrence_id must be an integer") from error
        questions = self.catalog.get_occurrence_questions(
            [normalized_id], include_solution=False
        )
        if not questions:
            raise PracticeNotFound("Question occurrence not found")
        return questions[0]

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
                SELECT ps.status, ps.mode, ps.collection_id,
                       psq.question_id, psq.occurrence_id, psq.answered_at
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
            if is_correct is False:
                source = (question.get("sources") or [{}])[0]
                occurrence_id = row["occurrence_id"] or source.get("occurrence_id")
                item_key = (
                    f"occurrence:{occurrence_id}"
                    if occurrence_id is not None
                    else f"question:{row['question_id']}"
                )
                connection.execute(
                    """
                    INSERT INTO study_items(
                        profile_id, kind, item_key, occurrence_id, question_id,
                        collection_id, relative_path, wrong_count, created_at, updated_at
                    ) VALUES (?, 'wrong', ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(profile_id, kind, item_key) DO UPDATE SET
                        wrong_count = study_items.wrong_count + 1,
                        updated_at = excluded.updated_at,
                        collection_id = excluded.collection_id,
                        relative_path = excluded.relative_path
                    """,
                    (
                        self.profile_id,
                        item_key,
                        occurrence_id,
                        row["question_id"],
                        source.get("collection_id") or "",
                        source.get("relative_path") or "",
                        answered_at,
                        answered_at,
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
            if row["collection_id"] and row["occurrence_id"] is not None:
                connection.execute(
                    """
                    INSERT INTO collection_question_progress(
                        profile_id, collection_id, occurrence_id, attempts,
                        correct_count, wrong_count, ungraded_count, streak,
                        last_answer, last_result, last_seen_at
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(profile_id, collection_id, occurrence_id) DO UPDATE SET
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
                        row["collection_id"],
                        row["occurrence_id"],
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
                    SET status = 'complete', completed_at = ?,
                        duration_ms = MAX(0, CAST(
                            (julianday(?) - julianday(created_at)) * 86400000 AS INTEGER
                        ))
                    WHERE session_id = ?
                    """,
                    (answered_at, answered_at, session_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        result_visible = row["mode"] != "test" or remaining == 0
        response = {
            "session_id": session_id,
            "position": position,
            "question_id": question["question_id"],
            "occurrence_id": row["occurrence_id"],
            "submitted_answer": submitted_answer,
            "answered_at": answered_at,
        }
        if result_visible:
            response.update(
                {
                    "is_correct": is_correct,
                    "correct_answer": question.get("answer", ""),
                    "point": question.get("point", ""),
                    "explanation": question.get("explanation", ""),
                }
            )
        return response

    def finish_session(self, session_id: str) -> dict[str, Any]:
        session_id = display_text(session_id)
        now = datetime.now(UTC)
        connection = self.connect()
        try:
            with connection:
                row = connection.execute(
                    """
                    SELECT created_at, status
                    FROM practice_sessions
                    WHERE session_id = ? AND profile_id = ?
                    """,
                    (session_id, self.profile_id),
                ).fetchone()
                if row is None:
                    raise PracticeNotFound("Practice session not found")
                if row["status"] == "active":
                    created_at = datetime.fromisoformat(row["created_at"])
                    duration_ms = max(0, int((now - created_at).total_seconds() * 1000))
                    connection.execute(
                        """
                        UPDATE practice_sessions
                        SET status = 'complete', completed_at = ?, duration_ms = ?
                        WHERE session_id = ? AND profile_id = ?
                        """,
                        (
                            now.replace(microsecond=0).isoformat(),
                            duration_ms,
                            session_id,
                            self.profile_id,
                        ),
                    )
        finally:
            connection.close()
        return self.get_session(session_id)

    def _select_saved_items(
        self,
        kind: str,
        *,
        collection_id: str = "",
        count: int | None = None,
        start_position: int = 1,
        canonical_type: str = "",
    ) -> list[dict[str, Any]]:
        limit = 5000 if count in (None, 0, "") else max(1, min(int(count), 5000))
        connection = self.connect()
        try:
            clauses = ["profile_id = ?", "kind = ?"]
            params: list[Any] = [self.profile_id, kind]
            if collection_id:
                clauses.append("collection_id = ?")
                params.append(collection_id)
            rows = connection.execute(
                f"""
                SELECT item_key, occurrence_id, question_id, collection_id
                FROM study_items
                WHERE {' AND '.join(clauses)}
                ORDER BY relative_path, occurrence_id, question_id
                """,
                params,
            ).fetchall()
        finally:
            connection.close()
        by_occurrence = {
            row["occurrence_id"]: dict(row)
            for row in rows
            if row["occurrence_id"] is not None
        }
        selected: list[dict[str, Any]] = []
        if collection_id:
            for item in self.catalog.collection_outline(collection_id):
                if item["collection_position"] < max(1, int(start_position)):
                    continue
                saved = by_occurrence.get(item["occurrence_id"])
                if not saved:
                    continue
                if canonical_type and item["question_type"] != canonical_type:
                    continue
                selected.append(
                    {
                        "question_id": item["question_id"],
                        "occurrence_id": item["occurrence_id"],
                        "collection_position": item["collection_position"],
                    }
                )
                if len(selected) >= limit:
                    break
            return selected
        for row in rows:
            selected.append(
                {
                    "question_id": row["question_id"],
                    "occurrence_id": row["occurrence_id"],
                    "collection_position": None,
                }
            )
            if len(selected) >= limit:
                break
        return selected

    def add_favorite(
        self, *, occurrence_id: int | None = None, question_id: str = ""
    ) -> dict[str, Any]:
        question = self._resolve_favorite_question(
            occurrence_id=occurrence_id, question_id=question_id
        )
        source = (question.get("sources") or [{}])[0]
        resolved_occurrence = question.get("occurrence_id") or source.get("occurrence_id")
        if resolved_occurrence is None:
            raise PracticeNotFound("Question source occurrence not found")
        return self._save_item(
            "favorite",
            occurrence_id=int(resolved_occurrence),
            question_id=question["question_id"],
            collection_id=source.get("collection_id") or "",
            relative_path=source.get("relative_path") or "",
        )

    def toggle_favorite(
        self, *, occurrence_id: int | None = None, question_id: str = ""
    ) -> dict[str, Any]:
        question = self._resolve_favorite_question(
            occurrence_id=occurrence_id, question_id=question_id
        )
        source = (question.get("sources") or [{}])[0]
        resolved_occurrence = question.get("occurrence_id") or source.get("occurrence_id")
        if resolved_occurrence is None:
            raise PracticeNotFound("Question source occurrence not found")
        item_key = f"occurrence:{int(resolved_occurrence)}"
        now = _utc_now()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT 1 FROM study_items
                WHERE profile_id = ? AND kind = 'favorite' AND item_key = ?
                """,
                (self.profile_id, item_key),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    DELETE FROM study_items
                    WHERE profile_id = ? AND kind = 'favorite' AND item_key = ?
                    """,
                    (self.profile_id, item_key),
                )
                connection.commit()
                return {
                    "kind": "favorite",
                    "item_key": item_key,
                    "occurrence_id": int(resolved_occurrence),
                    "favorited": False,
                }
            connection.execute(
                """
                INSERT INTO study_items(
                    profile_id, kind, item_key, occurrence_id, question_id,
                    collection_id, relative_path, wrong_count, created_at, updated_at
                ) VALUES (?, 'favorite', ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    self.profile_id,
                    item_key,
                    int(resolved_occurrence),
                    question["question_id"],
                    source.get("collection_id") or "",
                    source.get("relative_path") or "",
                    now,
                    now,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return {
            "kind": "favorite",
            "item_key": item_key,
            "occurrence_id": int(resolved_occurrence),
            "question_id": question["question_id"],
            "collection_id": source.get("collection_id") or "",
            "relative_path": source.get("relative_path") or "",
            "updated_at": now,
            "favorited": True,
        }

    def favorite_occurrence_ids(self, occurrence_ids: list[int]) -> list[int]:
        unique_ids = sorted({int(value) for value in occurrence_ids})
        if not unique_ids:
            return []
        if len(unique_ids) > 200:
            raise ValueError("at most 200 occurrence ids are allowed")
        placeholders = ",".join("?" for _ in unique_ids)
        connection = self.connect()
        try:
            rows = connection.execute(
                f"""
                SELECT occurrence_id FROM study_items
                WHERE profile_id = ? AND kind = 'favorite'
                  AND occurrence_id IN ({placeholders})
                """,
                (self.profile_id, *unique_ids),
            ).fetchall()
        finally:
            connection.close()
        return sorted(int(row["occurrence_id"]) for row in rows)

    def _resolve_favorite_question(
        self, *, occurrence_id: int | None = None, question_id: str = ""
    ) -> dict[str, Any]:
        question: dict[str, Any] | None = None
        if occurrence_id is not None:
            rows = self.catalog.get_occurrence_questions(
                [int(occurrence_id)], include_solution=False
            )
            question = rows[0] if rows else None
        elif display_text(question_id):
            question = self.catalog.get_question(question_id, include_solution=False)
        if question is None:
            raise PracticeNotFound("Question not found")
        return question

    def _save_item(
        self,
        kind: str,
        *,
        occurrence_id: int,
        question_id: str,
        collection_id: str,
        relative_path: str,
    ) -> dict[str, Any]:
        if kind not in {"favorite", "wrong"}:
            raise ValueError("kind must be favorite or wrong")
        item_key = f"occurrence:{int(occurrence_id)}"
        now = _utc_now()
        connection = self.connect()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO study_items(
                        profile_id, kind, item_key, occurrence_id, question_id,
                        collection_id, relative_path, wrong_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    ON CONFLICT(profile_id, kind, item_key) DO UPDATE SET
                        updated_at = excluded.updated_at,
                        collection_id = excluded.collection_id,
                        relative_path = excluded.relative_path
                    """,
                    (
                        self.profile_id,
                        kind,
                        item_key,
                        int(occurrence_id),
                        question_id,
                        collection_id,
                        relative_path,
                        now,
                        now,
                    ),
                )
        finally:
            connection.close()
        return {
            "kind": kind,
            "item_key": item_key,
            "occurrence_id": int(occurrence_id),
            "question_id": question_id,
            "collection_id": collection_id,
            "relative_path": relative_path,
            "updated_at": now,
        }

    def remove_saved_item(self, kind: str, item_key: str) -> bool:
        if kind not in {"favorite", "wrong"}:
            raise ValueError("kind must be favorite or wrong")
        connection = self.connect()
        try:
            with connection:
                cursor = connection.execute(
                    """
                    DELETE FROM study_items
                    WHERE profile_id = ? AND kind = ? AND item_key = ?
                    """,
                    (self.profile_id, kind, display_text(item_key)),
                )
        finally:
            connection.close()
        return cursor.rowcount == 1

    def saved_library(self, kind: str, parent: str = "") -> dict[str, Any]:
        if kind not in {"favorite", "wrong"}:
            raise ValueError("kind must be favorite or wrong")
        connection = self.connect()
        try:
            rows = connection.execute(
                """
                SELECT collection_id, COUNT(*) AS item_count
                FROM study_items
                WHERE profile_id = ? AND kind = ? AND collection_id != ''
                GROUP BY collection_id
                """,
                (self.profile_id, kind),
            ).fetchall()
        finally:
            connection.close()
        counts = {row["collection_id"]: row["item_count"] for row in rows}
        payload = self.catalog.browse_library(
            parent,
            collection_ids=set(counts),
            collection_counts=counts,
        )
        payload["kind"] = kind
        payload["total_saved"] = sum(counts.values())
        return payload

    def saved_outline(self, kind: str, collection_id: str) -> dict[str, Any]:
        base = self.collection_outline(collection_id)
        connection = self.connect()
        try:
            rows = connection.execute(
                """
                SELECT item_key, occurrence_id, wrong_count, created_at, updated_at
                FROM study_items
                WHERE profile_id = ? AND kind = ? AND collection_id = ?
                """,
                (self.profile_id, kind, collection_id),
            ).fetchall()
        finally:
            connection.close()
        by_occurrence = {row["occurrence_id"]: dict(row) for row in rows}
        items = []
        for item in base["items"]:
            saved = by_occurrence.get(item["occurrence_id"])
            if saved:
                items.append({**item, "saved": saved})
        base["items"] = items
        base["summary"] = {
            **base["summary"],
            "total": len(items),
            "saved": len(items),
        }
        base["kind"] = kind
        return base

    def dashboard_stats(self) -> dict[str, int]:
        connection = self.connect()
        try:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM practice_sessions) AS sessions,
                    (SELECT COUNT(*) FROM practice_sessions WHERE status = 'active') AS active_practice,
                    (SELECT COUNT(*) FROM practice_session_questions WHERE answered_at IS NOT NULL) AS answers,
                    (SELECT COUNT(*) FROM study_items WHERE kind = 'favorite') AS favorites,
                    (SELECT COUNT(*) FROM study_items WHERE kind = 'wrong') AS wrong_items,
                    (SELECT COUNT(*) FROM question_comments) AS comments,
                    (SELECT COUNT(*) FROM question_notes) AS notes
                """
            ).fetchone()
        finally:
            connection.close()
        return dict(row)

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


def _comment_body(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("comment body must be a string")
    body = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        raise ValueError("comment body must not be empty")
    if len(body) > COMMENT_MAX_LENGTH:
        raise ValueError(f"comment body must not exceed {COMMENT_MAX_LENGTH} characters")
    return body


def _note_body(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("note body must be a string")
    body = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        raise ValueError("note body must not be empty")
    if len(body) > NOTE_MAX_LENGTH:
        raise ValueError(f"note body must not exceed {NOTE_MAX_LENGTH} characters")
    return body


def _question_reference(question: dict[str, Any] | None) -> dict[str, Any] | None:
    if not question:
        return None
    source = (question.get("sources") or [{}])[0]
    return {
        "question_id": question.get("question_id", ""),
        "occurrence_id": question.get("occurrence_id"),
        "type_label": question.get("type_label", ""),
        "source_code": question.get("source_code", ""),
        "stem": question.get("stem", ""),
        "collection_id": source.get("collection_id", ""),
        "collection_position": source.get("collection_position") or source.get("position"),
        "relative_path": source.get("relative_path", ""),
    }

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .auth import profile_privacy_preferences, public_avatar_url
from .catalog import Catalog
from .normalize import display_text, normalize_answer


APP_SCHEMA_REVISION = 6
COMMENT_MAX_LENGTH = 2000
NOTE_MAX_LENGTH = 10000
STUDY_TIME_ZONE_NAME = "Asia/Shanghai"
STUDY_TIME_ZONE = timezone(timedelta(hours=8), STUDY_TIME_ZONE_NAME)
DEFAULT_DAILY_QUESTION_TARGET = 20
DEFAULT_DAILY_DURATION_TARGET_MINUTES = 30


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

CREATE TABLE IF NOT EXISTS study_goals (
    profile_id TEXT PRIMARY KEY,
    daily_question_target INTEGER NOT NULL DEFAULT 20
        CHECK (daily_question_target BETWEEN 1 AND 1000),
    daily_duration_target_minutes INTEGER NOT NULL DEFAULT 30
        CHECK (daily_duration_target_minutes BETWEEN 1 AND 1440),
    updated_at TEXT NOT NULL
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
    saved_kind TEXT NOT NULL DEFAULT '',
    archived_at TEXT
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
    parent_comment_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comment_reactions (
    comment_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    reaction TEXT NOT NULL CHECK (reaction IN ('agree', 'disagree')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (comment_id, profile_id)
);

CREATE TABLE IF NOT EXISTS user_follows (
    follower_id TEXT NOT NULL,
    following_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (follower_id, following_id)
);

CREATE TABLE IF NOT EXISTS direct_messages (
    message_id TEXT PRIMARY KEY,
    sender_id TEXT NOT NULL,
    receiver_id TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT '',
    comment_id TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    read_at TEXT
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

CREATE INDEX IF NOT EXISTS idx_session_questions_answered
ON practice_session_questions(answered_at, session_id);

CREATE INDEX IF NOT EXISTS idx_practice_sessions_profile
ON practice_sessions(profile_id, created_at DESC);

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

CREATE INDEX IF NOT EXISTS idx_comment_reactions_comment
ON comment_reactions(comment_id, reaction);

CREATE INDEX IF NOT EXISTS idx_user_follows_following
ON user_follows(following_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_direct_messages_receiver
ON direct_messages(receiver_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_notifications_profile
ON notifications(profile_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_question_notes_profile
ON question_notes(profile_id, updated_at DESC, occurrence_id);
"""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _study_day_bounds(target_day: date | None = None) -> tuple[date, str, str]:
    local_day = target_day or datetime.now(STUDY_TIME_ZONE).date()
    start_local = datetime.combine(local_day, time.min, tzinfo=STUDY_TIME_ZONE)
    end_local = start_local + timedelta(days=1)
    return (
        local_day,
        start_local.astimezone(UTC).isoformat(),
        end_local.astimezone(UTC).isoformat(),
    )


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
            ("archived_at", "TEXT"),
        ):
            if name not in session_columns:
                connection.execute(
                    f"ALTER TABLE practice_sessions ADD COLUMN {name} {declaration}"
                )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_practice_sessions_history
            ON practice_sessions(profile_id, archived_at, status, created_at DESC)
            """
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
        comment_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(question_comments)")
        }
        if "parent_comment_id" not in comment_columns:
            connection.execute(
                "ALTER TABLE question_comments ADD COLUMN parent_comment_id TEXT"
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
            "archived_at": session["archived_at"],
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

    def session_history(
        self,
        *,
        status: object = "",
        limit: object = 20,
        offset: object = 0,
    ) -> dict[str, Any]:
        status_value = display_text(status).lower()
        if status_value and status_value not in {"active", "complete", "archived"}:
            raise ValueError("练习记录状态无效")
        try:
            limit_value = max(1, min(int(limit), 100))
            offset_value = max(0, int(offset))
        except (TypeError, ValueError) as error:
            raise ValueError("练习记录分页参数无效") from error
        conditions = ["s.profile_id = ?"]
        parameters: list[object] = [self.profile_id]
        if status_value == "archived":
            conditions.append("s.archived_at IS NOT NULL")
        else:
            conditions.append("s.archived_at IS NULL")
        if status_value in {"active", "complete"}:
            conditions.append("s.status = ?")
            parameters.append(status_value)
        where = " AND ".join(conditions)
        connection = self.connect()
        try:
            total = connection.execute(
                f"SELECT COUNT(*) FROM practice_sessions s WHERE {where}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT s.session_id, s.collection_id, s.mode, s.filters_json,
                       s.total_questions, s.status, s.created_at, s.completed_at,
                       s.duration_ms, s.saved_kind, s.archived_at,
                       SUM(CASE WHEN q.answered_at IS NOT NULL THEN 1 ELSE 0 END) AS answered,
                       SUM(CASE WHEN q.is_correct = 1 THEN 1 ELSE 0 END) AS correct,
                       SUM(CASE WHEN q.is_correct = 0 THEN 1 ELSE 0 END) AS wrong,
                       SUM(CASE WHEN q.answered_at IS NOT NULL AND q.is_correct IS NULL THEN 1 ELSE 0 END) AS ungraded,
                       COALESCE(s.archived_at, s.completed_at, MAX(q.answered_at), s.created_at) AS last_activity_at
                FROM practice_sessions s
                LEFT JOIN practice_session_questions q ON q.session_id = s.session_id
                WHERE {where}
                GROUP BY s.session_id
                ORDER BY last_activity_at DESC, s.rowid DESC
                LIMIT ? OFFSET ?
                """,
                (*parameters, limit_value, offset_value),
            ).fetchall()
            stats_row = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN archived_at IS NULL THEN 1 ELSE 0 END) AS total,
                    SUM(CASE WHEN archived_at IS NULL AND status = 'active' THEN 1 ELSE 0 END) AS active,
                    SUM(CASE WHEN archived_at IS NULL AND status = 'complete' THEN 1 ELSE 0 END) AS complete,
                    SUM(CASE WHEN archived_at IS NOT NULL THEN 1 ELSE 0 END) AS archived
                FROM practice_sessions WHERE profile_id = ?
                """,
                (self.profile_id,),
            ).fetchone()
        finally:
            connection.close()
        collection_ids = list(
            dict.fromkeys(row["collection_id"] for row in rows if row["collection_id"])
        )
        collections = {
            collection_id: self.catalog.get_collection(collection_id)
            for collection_id in collection_ids
        }
        items = []
        for row in rows:
            try:
                filters = json.loads(row["filters_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                filters = {}
            if not isinstance(filters, dict):
                filters = {}
            collection = collections.get(row["collection_id"])
            answered = int(row["answered"] or 0)
            correct = int(row["correct"] or 0)
            wrong = int(row["wrong"] or 0)
            ungraded = int(row["ungraded"] or 0)
            reveal_results = row["mode"] != "test" or row["status"] == "complete"
            graded = correct + wrong
            items.append(
                {
                    "session_id": row["session_id"],
                    "title": self._session_history_title(row, collection, filters),
                    "collection_id": row["collection_id"],
                    "relative_path": collection.get("relative_path", "") if collection else "",
                    "mode": row["mode"],
                    "status": row["status"],
                    "saved_kind": row["saved_kind"],
                    "created_at": row["created_at"],
                    "completed_at": row["completed_at"],
                    "archived_at": row["archived_at"],
                    "last_activity_at": row["last_activity_at"] or row["created_at"],
                    "duration_ms": row["duration_ms"],
                    "summary": {
                        "total": int(row["total_questions"]),
                        "answered": answered,
                        "correct": correct if reveal_results else None,
                        "wrong": wrong if reveal_results else None,
                        "ungraded": ungraded if reveal_results else None,
                        "accuracy": (
                            round((correct / graded) * 100)
                            if reveal_results and graded
                            else None
                        ),
                        "progress": round(
                            (answered / int(row["total_questions"])) * 100
                        )
                        if row["total_questions"]
                        else 0,
                    },
                }
            )
        stats = {
            key: int(stats_row[key] or 0)
            for key in ("total", "active", "complete", "archived")
        }
        return {
            "items": items,
            "total": int(total),
            "status": status_value,
            "limit": limit_value,
            "offset": offset_value,
            "has_more": offset_value + len(items) < int(total),
            "stats": stats,
        }

    def set_session_archived(self, session_id: object, archived: object) -> dict[str, Any]:
        session_id_value = display_text(session_id)
        if not isinstance(archived, bool):
            raise ValueError("归档状态必须是布尔值")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, archived_at FROM practice_sessions
                WHERE session_id = ? AND profile_id = ?
                """,
                (session_id_value, self.profile_id),
            ).fetchone()
            if row is None:
                raise PracticeNotFound("Practice session not found")
            archived_at = row["archived_at"]
            if bool(archived_at) != archived:
                archived_at = _utc_now() if archived else None
                connection.execute(
                    """
                    UPDATE practice_sessions SET archived_at = ?
                    WHERE session_id = ? AND profile_id = ?
                    """,
                    (archived_at, session_id_value, self.profile_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return {
            "session_id": session_id_value,
            "status": row["status"],
            "archived": archived,
            "archived_at": archived_at,
        }

    @staticmethod
    def _session_history_title(
        session: sqlite3.Row,
        collection: dict[str, Any] | None,
        filters: dict[str, Any],
    ) -> str:
        if collection:
            return display_text(collection.get("title")) or "题库练习"
        if session["saved_kind"] == "favorite":
            return "收藏练习"
        if session["saved_kind"] == "wrong":
            return "错题重练"
        question_type = display_text(filters.get("question_type"))
        return f"{question_type}练习" if question_type else "随机练习"

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
                  AND archived_at IS NULL
                ORDER BY created_at DESC, rowid DESC LIMIT 1
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
                       author_name, body, parent_comment_id, created_at, updated_at
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
        self,
        occurrence_id: int,
        *,
        author_name: str,
        body: object,
        parent_comment_id: str = "",
    ) -> dict[str, Any]:
        question = self._resolve_occurrence_question(occurrence_id)
        normalized_body = _comment_body(body)
        parent_comment_id = display_text(parent_comment_id)
        now = _utc_now()
        if parent_comment_id:
            with self.connect() as check_connection:
                parent = check_connection.execute(
                    "SELECT occurrence_id, author_id FROM question_comments WHERE comment_id = ?",
                    (parent_comment_id,),
                ).fetchone()
            if parent is None or int(parent["occurrence_id"]) != int(occurrence_id):
                raise PracticeNotFound("Parent comment not found")
        item = {
            "comment_id": f"cmt_{secrets.token_hex(12)}",
            "occurrence_id": int(occurrence_id),
            "question_id": question["question_id"],
            "author_id": self.profile_id,
            "author_name": display_text(author_name) or "用户",
            "body": normalized_body,
            "parent_comment_id": parent_comment_id or None,
            "created_at": now,
            "updated_at": now,
            "can_edit": True,
            "can_delete": True,
            "edited": False,
            "agree_count": 0,
            "disagree_count": 0,
            "reply_count": 0,
            "my_reaction": "",
        }
        connection = self.connect()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO question_comments(
                        comment_id, occurrence_id, question_id, author_id,
                        author_name, body, parent_comment_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["comment_id"],
                        item["occurrence_id"],
                        item["question_id"],
                        item["author_id"],
                        item["author_name"],
                        item["body"],
                        item["parent_comment_id"],
                        item["created_at"],
                        item["updated_at"],
                    ),
                )
                if parent_comment_id:
                    parent_author = connection.execute(
                        "SELECT author_id FROM question_comments WHERE comment_id = ?",
                        (parent_comment_id,),
                    ).fetchone()
                    if parent_author and parent_author["author_id"] != self.profile_id:
                        connection.execute(
                            """
                            INSERT INTO notifications(
                                notification_id, profile_id, kind, actor_id, comment_id, body, created_at
                            ) VALUES (?, ?, 'reply', ?, ?, ?, ?)
                            """,
                            (
                                f"ntf_{secrets.token_hex(10)}",
                                parent_author["author_id"],
                                self.profile_id,
                                item["comment_id"],
                                normalized_body[:200],
                                now,
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
            connection.execute(
                "DELETE FROM comment_reactions WHERE comment_id = ?", (comment_id,)
            )
            connection.execute(
                "DELETE FROM notifications WHERE comment_id = ?", (comment_id,)
            )
            connection.execute(
                "UPDATE question_comments SET parent_comment_id = NULL WHERE parent_comment_id = ?",
                (comment_id,),
            )
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
                       author_name, body, parent_comment_id, created_at, updated_at
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
        reaction_counts: dict[str, dict[str, int]] = {}
        own_reactions: dict[str, str] = {}
        if rows:
            comment_ids = [row["comment_id"] for row in rows]
            placeholders = ",".join("?" for _ in comment_ids)
            connection = self.connect()
            try:
                for reaction in connection.execute(
                    f"SELECT comment_id, reaction, COUNT(*) AS count FROM comment_reactions WHERE comment_id IN ({placeholders}) GROUP BY comment_id, reaction",
                    comment_ids,
                ):
                    reaction_counts.setdefault(reaction["comment_id"], {})[reaction["reaction"]] = int(reaction["count"])
                for reaction in connection.execute(
                    f"SELECT comment_id, reaction FROM comment_reactions WHERE profile_id = ? AND comment_id IN ({placeholders})",
                    [self.profile_id, *comment_ids],
                ):
                    own_reactions[reaction["comment_id"]] = reaction["reaction"]
            finally:
                connection.close()
        for row in rows:
            item = dict(row)
            item.update(avatar_by_author.get(row["author_id"], {}))
            item["can_edit"] = row["author_id"] == self.profile_id
            item["can_delete"] = bool(
                viewer_is_admin or row["author_id"] == self.profile_id
            )
            item["edited"] = row["updated_at"] != row["created_at"]
            counts = reaction_counts.get(row["comment_id"], {})
            item["agree_count"] = counts.get("agree", 0)
            item["disagree_count"] = counts.get("disagree", 0)
            item["my_reaction"] = own_reactions.get(row["comment_id"], "")
            item["reply_count"] = 0
            if include_question:
                item["question"] = _question_reference(
                    questions.get(int(row["occurrence_id"]))
                )
            items.append(item)
        reply_counts: dict[str, int] = {}
        if rows:
            connection = self.connect()
            try:
                comment_ids = [row["comment_id"] for row in rows]
                placeholders = ",".join("?" for _ in comment_ids)
                for child in connection.execute(
                    f"SELECT parent_comment_id, COUNT(*) AS count FROM question_comments WHERE parent_comment_id IN ({placeholders}) GROUP BY parent_comment_id",
                    comment_ids,
                ):
                    reply_counts[child["parent_comment_id"]] = int(child["count"])
            finally:
                connection.close()
        for item in items:
            item["reply_count"] = reply_counts.get(item["comment_id"], 0)
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

    def study_goal(self) -> dict[str, Any]:
        connection = self.connect()
        try:
            return self._study_goal_payload(connection)
        finally:
            connection.close()

    def update_study_goal(
        self,
        daily_question_target: object,
        daily_duration_target_minutes: object,
    ) -> dict[str, Any]:
        try:
            question_target = int(daily_question_target)
            duration_target = int(daily_duration_target_minutes)
        except (TypeError, ValueError) as error:
            raise ValueError("学习目标必须是整数") from error
        if not 1 <= question_target <= 1000:
            raise ValueError("每日题数目标必须在 1 到 1000 之间")
        if not 1 <= duration_target <= 1440:
            raise ValueError("每日学习时长目标必须在 1 到 1440 分钟之间")
        updated_at = _utc_now()
        connection = self.connect()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO study_goals(
                        profile_id, daily_question_target,
                        daily_duration_target_minutes, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(profile_id) DO UPDATE SET
                        daily_question_target = excluded.daily_question_target,
                        daily_duration_target_minutes = excluded.daily_duration_target_minutes,
                        updated_at = excluded.updated_at
                    """,
                    (self.profile_id, question_target, duration_target, updated_at),
                )
            return self._study_goal_payload(connection)
        finally:
            connection.close()

    def _study_goal_payload(self, connection: sqlite3.Connection) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT daily_question_target, daily_duration_target_minutes, updated_at
            FROM study_goals WHERE profile_id = ?
            """,
            (self.profile_id,),
        ).fetchone()
        return {
            "daily_question_target": int(
                row["daily_question_target"] if row else DEFAULT_DAILY_QUESTION_TARGET
            ),
            "daily_duration_target_minutes": int(
                row["daily_duration_target_minutes"]
                if row
                else DEFAULT_DAILY_DURATION_TARGET_MINUTES
            ),
            "updated_at": row["updated_at"] if row else "",
            "time_zone": STUDY_TIME_ZONE_NAME,
        }

    def today_summary(self) -> dict[str, Any]:
        local_day, day_start, day_end = _study_day_bounds()
        day = local_day.isoformat()
        connection = self.connect()
        try:
            answered = connection.execute(
                """
                SELECT COUNT(*) FROM practice_session_questions q
                JOIN practice_sessions s ON s.session_id = q.session_id
                WHERE s.profile_id = ? AND q.answered_at >= ? AND q.answered_at < ?
                """,
                (self.profile_id, day_start, day_end),
            ).fetchone()[0]
            correct = connection.execute(
                """
                SELECT COUNT(*) FROM practice_session_questions q
                JOIN practice_sessions s ON s.session_id = q.session_id
                WHERE s.profile_id = ? AND q.answered_at >= ? AND q.answered_at < ?
                  AND q.is_correct = 1
                """,
                (self.profile_id, day_start, day_end),
            ).fetchone()[0]
            duration_ms = connection.execute(
                """
                SELECT COALESCE(SUM(session_elapsed), 0) FROM (
                    SELECT MAX(COALESCE(q.elapsed_ms, 0)) AS session_elapsed
                    FROM practice_session_questions q JOIN practice_sessions s ON s.session_id = q.session_id
                    WHERE s.profile_id = ? AND q.answered_at >= ? AND q.answered_at < ?
                    GROUP BY q.session_id
                )
                """,
                (self.profile_id, day_start, day_end),
            ).fetchone()[0]
            totals = {
                "wrong": connection.execute("SELECT COUNT(*) FROM study_items WHERE profile_id = ? AND kind = 'wrong'", (self.profile_id,)).fetchone()[0],
                "favorites": connection.execute("SELECT COUNT(*) FROM study_items WHERE profile_id = ? AND kind = 'favorite'", (self.profile_id,)).fetchone()[0],
                "notes": connection.execute("SELECT COUNT(*) FROM question_notes WHERE profile_id = ?", (self.profile_id,)).fetchone()[0],
                "comments": connection.execute("SELECT COUNT(*) FROM question_comments WHERE author_id = ?", (self.profile_id,)).fetchone()[0],
                "likes": connection.execute("SELECT COUNT(*) FROM comment_reactions WHERE profile_id = ? AND reaction = 'agree'", (self.profile_id,)).fetchone()[0],
            }
            daily = {
                "wrong": connection.execute("SELECT COUNT(*) FROM study_items WHERE profile_id = ? AND kind = 'wrong' AND updated_at >= ? AND updated_at < ?", (self.profile_id, day_start, day_end)).fetchone()[0],
                "favorites": connection.execute("SELECT COUNT(*) FROM study_items WHERE profile_id = ? AND kind = 'favorite' AND updated_at >= ? AND updated_at < ?", (self.profile_id, day_start, day_end)).fetchone()[0],
                "notes": connection.execute("SELECT COUNT(*) FROM question_notes WHERE profile_id = ? AND updated_at >= ? AND updated_at < ?", (self.profile_id, day_start, day_end)).fetchone()[0],
                "comments": connection.execute("SELECT COUNT(*) FROM question_comments WHERE author_id = ? AND created_at >= ? AND created_at < ?", (self.profile_id, day_start, day_end)).fetchone()[0],
                "likes": connection.execute("SELECT COUNT(*) FROM comment_reactions WHERE profile_id = ? AND reaction = 'agree' AND created_at >= ? AND created_at < ?", (self.profile_id, day_start, day_end)).fetchone()[0],
            }
            goal = self._study_goal_payload(connection)
            trend = self._weekly_study_trend(connection, local_day)
            streak_days = self._study_streak(connection, local_day)
        finally:
            connection.close()
        accuracy = round((int(correct) / int(answered)) * 100) if answered else 0
        duration_minutes = round(int(duration_ms or 0) / 60000, 1)
        question_progress = min(
            100,
            round((int(answered) / goal["daily_question_target"]) * 100),
        )
        duration_progress = min(
            100,
            round(
                (duration_minutes / goal["daily_duration_target_minutes"]) * 100
            ),
        )
        return {
            "date": day,
            "updated_at": _utc_now(),
            "score": accuracy,
            "duration_minutes": duration_minutes,
            "questions": int(answered),
            "accuracy": accuracy,
            "streak_days": streak_days,
            "goal": {
                **goal,
                "question_progress": question_progress,
                "duration_progress": duration_progress,
                "completed": question_progress >= 100 and duration_progress >= 100,
            },
            "weekly_trend": trend,
            "totals": {key: int(value) for key, value in totals.items()},
            "daily": {key: int(value) for key, value in daily.items()},
        }

    def _study_streak(self, connection: sqlite3.Connection, local_day: date) -> int:
        rows = connection.execute(
            """
            SELECT DISTINCT date(q.answered_at, '+8 hours') AS study_day
            FROM practice_session_questions q
            JOIN practice_sessions s ON s.session_id = q.session_id
            WHERE s.profile_id = ? AND q.answered_at IS NOT NULL
            """,
            (self.profile_id,),
        ).fetchall()
        study_days = {row["study_day"] for row in rows if row["study_day"]}
        cursor = local_day if local_day.isoformat() in study_days else local_day - timedelta(days=1)
        streak = 0
        while cursor.isoformat() in study_days:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    def _weekly_study_trend(
        self, connection: sqlite3.Connection, local_day: date
    ) -> list[dict[str, Any]]:
        first_day = local_day - timedelta(days=6)
        range_start = _study_day_bounds(first_day)[1]
        range_end = _study_day_bounds(local_day)[2]
        question_rows = connection.execute(
            """
            SELECT date(q.answered_at, '+8 hours') AS study_day,
                   COUNT(*) AS questions,
                   SUM(CASE WHEN q.is_correct = 1 THEN 1 ELSE 0 END) AS correct
            FROM practice_session_questions q
            JOIN practice_sessions s ON s.session_id = q.session_id
            WHERE s.profile_id = ? AND q.answered_at >= ? AND q.answered_at < ?
            GROUP BY study_day
            """,
            (self.profile_id, range_start, range_end),
        ).fetchall()
        duration_rows = connection.execute(
            """
            SELECT study_day, COALESCE(SUM(session_elapsed), 0) AS duration_ms
            FROM (
                SELECT date(q.answered_at, '+8 hours') AS study_day,
                       q.session_id,
                       MAX(COALESCE(q.elapsed_ms, 0)) AS session_elapsed
                FROM practice_session_questions q
                JOIN practice_sessions s ON s.session_id = q.session_id
                WHERE s.profile_id = ? AND q.answered_at >= ? AND q.answered_at < ?
                GROUP BY study_day, q.session_id
            )
            GROUP BY study_day
            """,
            (self.profile_id, range_start, range_end),
        ).fetchall()
        questions_by_day = {row["study_day"]: row for row in question_rows}
        duration_by_day = {
            row["study_day"]: int(row["duration_ms"] or 0) for row in duration_rows
        }
        trend = []
        for offset in range(7):
            trend_day = first_day + timedelta(days=offset)
            day_key = trend_day.isoformat()
            row = questions_by_day.get(day_key)
            questions = int(row["questions"] if row else 0)
            correct = int(row["correct"] if row and row["correct"] is not None else 0)
            trend.append(
                {
                    "date": day_key,
                    "questions": questions,
                    "accuracy": round((correct / questions) * 100) if questions else 0,
                    "duration_minutes": round(
                        duration_by_day.get(day_key, 0) / 60000, 1
                    ),
                }
            )
        return trend

    def react_comment(self, comment_id: str, reaction: str) -> dict[str, Any]:
        comment_id = display_text(comment_id)
        reaction = display_text(reaction).lower()
        if reaction not in {"agree", "disagree"}:
            raise ValueError("reaction must be agree or disagree")
        connection = self.connect()
        try:
            with connection:
                comment = connection.execute(
                    "SELECT author_id FROM question_comments WHERE comment_id = ?",
                    (comment_id,),
                ).fetchone()
                if comment is None:
                    raise PracticeNotFound("Comment not found")
                current = connection.execute(
                    "SELECT reaction FROM comment_reactions WHERE comment_id = ? AND profile_id = ?",
                    (comment_id, self.profile_id),
                ).fetchone()
                if current and current["reaction"] == reaction:
                    connection.execute(
                        "DELETE FROM comment_reactions WHERE comment_id = ? AND profile_id = ?",
                        (comment_id, self.profile_id),
                    )
                    selected = ""
                else:
                    connection.execute(
                        """
                        INSERT INTO comment_reactions(comment_id, profile_id, reaction, created_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(comment_id, profile_id) DO UPDATE SET reaction = excluded.reaction, created_at = excluded.created_at
                        """,
                        (comment_id, self.profile_id, reaction, _utc_now()),
                    )
                    selected = reaction
                    if comment["author_id"] != self.profile_id and reaction == "agree":
                        connection.execute(
                            """
                            INSERT INTO notifications(notification_id, profile_id, kind, actor_id, comment_id, body, created_at)
                            VALUES (?, ?, 'agree', ?, ?, '', ?)
                            """,
                            (f"ntf_{secrets.token_hex(10)}", comment["author_id"], self.profile_id, comment_id, _utc_now()),
                        )
                counts = connection.execute(
                    "SELECT reaction, COUNT(*) AS count FROM comment_reactions WHERE comment_id = ? GROUP BY reaction",
                    (comment_id,),
                ).fetchall()
        finally:
            connection.close()
        result = {"comment_id": comment_id, "my_reaction": selected, "agree_count": 0, "disagree_count": 0}
        for row in counts:
            result[f"{row['reaction']}_count"] = int(row["count"])
        return result

    def my_likes(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        connection = self.connect()
        try:
            rows = connection.execute(
                """
                SELECT c.comment_id, c.occurrence_id, c.question_id, c.author_id, c.author_name,
                       c.body, c.parent_comment_id, c.created_at, c.updated_at, r.created_at AS liked_at
                FROM comment_reactions r JOIN question_comments c ON c.comment_id = r.comment_id
                WHERE r.profile_id = ? AND r.reaction = 'agree'
                ORDER BY r.created_at DESC LIMIT ? OFFSET ?
                """,
                (self.profile_id, limit, offset),
            ).fetchall()
            total = connection.execute(
                "SELECT COUNT(*) FROM comment_reactions WHERE profile_id = ? AND reaction = 'agree'",
                (self.profile_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        items = self._decorate_comments(rows, viewer_is_admin=False, include_question=True)
        for item, row in zip(items, rows):
            item["liked_at"] = row["liked_at"]
        return {"count": len(items), "total": int(total), "offset": offset, "items": items}

    def follow_user(self, target_id: str) -> dict[str, Any]:
        target_id = display_text(target_id)
        if not target_id or target_id == self.profile_id:
            raise ValueError("不能关注自己")
        connection = self.connect()
        try:
            with connection:
                has_users = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
                ).fetchone()
                if not has_users or connection.execute(
                    "SELECT 1 FROM users WHERE user_id = ? AND status = 'active'", (target_id,)
                ).fetchone() is None:
                    raise PracticeNotFound("用户不存在")
                connection.execute(
                    "INSERT OR IGNORE INTO user_follows(follower_id, following_id, created_at) VALUES (?, ?, ?)",
                    (self.profile_id, target_id, _utc_now()),
                )
        finally:
            connection.close()
        return self.profile_stats(target_id)

    def unfollow_user(self, target_id: str) -> dict[str, Any]:
        target_id = display_text(target_id)
        connection = self.connect()
        try:
            with connection:
                connection.execute(
                    "DELETE FROM user_follows WHERE follower_id = ? AND following_id = ?",
                    (self.profile_id, target_id),
                )
        finally:
            connection.close()
        return self.profile_stats(target_id)

    def profile_stats(self, user_id: str) -> dict[str, Any]:
        user_id = display_text(user_id)
        connection = self.connect()
        try:
            followers = connection.execute(
                """
                SELECT COUNT(*) FROM user_follows f
                JOIN users u ON u.user_id = f.follower_id
                WHERE f.following_id = ? AND u.status = 'active'
                """,
                (user_id,),
            ).fetchone()[0]
            following = connection.execute(
                """
                SELECT COUNT(*) FROM user_follows f
                JOIN users u ON u.user_id = f.following_id
                WHERE f.follower_id = ? AND u.status = 'active'
                """,
                (user_id,),
            ).fetchone()[0]
            likes = connection.execute(
                """
                SELECT COUNT(*) FROM comment_reactions r
                JOIN question_comments c ON c.comment_id = r.comment_id
                WHERE c.author_id = ? AND r.reaction = 'agree' AND r.profile_id != c.author_id
                """,
                (user_id,),
            ).fetchone()[0]
            dynamics = connection.execute(
                "SELECT COUNT(*) FROM question_comments WHERE author_id = ?", (user_id,)
            ).fetchone()[0]
            has_forum_reactions = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'forum_reactions'"
            ).fetchone()
            if has_forum_reactions:
                forum_likes = connection.execute(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM forum_reactions fr
                       JOIN forum_topics t ON fr.target_type = 'topic' AND fr.target_id = t.topic_id
                       WHERE t.author_id = ? AND t.status = 'open'
                         AND fr.reaction = 'agree' AND fr.profile_id != t.author_id)
                      +
                      (SELECT COUNT(*) FROM forum_reactions fr
                       JOIN forum_replies r ON fr.target_type = 'reply' AND fr.target_id = r.reply_id
                       JOIN forum_topics t ON t.topic_id = r.topic_id
                       WHERE r.author_id = ? AND r.status = 'visible' AND t.status = 'open'
                         AND fr.reaction = 'agree' AND fr.profile_id != r.author_id)
                    """,
                    (user_id, user_id),
                ).fetchone()[0]
                forum_dynamics = connection.execute(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM forum_topics WHERE author_id = ? AND status = 'open')
                      +
                      (SELECT COUNT(*) FROM forum_replies r JOIN forum_topics t ON t.topic_id = r.topic_id
                       WHERE r.author_id = ? AND r.status = 'visible' AND t.status = 'open')
                    """,
                    (user_id, user_id),
                ).fetchone()[0]
                likes += int(forum_likes)
                dynamics += int(forum_dynamics)
            is_following = bool(
                connection.execute(
                    "SELECT 1 FROM user_follows WHERE follower_id = ? AND following_id = ?",
                    (self.profile_id, user_id),
                ).fetchone()
            )
        finally:
            connection.close()
        return {
            "followers": int(followers),
            "following": int(following),
            "likes": int(likes),
            "dynamics": int(dynamics),
            "is_following": is_following,
            "is_self": user_id == self.profile_id,
        }

    def profile_connections(
        self,
        user_id: str,
        *,
        relation: str = "followers",
        query: str = "",
        limit: int = 30,
        offset: int = 0,
    ) -> dict[str, Any]:
        user_id = display_text(user_id)
        relation = display_text(relation).lower()
        if relation not in {"followers", "following"}:
            raise ValueError("关系类型无效")
        query = display_text(query)[:64]
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        connection = self.connect()
        try:
            owner = connection.execute(
                """
                SELECT user_id, username, avatar, updated_at, privacy_json
                FROM users WHERE user_id = ? AND status = 'active'
                """,
                (user_id,),
            ).fetchone()
            if owner is None:
                raise PracticeNotFound("用户不存在")
            owner_privacy = profile_privacy_preferences(owner["privacy_json"])
            if user_id != self.profile_id and not owner_privacy[relation]:
                raise PracticeForbidden(
                    "该用户未公开粉丝列表" if relation == "followers" else "该用户未公开关注列表"
                )

            owner_column = "f.following_id" if relation == "followers" else "f.follower_id"
            person_column = "f.follower_id" if relation == "followers" else "f.following_id"
            where = [f"{owner_column} = ?", "u.status = 'active'"]
            parameters: list[Any] = [user_id]
            if query:
                escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                where.append("u.username LIKE ? ESCAPE '\\' COLLATE NOCASE")
                parameters.append(f"%{escaped_query}%")
            where_sql = " AND ".join(where)
            rows = connection.execute(
                f"""
                SELECT u.user_id, u.username, u.avatar, u.updated_at, u.ip_location,
                       u.school, u.major, u.privacy_json, f.created_at AS connected_at,
                       (SELECT COUNT(*) FROM user_follows count_followers
                        JOIN users count_users
                          ON count_users.user_id = count_followers.follower_id
                         AND count_users.status = 'active'
                        WHERE count_followers.following_id = u.user_id) AS follower_count,
                       EXISTS(
                           SELECT 1 FROM user_follows viewer_follow
                           WHERE viewer_follow.follower_id = ? AND viewer_follow.following_id = u.user_id
                       ) AS is_following,
                       EXISTS(
                           SELECT 1 FROM user_follows follows_viewer
                           WHERE follows_viewer.follower_id = u.user_id AND follows_viewer.following_id = ?
                       ) AS follows_viewer
                FROM user_follows f
                JOIN users u ON u.user_id = {person_column}
                WHERE {where_sql}
                ORDER BY f.created_at DESC, u.username COLLATE NOCASE, u.user_id
                LIMIT ? OFFSET ?
                """,
                (self.profile_id, self.profile_id, *parameters, limit, offset),
            ).fetchall()
            total = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM user_follows f
                JOIN users u ON u.user_id = {person_column}
                WHERE {where_sql}
                """,
                parameters,
            ).fetchone()[0]
        finally:
            connection.close()

        items: list[dict[str, Any]] = []
        for row in rows:
            privacy = profile_privacy_preferences(row["privacy_json"])
            is_self = row["user_id"] == self.profile_id
            items.append(
                {
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "avatar": row["avatar"],
                    "avatar_url": public_avatar_url(row["user_id"], row["avatar"], row["updated_at"]),
                    "ip_location": row["ip_location"] or "本地",
                    "school": row["school"] if is_self or privacy.get("school") else "",
                    "major": row["major"] if is_self or privacy.get("major") else "",
                    "follower_count": (
                        int(row["follower_count"])
                        if is_self or privacy["followers"]
                        else None
                    ),
                    "is_following": bool(row["is_following"]),
                    "follows_viewer": bool(row["follows_viewer"]),
                    "is_self": is_self,
                    "connected_at": row["connected_at"],
                }
            )
        stats = self.profile_stats(user_id)
        visibility = {
            "followers": user_id == self.profile_id or owner_privacy["followers"],
            "following": user_id == self.profile_id or owner_privacy["following"],
        }
        if not visibility["followers"]:
            stats["followers"] = None
        if not visibility["following"]:
            stats["following"] = None
        return {
            "owner": {
                "user_id": owner["user_id"],
                "username": owner["username"],
                "avatar": owner["avatar"],
                "avatar_url": public_avatar_url(owner["user_id"], owner["avatar"], owner["updated_at"]),
            },
            "relation": relation,
            "query": query,
            "count": len(items),
            "total": int(total),
            "offset": offset,
            "has_more": offset + len(items) < int(total),
            "stats": stats,
            "visibility": visibility,
            "items": items,
        }

    def public_profile(self, user_id: str) -> dict[str, Any]:
        user_id = display_text(user_id)
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ? AND status = 'active'", (user_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise PracticeNotFound("用户不存在")
        profile = {
            "user_id": row["user_id"],
            "username": row["username"],
            "avatar": row["avatar"],
            "avatar_url": public_avatar_url(row["user_id"], row["avatar"], row["updated_at"]),
            "ip_location": row["ip_location"] if "ip_location" in row.keys() else "本地",
            "birthday": row["birthday"] if "birthday" in row.keys() else "",
            "gender": row["gender"] if "gender" in row.keys() else "",
            "school": row["school"] if "school" in row.keys() else "",
            "major": row["major"] if "major" in row.keys() else "",
        }
        privacy = profile_privacy_preferences(row["privacy_json"])
        if user_id != self.profile_id:
            for key in ("birthday", "gender", "school", "major"):
                if not privacy.get(key):
                    profile[key] = ""
        else:
            profile.update(
                {
                    "birthday": row["birthday"] if "birthday" in row.keys() else "",
                    "gender": row["gender"] if "gender" in row.keys() else "",
                    "privacy": privacy,
                }
            )
        profile["stats"] = self.profile_stats(user_id)
        profile["visibility"] = {
            "followers": user_id == self.profile_id or privacy["followers"],
            "following": user_id == self.profile_id or privacy["following"],
            "direct_messages": user_id == self.profile_id or privacy["direct_messages"],
        }
        if user_id != self.profile_id:
            if not privacy["followers"]:
                profile["stats"]["followers"] = None
            if not privacy["following"]:
                profile["stats"]["following"] = None
        profile["stats"]["can_message"] = profile["visibility"]["direct_messages"]
        profile["is_self"] = profile["stats"]["is_self"]
        profile["dynamics"] = self._public_dynamics(user_id)
        return profile

    def _public_dynamics(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        connection = self.connect()
        try:
            comment_rows = connection.execute(
                """
                SELECT comment_id, occurrence_id, question_id, author_id, author_name,
                       body, parent_comment_id, created_at, updated_at
                FROM question_comments WHERE author_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            has_forum = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'forum_topics'"
            ).fetchone()
            if has_forum:
                topic_rows = connection.execute(
                    """
                    SELECT t.topic_id, t.title, t.body, t.created_at, t.updated_at,
                           (SELECT COUNT(*) FROM forum_replies r
                            WHERE r.topic_id = t.topic_id AND r.status = 'visible') AS reply_count
                    FROM forum_topics t
                    WHERE t.author_id = ? AND t.status = 'open'
                    ORDER BY t.created_at DESC LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
                reply_rows = connection.execute(
                    """
                    SELECT r.reply_id, r.topic_id, r.body, r.created_at, r.updated_at,
                           t.title AS topic_title
                    FROM forum_replies r
                    JOIN forum_topics t ON t.topic_id = r.topic_id
                    WHERE r.author_id = ? AND r.status = 'visible' AND t.status = 'open'
                    ORDER BY r.created_at DESC LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
            else:
                topic_rows = []
                reply_rows = []
        finally:
            connection.close()
        items = self._decorate_comments(
            comment_rows, viewer_is_admin=False, include_question=True
        )
        for item in items:
            item["kind"] = "comment"
        items.extend(
            {
                "kind": "forum_topic",
                "topic_id": row["topic_id"],
                "title": row["title"],
                "body": row["body"],
                "reply_count": int(row["reply_count"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in topic_rows
        )
        items.extend(
            {
                "kind": "forum_reply",
                "reply_id": row["reply_id"],
                "topic_id": row["topic_id"],
                "topic_title": row["topic_title"],
                "body": row["body"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in reply_rows
        )
        items.sort(
            key=lambda item: (item.get("created_at", ""), item.get("kind", "")),
            reverse=True,
        )
        return items[:limit]

    def list_messages(self, category: str = "all") -> dict[str, Any]:
        connection = self.connect()
        try:
            has_users = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
            if has_users:
                notifications = connection.execute(
                    """
                    SELECT n.notification_id, n.kind, n.actor_id, n.comment_id, n.body, n.created_at, n.read_at,
                           COALESCE(u.username, '') AS actor_name
                    FROM notifications n LEFT JOIN users u ON u.user_id = n.actor_id
                    WHERE n.profile_id = ? ORDER BY n.created_at DESC LIMIT 100
                    """,
                    (self.profile_id,),
                ).fetchall()
                direct = connection.execute(
                    """
                    SELECT m.message_id, m.sender_id, m.receiver_id, m.body, m.created_at, m.read_at, u.username AS sender_name
                    FROM direct_messages m JOIN users u ON u.user_id = m.sender_id
                    WHERE m.receiver_id = ? OR m.sender_id = ? ORDER BY m.created_at DESC LIMIT 100
                    """,
                    (self.profile_id, self.profile_id),
                ).fetchall()
            else:
                notifications = connection.execute("SELECT notification_id, kind, actor_id, comment_id, body, created_at, read_at, '' AS actor_name FROM notifications WHERE profile_id = ? ORDER BY created_at DESC LIMIT 100", (self.profile_id,)).fetchall()
                direct = connection.execute("SELECT message_id, sender_id, receiver_id, body, created_at, read_at, '' AS sender_name FROM direct_messages WHERE receiver_id = ? OR sender_id = ? ORDER BY created_at DESC LIMIT 100", (self.profile_id, self.profile_id)).fetchall()
        finally:
            connection.close()
        return {"category": category, "notifications": [dict(row) for row in notifications], "messages": [dict(row) for row in direct]}

    def message_alerts(self, *, limit: int = 40) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        connection = self.connect()
        try:
            notifications = connection.execute(
                """
                SELECT n.notification_id, n.kind, n.body, n.created_at,
                       COALESCE(u.username, '') AS actor_name
                FROM notifications n
                LEFT JOIN users u ON u.user_id = n.actor_id
                WHERE n.profile_id = ?
                ORDER BY n.created_at DESC, n.notification_id DESC
                LIMIT ?
                """,
                (self.profile_id, limit),
            ).fetchall()
            direct = connection.execute(
                """
                SELECT m.message_id, m.body, m.created_at,
                       COALESCE(u.username, '') AS sender_name
                FROM direct_messages m
                LEFT JOIN users u ON u.user_id = m.sender_id
                WHERE m.receiver_id = ?
                ORDER BY m.created_at DESC, m.message_id DESC
                LIMIT ?
                """,
                (self.profile_id, limit),
            ).fetchall()
        finally:
            connection.close()
        items: list[dict[str, Any]] = []
        category_map = {
            "system": "system",
            "reply": "replies",
            "mention": "replies",
            "agree": "agreements",
        }
        for row in notifications:
            category = category_map.get(row["kind"])
            if not category:
                continue
            actor = row["actor_name"] or "用户"
            message = {
                "system": row["body"] or "有一条新的系统通知",
                "replies": f"{actor} {row['body']}" if row["body"].startswith(("论坛回复：", "论坛提及：")) else f"{actor} 回复或提到了你",
                "agreements": f"{actor} {row['body']}" if row["body"].startswith("赞同了你的论坛") else f"{actor} 赞同了你的评论",
            }[category]
            items.append(
                {
                    "event_id": f"notification:{row['notification_id']}",
                    "category": category,
                    "message": message,
                    "created_at": row["created_at"],
                }
            )
        for row in direct:
            sender = row["sender_name"] or "用户"
            items.append(
                {
                    "event_id": f"direct:{row['message_id']}",
                    "category": "direct",
                    "message": f"{sender} 发来一条私信",
                    "created_at": row["created_at"],
                }
            )
        items.sort(key=lambda item: (item["created_at"], item["event_id"]), reverse=True)
        return {"items": items[:limit]}

    def send_message(self, receiver_id: str, body: object) -> dict[str, Any]:
        normalized_body = _comment_body(body)
        receiver_id = display_text(receiver_id)
        now = _utc_now()
        connection = self.connect()
        try:
            with connection:
                receiver = connection.execute(
                    "SELECT privacy_json FROM users WHERE user_id = ? AND status = 'active'",
                    (receiver_id,),
                ).fetchone()
                if receiver is None:
                    raise PracticeNotFound("用户不存在")
                if receiver_id != self.profile_id and not profile_privacy_preferences(receiver["privacy_json"])["direct_messages"]:
                    raise PracticeForbidden("该用户暂未开启私信")
                message = {"message_id": f"msg_{secrets.token_hex(12)}", "sender_id": self.profile_id, "receiver_id": receiver_id, "body": normalized_body, "created_at": now, "read_at": None}
                connection.execute(
                    "INSERT INTO direct_messages(message_id, sender_id, receiver_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
                    (message["message_id"], self.profile_id, receiver_id, normalized_body, now),
                )
        finally:
            connection.close()
        return message

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
                SELECT ps.status, ps.mode, ps.collection_id, ps.archived_at,
                       psq.question_id, psq.occurrence_id, psq.answered_at
                FROM practice_sessions ps
                JOIN practice_session_questions psq ON psq.session_id = ps.session_id
                WHERE ps.session_id = ? AND ps.profile_id = ? AND psq.position = ?
                """,
                (session_id, self.profile_id, position),
            ).fetchone()
            if row is None:
                raise PracticeNotFound("Practice question not found")
            if row["archived_at"]:
                raise PracticeConflict("归档练习需先恢复")
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
                    SELECT created_at, status, archived_at
                    FROM practice_sessions
                    WHERE session_id = ? AND profile_id = ?
                    """,
                    (session_id, self.profile_id),
                ).fetchone()
                if row is None:
                    raise PracticeNotFound("Practice session not found")
                if row["archived_at"]:
                    raise PracticeConflict("归档练习需先恢复")
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
                    (SELECT COUNT(*) FROM practice_sessions WHERE status = 'active' AND archived_at IS NULL) AS active_practice,
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

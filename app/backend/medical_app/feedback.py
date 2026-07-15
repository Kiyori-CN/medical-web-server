from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .normalize import display_text


FEEDBACK_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS user_feedback (
    feedback_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('bug', 'suggestion', 'content', 'other')),
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_feedback_status
ON user_feedback(status, updated_at DESC, feedback_id);

CREATE INDEX IF NOT EXISTS idx_user_feedback_profile
ON user_feedback(profile_id, created_at DESC, feedback_id);
"""

FEEDBACK_CATEGORIES = {"bug", "suggestion", "content", "other"}
FEEDBACK_STATUSES = {"open", "resolved"}
FEEDBACK_MAX_LENGTH = 4000


class FeedbackNotFound(LookupError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class FeedbackStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).resolve()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(FEEDBACK_SCHEMA_SQL)
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def create(self, profile_id: str, category: object, body: object) -> dict[str, Any]:
        category_value = display_text(category).lower() or "suggestion"
        if category_value not in FEEDBACK_CATEGORIES:
            raise ValueError("反馈类型无效")
        body_value = display_text(body)
        if len(body_value) < 2:
            raise ValueError("反馈内容至少需要 2 个字符")
        if len(body_value) > FEEDBACK_MAX_LENGTH:
            raise ValueError(f"反馈内容不能超过 {FEEDBACK_MAX_LENGTH} 个字符")
        feedback_id = f"fb_{secrets.token_hex(12)}"
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_feedback(
                    feedback_id, profile_id, category, body, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'open', ?, ?)
                """,
                (feedback_id, display_text(profile_id), category_value, body_value, now, now),
            )
            connection.commit()
        return {
            "feedback_id": feedback_id,
            "category": category_value,
            "body": body_value,
            "status": "open",
            "created_at": now,
            "updated_at": now,
        }

    def list_admin(
        self,
        *,
        query: object = "",
        status: object = "",
        limit: object = 100,
        offset: object = 0,
    ) -> dict[str, Any]:
        query_value = display_text(query)
        status_value = display_text(status).lower()
        if status_value and status_value not in FEEDBACK_STATUSES:
            raise ValueError("反馈状态无效")
        try:
            limit_value = max(1, min(int(limit), 200))
            offset_value = max(0, int(offset))
        except (TypeError, ValueError) as error:
            raise ValueError("反馈分页参数无效") from error
        conditions = []
        params: list[object] = []
        if status_value:
            conditions.append("f.status = ?")
            params.append(status_value)
        if query_value:
            conditions.append("(f.body LIKE ? OR COALESCE(u.username, '') LIKE ?)")
            pattern = f"%{query_value}%"
            params.extend((pattern, pattern))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as connection:
            total = connection.execute(
                f"""
                SELECT COUNT(*) FROM user_feedback f
                LEFT JOIN users u ON u.user_id = f.profile_id
                {where}
                """,
                params,
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT f.feedback_id, f.profile_id,
                       COALESCE(u.username, '已注销用户') AS username,
                       f.category, f.body, f.status, f.created_at, f.updated_at
                FROM user_feedback f
                LEFT JOIN users u ON u.user_id = f.profile_id
                {where}
                ORDER BY CASE f.status WHEN 'open' THEN 0 ELSE 1 END,
                         f.updated_at DESC, f.feedback_id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit_value, offset_value],
            ).fetchall()
            stats_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM user_feedback GROUP BY status"
            ).fetchall()
        stats = {"total": 0, "open": 0, "resolved": 0}
        for row in stats_rows:
            stats[row["status"]] = int(row["count"])
            stats["total"] += int(row["count"])
        return {
            "items": [dict(row) for row in rows],
            "total": int(total),
            "query": query_value,
            "status": status_value,
            "limit": limit_value,
            "offset": offset_value,
            "stats": stats,
        }

    def update_status(self, feedback_id: object, status: object) -> dict[str, Any]:
        feedback_id_value = display_text(feedback_id)
        status_value = display_text(status).lower()
        if status_value not in FEEDBACK_STATUSES:
            raise ValueError("反馈状态无效")
        now = _utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT feedback_id FROM user_feedback WHERE feedback_id = ?",
                (feedback_id_value,),
            ).fetchone()
            if row is None:
                raise FeedbackNotFound("反馈不存在")
            connection.execute(
                "UPDATE user_feedback SET status = ?, updated_at = ? WHERE feedback_id = ?",
                (status_value, now, feedback_id_value),
            )
            connection.commit()
            updated = connection.execute(
                """
                SELECT feedback_id, profile_id, category, body, status, created_at, updated_at
                FROM user_feedback WHERE feedback_id = ?
                """,
                (feedback_id_value,),
            ).fetchone()
        return dict(updated)

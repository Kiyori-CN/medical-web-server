from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .normalize import display_text


FORUM_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS forum_sections (
    section_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'hidden'))
);

CREATE TABLE IF NOT EXISTS forum_topics (
    topic_id TEXT PRIMARY KEY,
    section_id TEXT NOT NULL REFERENCES forum_sections(section_id),
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'hidden', 'deleted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS forum_replies (
    reply_id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES forum_topics(topic_id) ON DELETE CASCADE,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    body TEXT NOT NULL,
    parent_reply_id TEXT,
    status TEXT NOT NULL DEFAULT 'visible' CHECK (status IN ('visible', 'hidden', 'deleted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_forum_topics_listing
ON forum_topics(section_id, status, updated_at DESC, topic_id);

CREATE INDEX IF NOT EXISTS idx_forum_replies_topic
ON forum_replies(topic_id, status, created_at, reply_id);
"""

DEFAULT_SECTIONS = (
    ("study", "学习交流", "复习方法、知识梳理与备考经验", 10),
    ("questions", "题目讨论", "围绕原题、解析和易错点展开讨论", 20),
)


class ForumNotFound(LookupError):
    pass


class ForumForbidden(PermissionError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _text(value: object, *, field: str, minimum: int, maximum: int) -> str:
    result = display_text(value)
    if len(result) < minimum or len(result) > maximum:
        raise ValueError(f"{field}长度须为 {minimum}–{maximum} 个字符")
    return result


class ForumStore:
    def __init__(self, database_path: str | Path, profile_id: str):
        self.database_path = Path(database_path).resolve()
        self.profile_id = display_text(profile_id)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(FORUM_SCHEMA_SQL)
            connection.executemany(
                "INSERT OR IGNORE INTO forum_sections(section_id, name, description, position) VALUES (?, ?, ?, ?)",
                DEFAULT_SECTIONS,
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def sections(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.section_id, s.name, s.description, s.position,
                       COUNT(t.topic_id) AS topic_count
                FROM forum_sections s
                LEFT JOIN forum_topics t ON t.section_id = s.section_id AND t.status = 'open'
                WHERE s.status = 'active'
                GROUP BY s.section_id
                ORDER BY s.position, s.section_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_topics(
        self,
        *,
        section_id: str = "",
        query: str = "",
        sort: str = "latest",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        section_id = display_text(section_id)
        query = display_text(query)
        sort = display_text(sort).lower() or "latest"
        if sort not in {"latest", "newest", "replies"}:
            raise ValueError("论坛排序方式无效")
        limit = max(1, min(int(limit), 50))
        offset = max(0, int(offset))
        clauses = ["t.status = 'open'"]
        parameters: list[Any] = []
        if section_id:
            clauses.append("t.section_id = ?")
            parameters.append(section_id)
        if query:
            clauses.append("(t.title LIKE ? ESCAPE '\\' OR t.body LIKE ? ESCAPE '\\' OR t.author_name LIKE ? ESCAPE '\\')")
            escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped_query}%"
            parameters.extend([like, like, like])
        where = " AND ".join(clauses)
        ordering = {
            "latest": "last_activity DESC, t.topic_id DESC",
            "newest": "t.created_at DESC, t.topic_id DESC",
            "replies": "reply_count DESC, last_activity DESC",
        }[sort]
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM forum_topics t WHERE {where}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT t.topic_id, t.section_id, s.name AS section_name,
                       t.author_id, t.author_name, t.title, t.body,
                       t.created_at, t.updated_at,
                       (SELECT COUNT(*) FROM forum_replies r
                        WHERE r.topic_id = t.topic_id AND r.status = 'visible') AS reply_count,
                       COALESCE((SELECT MAX(r.updated_at) FROM forum_replies r
                                 WHERE r.topic_id = t.topic_id AND r.status = 'visible'),
                                t.updated_at) AS last_activity
                FROM forum_topics t
                JOIN forum_sections s ON s.section_id = t.section_id
                WHERE {where}
                ORDER BY {ordering}
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
        return {"total": int(total), "limit": limit, "offset": offset, "items": [self._topic_summary(row) for row in rows]}

    def create_topic(self, *, section_id: object, title: object, body: object, author_name: object) -> dict[str, Any]:
        section_id = display_text(section_id)
        title = _text(title, field="标题", minimum=4, maximum=120)
        body = _text(body, field="正文", minimum=4, maximum=20000)
        author_name = _text(author_name, field="用户名", minimum=1, maximum=32)
        now = _utc_now()
        topic_id = secrets.token_urlsafe(18)
        with self._connect() as connection:
            section = connection.execute(
                "SELECT section_id FROM forum_sections WHERE section_id = ? AND status = 'active'",
                (section_id,),
            ).fetchone()
            if section is None:
                raise ForumNotFound("论坛分区不存在")
            connection.execute(
                "INSERT INTO forum_topics(topic_id, section_id, author_id, author_name, title, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (topic_id, section_id, self.profile_id, author_name, title, body, now, now),
            )
            connection.commit()
        return self.get_topic(topic_id, viewer_is_admin=False)["topic"]

    def get_topic(self, topic_id: str, *, viewer_is_admin: bool = False) -> dict[str, Any]:
        topic_id = display_text(topic_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT t.*, s.name AS section_name,
                       (SELECT COUNT(*) FROM forum_replies r
                        WHERE r.topic_id = t.topic_id AND r.status = 'visible') AS reply_count
                FROM forum_topics t JOIN forum_sections s ON s.section_id = t.section_id
                WHERE t.topic_id = ?
                """,
                (topic_id,),
            ).fetchone()
            if row is None or (row["status"] != "open" and not viewer_is_admin and row["author_id"] != self.profile_id):
                raise ForumNotFound("论坛主题不存在")
            replies = connection.execute(
                """
                SELECT reply_id, topic_id, author_id, author_name, body,
                       parent_reply_id, status, created_at, updated_at
                FROM forum_replies
                WHERE topic_id = ? AND (status = 'visible' OR author_id = ? OR ? = 1)
                ORDER BY created_at, rowid
                LIMIT 500
                """,
                (topic_id, self.profile_id, int(viewer_is_admin)),
            ).fetchall()
        return {"topic": self._topic_detail(row, viewer_is_admin), "replies": [self._reply(row, viewer_is_admin) for row in replies]}

    def update_topic(self, topic_id: str, *, title: object, body: object, viewer_is_admin: bool = False) -> dict[str, Any]:
        title = _text(title, field="标题", minimum=4, maximum=120)
        body = _text(body, field="正文", minimum=4, maximum=20000)
        with self._connect() as connection:
            row = self._topic_row(connection, topic_id)
            self._require_owner(row["author_id"], viewer_is_admin)
            if row["status"] != "open":
                raise ForumForbidden("已删除或隐藏的主题不能编辑")
            now = _utc_now()
            connection.execute(
                "UPDATE forum_topics SET title = ?, body = ?, updated_at = ? WHERE topic_id = ?",
                (title, body, now, topic_id),
            )
            connection.commit()
        return self.get_topic(topic_id, viewer_is_admin=viewer_is_admin)["topic"]

    def delete_topic(self, topic_id: str, *, viewer_is_admin: bool = False) -> bool:
        with self._connect() as connection:
            row = self._topic_row(connection, topic_id)
            self._require_owner(row["author_id"], viewer_is_admin)
            cursor = connection.execute(
                "UPDATE forum_topics SET status = 'deleted', updated_at = ? WHERE topic_id = ? AND status != 'deleted'",
                (_utc_now(), topic_id),
            )
            connection.commit()
        return cursor.rowcount > 0

    def create_reply(
        self,
        topic_id: str,
        *,
        body: object,
        author_name: object,
        parent_reply_id: object = "",
    ) -> dict[str, Any]:
        body = _text(body, field="回复", minimum=2, maximum=5000)
        author_name = _text(author_name, field="用户名", minimum=1, maximum=32)
        parent_reply_id = display_text(parent_reply_id)
        now = _utc_now()
        reply_id = secrets.token_urlsafe(18)
        with self._connect() as connection:
            topic = self._topic_row(connection, topic_id)
            if topic["status"] != "open":
                raise ForumForbidden("该主题不可回复")
            parent_author_id = ""
            if parent_reply_id:
                parent = connection.execute(
                    "SELECT reply_id, author_id FROM forum_replies WHERE reply_id = ? AND topic_id = ? AND status = 'visible'",
                    (parent_reply_id, topic_id),
                ).fetchone()
                if parent is None:
                    raise ForumNotFound("回复目标不存在")
                parent_author_id = parent["author_id"]
            connection.execute(
                "INSERT INTO forum_replies(reply_id, topic_id, author_id, author_name, body, parent_reply_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (reply_id, topic_id, self.profile_id, author_name, body, parent_reply_id or None, now, now),
            )
            connection.execute(
                "UPDATE forum_topics SET updated_at = ? WHERE topic_id = ?", (now, topic_id)
            )
            self._notify_reply(
                connection,
                reply_id=reply_id,
                topic_author_id=topic["author_id"],
                parent_author_id=parent_author_id,
                topic_title=topic["title"],
                created_at=now,
            )
            connection.commit()
        return {
            "reply_id": reply_id,
            "topic_id": topic_id,
            "author_id": self.profile_id,
            "author_name": author_name,
            "body": body,
            "parent_reply_id": parent_reply_id or None,
            "status": "visible",
            "created_at": now,
            "updated_at": now,
            "can_manage": True,
        }

    def update_reply(self, reply_id: str, *, body: object, viewer_is_admin: bool = False) -> dict[str, Any]:
        body = _text(body, field="回复", minimum=2, maximum=5000)
        with self._connect() as connection:
            row = self._reply_row(connection, reply_id)
            self._require_owner(row["author_id"], viewer_is_admin)
            if row["status"] != "visible":
                raise ForumForbidden("已删除或隐藏的回复不能编辑")
            now = _utc_now()
            connection.execute(
                "UPDATE forum_replies SET body = ?, updated_at = ? WHERE reply_id = ?",
                (body, now, reply_id),
            )
            connection.execute(
                "UPDATE forum_topics SET updated_at = ? WHERE topic_id = ?", (now, row["topic_id"])
            )
            connection.commit()
        payload = dict(row)
        payload.update({"body": body, "updated_at": now, "can_manage": True})
        return payload

    def delete_reply(self, reply_id: str, *, viewer_is_admin: bool = False) -> bool:
        with self._connect() as connection:
            row = self._reply_row(connection, reply_id)
            self._require_owner(row["author_id"], viewer_is_admin)
            cursor = connection.execute(
                "UPDATE forum_replies SET status = 'deleted', updated_at = ? WHERE reply_id = ? AND status != 'deleted'",
                (_utc_now(), reply_id),
            )
            connection.commit()
        return cursor.rowcount > 0

    def _topic_row(self, connection: sqlite3.Connection, topic_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM forum_topics WHERE topic_id = ?", (display_text(topic_id),)).fetchone()
        if row is None:
            raise ForumNotFound("论坛主题不存在")
        return row

    def _reply_row(self, connection: sqlite3.Connection, reply_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM forum_replies WHERE reply_id = ?", (display_text(reply_id),)).fetchone()
        if row is None:
            raise ForumNotFound("论坛回复不存在")
        return row

    def _require_owner(self, author_id: str, viewer_is_admin: bool) -> None:
        if author_id != self.profile_id and not viewer_is_admin:
            raise ForumForbidden("只能管理自己的论坛内容")

    def _notify_reply(
        self,
        connection: sqlite3.Connection,
        *,
        reply_id: str,
        topic_author_id: str,
        parent_author_id: str,
        topic_title: str,
        created_at: str,
    ) -> None:
        notifications_available = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'notifications'"
        ).fetchone()
        if notifications_available is None:
            return
        recipients = {
            user_id
            for user_id in (topic_author_id, parent_author_id)
            if user_id and user_id != self.profile_id
        }
        for recipient_id in recipients:
            connection.execute(
                """
                INSERT INTO notifications(
                    notification_id, profile_id, kind, actor_id,
                    comment_id, body, created_at
                ) VALUES (?, ?, 'reply', ?, ?, ?, ?)
                """,
                (
                    f"ntf_{secrets.token_hex(10)}",
                    recipient_id,
                    self.profile_id,
                    reply_id,
                    f"论坛回复：{display_text(topic_title)[:160]}",
                    created_at,
                ),
            )

    def _topic_summary(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["preview"] = display_text(row["body"])[:160]
        payload.pop("body", None)
        payload["can_manage"] = row["author_id"] == self.profile_id
        return payload

    def _topic_detail(self, row: sqlite3.Row, viewer_is_admin: bool) -> dict[str, Any]:
        payload = dict(row)
        payload["can_manage"] = row["author_id"] == self.profile_id or viewer_is_admin
        return payload

    def _reply(self, row: sqlite3.Row, viewer_is_admin: bool) -> dict[str, Any]:
        payload = dict(row)
        if row["status"] != "visible":
            payload["body"] = "该回复已被删除"
        payload["can_manage"] = row["author_id"] == self.profile_id or viewer_is_admin
        return payload

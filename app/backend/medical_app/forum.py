from __future__ import annotations

import re
import secrets
import sqlite3
import unicodedata
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

CREATE TABLE IF NOT EXISTS forum_reactions (
    target_type TEXT NOT NULL CHECK (target_type IN ('topic', 'reply')),
    target_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    reaction TEXT NOT NULL CHECK (reaction IN ('agree', 'disagree')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (target_type, target_id, profile_id)
);

CREATE TABLE IF NOT EXISTS forum_moderation_log (
    audit_id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('topic', 'reply')),
    target_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    moderator_id TEXT NOT NULL,
    moderator_name TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('edit', 'hide', 'restore', 'delete', 'purge')),
    previous_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_forum_topics_listing
ON forum_topics(section_id, status, updated_at DESC, topic_id);

CREATE INDEX IF NOT EXISTS idx_forum_replies_topic
ON forum_replies(topic_id, status, created_at, reply_id);

CREATE INDEX IF NOT EXISTS idx_forum_reactions_target
ON forum_reactions(target_type, target_id, reaction);

CREATE INDEX IF NOT EXISTS idx_forum_topics_admin
ON forum_topics(status, updated_at DESC, topic_id);

CREATE INDEX IF NOT EXISTS idx_forum_replies_admin
ON forum_replies(status, updated_at DESC, reply_id);

CREATE INDEX IF NOT EXISTS idx_forum_moderation_created
ON forum_moderation_log(created_at DESC, audit_id);

CREATE INDEX IF NOT EXISTS idx_forum_moderation_target
ON forum_moderation_log(target_type, target_id, created_at DESC);
"""

DEFAULT_SECTIONS = (
    ("study", "学习交流", "复习方法、知识梳理与备考经验", 10),
    ("questions", "题目讨论", "围绕原题、解析和易错点展开讨论", 20),
)
_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_.@-])@([\w.-]{3,32})(?![\w.-])", re.UNICODE)
_MENTION_LIMIT = 20


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
            self._ensure_purge_audit_action(connection)
            connection.executemany(
                "INSERT OR IGNORE INTO forum_sections(section_id, name, description, position) VALUES (?, ?, ?, ?)",
                DEFAULT_SECTIONS,
            )
            connection.commit()

    @staticmethod
    def _ensure_purge_audit_action(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'forum_moderation_log'"
        ).fetchone()
        if row is None or "'purge'" in str(row[0] or ""):
            return
        connection.execute(
            "ALTER TABLE forum_moderation_log RENAME TO forum_moderation_log_legacy"
        )
        connection.execute(
            """
            CREATE TABLE forum_moderation_log (
                audit_id TEXT PRIMARY KEY,
                target_type TEXT NOT NULL CHECK (target_type IN ('topic', 'reply')),
                target_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                moderator_id TEXT NOT NULL,
                moderator_name TEXT NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('edit', 'hide', 'restore', 'delete', 'purge')),
                previous_status TEXT NOT NULL,
                new_status TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO forum_moderation_log(
                audit_id, target_type, target_id, topic_id, moderator_id,
                moderator_name, action, previous_status, new_status, reason, created_at
            )
            SELECT audit_id, target_type, target_id, topic_id, moderator_id,
                   moderator_name, action, previous_status, new_status, reason, created_at
            FROM forum_moderation_log_legacy
            """
        )
        connection.execute("DROP TABLE forum_moderation_log_legacy")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_forum_moderation_created ON forum_moderation_log(created_at DESC, audit_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_forum_moderation_target ON forum_moderation_log(target_type, target_id, created_at DESC)"
        )

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
                                t.updated_at) AS last_activity,
                       (SELECT COUNT(*) FROM forum_reactions fr
                        WHERE fr.target_type = 'topic' AND fr.target_id = t.topic_id
                          AND fr.reaction = 'agree') AS agree_count,
                       (SELECT COUNT(*) FROM forum_reactions fr
                        WHERE fr.target_type = 'topic' AND fr.target_id = t.topic_id
                          AND fr.reaction = 'disagree') AS disagree_count,
                       COALESCE((SELECT fr.reaction FROM forum_reactions fr
                                 WHERE fr.target_type = 'topic' AND fr.target_id = t.topic_id
                                   AND fr.profile_id = ?), '') AS my_reaction
                FROM forum_topics t
                JOIN forum_sections s ON s.section_id = t.section_id
                WHERE {where}
                ORDER BY {ordering}
                LIMIT ? OFFSET ?
                """,
                [self.profile_id, *parameters, limit, offset],
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
                        WHERE r.topic_id = t.topic_id AND r.status = 'visible') AS reply_count,
                       (SELECT COUNT(*) FROM forum_reactions fr
                        WHERE fr.target_type = 'topic' AND fr.target_id = t.topic_id
                          AND fr.reaction = 'agree') AS agree_count,
                       (SELECT COUNT(*) FROM forum_reactions fr
                        WHERE fr.target_type = 'topic' AND fr.target_id = t.topic_id
                          AND fr.reaction = 'disagree') AS disagree_count,
                       COALESCE((SELECT fr.reaction FROM forum_reactions fr
                                 WHERE fr.target_type = 'topic' AND fr.target_id = t.topic_id
                                   AND fr.profile_id = ?), '') AS my_reaction
                FROM forum_topics t JOIN forum_sections s ON s.section_id = t.section_id
                WHERE t.topic_id = ?
                """,
                (self.profile_id, topic_id),
            ).fetchone()
            if row is None or (row["status"] != "open" and not viewer_is_admin and row["author_id"] != self.profile_id):
                raise ForumNotFound("论坛主题不存在")
            replies = connection.execute(
                """
                SELECT r.reply_id, r.topic_id, r.author_id, r.author_name, r.body,
                       r.parent_reply_id, r.status, r.created_at, r.updated_at,
                       COALESCE(p.author_name, '') AS parent_author_name,
                       (SELECT COUNT(*) FROM forum_reactions fr
                        WHERE fr.target_type = 'reply' AND fr.target_id = r.reply_id
                          AND fr.reaction = 'agree') AS agree_count,
                       (SELECT COUNT(*) FROM forum_reactions fr
                        WHERE fr.target_type = 'reply' AND fr.target_id = r.reply_id
                          AND fr.reaction = 'disagree') AS disagree_count,
                       COALESCE((SELECT fr.reaction FROM forum_reactions fr
                                 WHERE fr.target_type = 'reply' AND fr.target_id = r.reply_id
                                   AND fr.profile_id = ?), '') AS my_reaction
                FROM forum_replies r
                LEFT JOIN forum_replies p ON p.reply_id = r.parent_reply_id
                WHERE r.topic_id = ? AND (r.status = 'visible' OR r.author_id = ? OR ? = 1)
                ORDER BY r.created_at, r.rowid
                LIMIT 500
                """,
                (self.profile_id, topic_id, self.profile_id, int(viewer_is_admin)),
            ).fetchall()
        return {"topic": self._topic_detail(row, viewer_is_admin), "replies": [self._reply(row, viewer_is_admin) for row in replies]}

    def update_topic(
        self,
        topic_id: str,
        *,
        title: object,
        body: object,
        viewer_is_admin: bool = False,
        moderator_name: object = "管理员",
        moderation_reason: object = "管理员从主题详情编辑",
    ) -> dict[str, Any]:
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
            if viewer_is_admin and row["author_id"] != self.profile_id:
                self._record_moderation(
                    connection,
                    target_type="topic",
                    target_id=topic_id,
                    topic_id=topic_id,
                    moderator_name=moderator_name,
                    action="edit",
                    previous_status=row["status"],
                    new_status=row["status"],
                    reason=moderation_reason,
                    created_at=now,
                )
            connection.commit()
        return self.get_topic(topic_id, viewer_is_admin=viewer_is_admin)["topic"]

    def delete_topic(
        self,
        topic_id: str,
        *,
        viewer_is_admin: bool = False,
        moderator_name: object = "管理员",
        moderation_reason: object = "管理员从主题详情删除",
    ) -> bool:
        with self._connect() as connection:
            row = self._topic_row(connection, topic_id)
            self._require_owner(row["author_id"], viewer_is_admin)
            now = _utc_now()
            cursor = connection.execute(
                "UPDATE forum_topics SET status = 'deleted', updated_at = ? WHERE topic_id = ? AND status != 'deleted'",
                (now, topic_id),
            )
            if cursor.rowcount and viewer_is_admin and row["author_id"] != self.profile_id:
                self._record_moderation(
                    connection,
                    target_type="topic",
                    target_id=topic_id,
                    topic_id=topic_id,
                    moderator_name=moderator_name,
                    action="delete",
                    previous_status=row["status"],
                    new_status="deleted",
                    reason=moderation_reason,
                    created_at=now,
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
            notified = self._notify_reply(
                connection,
                reply_id=reply_id,
                topic_author_id=topic["author_id"],
                parent_author_id=parent_author_id,
                topic_title=topic["title"],
                created_at=now,
            )
            self._notify_mentions(
                connection,
                reply_id=reply_id,
                body=body,
                topic_title=topic["title"],
                created_at=now,
                excluded_recipients=notified,
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

    def update_reply(
        self,
        reply_id: str,
        *,
        body: object,
        viewer_is_admin: bool = False,
        moderator_name: object = "管理员",
        moderation_reason: object = "管理员从主题详情编辑",
    ) -> dict[str, Any]:
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
            if viewer_is_admin and row["author_id"] != self.profile_id:
                self._record_moderation(
                    connection,
                    target_type="reply",
                    target_id=reply_id,
                    topic_id=row["topic_id"],
                    moderator_name=moderator_name,
                    action="edit",
                    previous_status=row["status"],
                    new_status=row["status"],
                    reason=moderation_reason,
                    created_at=now,
                )
            connection.commit()
        payload = dict(row)
        payload.update({"body": body, "updated_at": now, "can_manage": True})
        return payload

    def delete_reply(
        self,
        reply_id: str,
        *,
        viewer_is_admin: bool = False,
        moderator_name: object = "管理员",
        moderation_reason: object = "管理员从主题详情删除",
    ) -> bool:
        with self._connect() as connection:
            row = self._reply_row(connection, reply_id)
            self._require_owner(row["author_id"], viewer_is_admin)
            now = _utc_now()
            cursor = connection.execute(
                "UPDATE forum_replies SET status = 'deleted', updated_at = ? WHERE reply_id = ? AND status != 'deleted'",
                (now, reply_id),
            )
            if cursor.rowcount and viewer_is_admin and row["author_id"] != self.profile_id:
                self._record_moderation(
                    connection,
                    target_type="reply",
                    target_id=reply_id,
                    topic_id=row["topic_id"],
                    moderator_name=moderator_name,
                    action="delete",
                    previous_status=row["status"],
                    new_status="deleted",
                    reason=moderation_reason,
                    created_at=now,
                )
            connection.commit()
        return cursor.rowcount > 0

    def set_reaction(
        self, target_type: str, target_id: str, reaction: object
    ) -> dict[str, Any]:
        target_type = _reaction_target(target_type)
        target_id = display_text(target_id)
        reaction = display_text(reaction).lower()
        if reaction not in {"agree", "disagree"}:
            raise ValueError("论坛反应类型无效")
        with self._connect() as connection:
            target = self._reaction_target_row(connection, target_type, target_id)
            if target["author_id"] == self.profile_id:
                raise ValueError("不能对自己的论坛内容表态")
            previous = connection.execute(
                "SELECT reaction FROM forum_reactions WHERE target_type = ? AND target_id = ? AND profile_id = ?",
                (target_type, target_id, self.profile_id),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO forum_reactions(target_type, target_id, profile_id, reaction, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(target_type, target_id, profile_id)
                DO UPDATE SET reaction = excluded.reaction, created_at = excluded.created_at
                """,
                (target_type, target_id, self.profile_id, reaction, _utc_now()),
            )
            if reaction == "agree" and (previous is None or previous["reaction"] != "agree"):
                self._notify_agree(connection, target_type, target_id, target)
            connection.commit()
            return self._reaction_payload(connection, target_type, target_id)

    def clear_reaction(self, target_type: str, target_id: str) -> dict[str, Any]:
        target_type = _reaction_target(target_type)
        target_id = display_text(target_id)
        with self._connect() as connection:
            self._reaction_target_row(connection, target_type, target_id)
            connection.execute(
                "DELETE FROM forum_reactions WHERE target_type = ? AND target_id = ? AND profile_id = ?",
                (target_type, target_id, self.profile_id),
            )
            connection.commit()
            return self._reaction_payload(connection, target_type, target_id)

    def admin_content(
        self,
        *,
        query: object = "",
        target_type: object = "",
        status: object = "",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        query = display_text(query)[:200]
        target_type = display_text(target_type).lower()
        status = display_text(status).lower()
        if target_type not in {"", "topic", "reply"}:
            raise ValueError("论坛内容类型无效")
        if status not in {"", "visible", "hidden", "deleted"}:
            raise ValueError("论坛内容状态无效")
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        content_cte = """
            WITH content AS (
                SELECT 'topic' AS target_type, t.topic_id AS target_id,
                       t.topic_id, t.section_id, s.name AS section_name,
                       t.title AS topic_title, t.author_id, t.author_name,
                       substr(t.body, 1, 600) AS preview, t.body AS search_body,
                       CASE t.status WHEN 'open' THEN 'visible' ELSE t.status END AS status,
                       t.status AS raw_status, t.status AS topic_status,
                       t.created_at, t.updated_at
                FROM forum_topics t
                JOIN forum_sections s ON s.section_id = t.section_id
                UNION ALL
                SELECT 'reply' AS target_type, r.reply_id AS target_id,
                       r.topic_id, t.section_id, s.name AS section_name,
                       t.title AS topic_title, r.author_id, r.author_name,
                       substr(r.body, 1, 600) AS preview, r.body AS search_body,
                       CASE
                         WHEN r.status = 'deleted' OR t.status = 'deleted' THEN 'deleted'
                         WHEN r.status = 'hidden' OR t.status = 'hidden' THEN 'hidden'
                         ELSE 'visible'
                       END AS status,
                       r.status AS raw_status, t.status AS topic_status,
                       r.created_at, r.updated_at
                FROM forum_replies r
                JOIN forum_topics t ON t.topic_id = r.topic_id
                JOIN forum_sections s ON s.section_id = t.section_id
            )
        """
        clauses: list[str] = []
        parameters: list[Any] = []
        if target_type:
            clauses.append("target_type = ?")
            parameters.append(target_type)
        if status:
            clauses.append("status = ?")
            parameters.append(status)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            clauses.append(
                "(topic_title LIKE ? ESCAPE '\\' OR search_body LIKE ? ESCAPE '\\' OR author_name LIKE ? ESCAPE '\\')"
            )
            parameters.extend([like, like, like])
        where = " AND ".join(clauses) or "1 = 1"
        with self._connect() as connection:
            total = connection.execute(
                f"{content_cte} SELECT COUNT(*) FROM content WHERE {where}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                {content_cte}
                SELECT * FROM content
                WHERE {where}
                ORDER BY updated_at DESC, target_id DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
            stat_rows = connection.execute(
                f"{content_cte} SELECT target_type, status, COUNT(*) AS count FROM content GROUP BY target_type, status"
            ).fetchall()
            audit_rows = connection.execute(
                """
                SELECT audit_id, target_type, target_id, topic_id,
                       moderator_id, moderator_name, action,
                       previous_status, new_status, reason, created_at
                FROM forum_moderation_log
                ORDER BY created_at DESC, rowid DESC
                LIMIT 100
                """
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.pop("search_body", None)
            item["can_restore"] = item["raw_status"] == "hidden" and (
                item["target_type"] == "topic" or item["topic_status"] == "open"
            )
            items.append(item)
        stats = {
            "total": 0,
            "topics": 0,
            "replies": 0,
            "visible": 0,
            "hidden": 0,
            "deleted": 0,
        }
        for row in stat_rows:
            count = int(row["count"])
            stats["total"] += count
            stats["topics" if row["target_type"] == "topic" else "replies"] += count
            stats[row["status"]] += count
        return {
            "query": query,
            "target_type": target_type,
            "status": status,
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "stats": stats,
            "items": items,
            "audit": [dict(row) for row in audit_rows],
        }

    def moderate_content(
        self,
        target_type: object,
        target_id: object,
        *,
        action: object,
        reason: object,
        moderator_name: object,
    ) -> dict[str, Any]:
        target_type = _reaction_target(target_type)
        target_id = display_text(target_id)
        action = display_text(action).lower()
        if action not in {"hide", "restore", "delete"}:
            raise ValueError("论坛审核操作无效")
        reason = _text(reason, field="审核原因", minimum=2, maximum=500)
        moderator_name = _text(
            moderator_name, field="管理员用户名", minimum=1, maximum=32
        )
        now = _utc_now()
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                result = self._moderate_content_in_transaction(
                    connection,
                    target_type=target_type,
                    target_id=target_id,
                    action=action,
                    reason=reason,
                    moderator_name=moderator_name,
                    now=now,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return result

    def moderate_content_bulk(
        self,
        targets: object,
        *,
        action: object,
        reason: object,
        moderator_name: object,
    ) -> dict[str, Any]:
        if not isinstance(targets, list) or not 1 <= len(targets) <= 100:
            raise ValueError("批量论坛内容必须包含 1 到 100 项")
        normalized_targets: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for value in targets:
            if not isinstance(value, dict):
                raise ValueError("批量论坛目标格式无效")
            target_type = _reaction_target(value.get("target_type"))
            target_id = display_text(value.get("target_id"))
            if not target_id or len(target_id) > 128:
                raise ValueError("批量论坛目标 ID 无效")
            key = (target_type, target_id)
            if key in seen:
                raise ValueError("批量论坛目标不能重复")
            seen.add(key)
            normalized_targets.append(
                {"target_type": target_type, "target_id": target_id}
            )
        action = display_text(action).lower()
        if action not in {"hide", "restore", "delete"}:
            raise ValueError("论坛审核操作无效")
        reason = _text(reason, field="审核原因", minimum=2, maximum=500)
        moderator_name = _text(
            moderator_name, field="管理员用户名", minimum=1, maximum=32
        )
        if action == "restore":
            normalized_targets.sort(key=lambda item: item["target_type"] != "topic")
        elif action == "delete":
            normalized_targets.sort(key=lambda item: item["target_type"] == "topic")

        now = _utc_now()
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                results = [
                    self._moderate_content_in_transaction(
                        connection,
                        target_type=item["target_type"],
                        target_id=item["target_id"],
                        action=action,
                        reason=reason,
                        moderator_name=moderator_name,
                        now=now,
                    )
                    for item in normalized_targets
                ]
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "action": action,
            "count": len(results),
            "reason": reason,
            "items": results,
        }

    def purge_content(
        self,
        target_type: object,
        target_id: object,
        *,
        reason: object,
        moderator_name: object,
    ) -> dict[str, Any]:
        target_type = _reaction_target(target_type)
        target_id = display_text(target_id)
        if not target_id or len(target_id) > 128:
            raise ValueError("论坛内容 ID 无效")
        reason = _text(reason, field="永久删除原因", minimum=2, maximum=500)
        moderator_name = _text(
            moderator_name, field="管理员用户名", minimum=1, maximum=32
        )
        now = _utc_now()
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                result = self._purge_content_in_transaction(
                    connection,
                    target_type=target_type,
                    target_id=target_id,
                    reason=reason,
                    moderator_name=moderator_name,
                    now=now,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return result

    def purge_content_bulk(
        self,
        targets: object,
        *,
        reason: object,
        moderator_name: object,
    ) -> dict[str, Any]:
        if not isinstance(targets, list) or not 1 <= len(targets) <= 100:
            raise ValueError("批量永久删除必须包含 1 到 100 项论坛内容")
        normalized_targets: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for value in targets:
            if not isinstance(value, dict):
                raise ValueError("批量论坛目标格式无效")
            target_type = _reaction_target(value.get("target_type"))
            target_id = display_text(value.get("target_id"))
            if not target_id or len(target_id) > 128:
                raise ValueError("批量论坛目标 ID 无效")
            key = (target_type, target_id)
            if key in seen:
                raise ValueError("批量论坛目标不能重复")
            seen.add(key)
            normalized_targets.append(
                {"target_type": target_type, "target_id": target_id}
            )
        reason = _text(reason, field="永久删除原因", minimum=2, maximum=500)
        moderator_name = _text(
            moderator_name, field="管理员用户名", minimum=1, maximum=32
        )
        normalized_targets.sort(key=lambda item: item["target_type"] != "reply")
        now = _utc_now()
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                results = [
                    self._purge_content_in_transaction(
                        connection,
                        target_type=item["target_type"],
                        target_id=item["target_id"],
                        reason=reason,
                        moderator_name=moderator_name,
                        now=now,
                    )
                    for item in normalized_targets
                ]
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "action": "purge",
            "count": len(results),
            "reason": reason,
            "items": results,
        }

    def _purge_content_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        target_type: str,
        target_id: str,
        reason: str,
        moderator_name: str,
        now: str,
    ) -> dict[str, Any]:
        removed_replies = 0
        if target_type == "topic":
            row = self._topic_row(connection, target_id)
            topic_id = target_id
            current = row["status"]
            reply_ids = [
                item[0]
                for item in connection.execute(
                    "SELECT reply_id FROM forum_replies WHERE topic_id = ?",
                    (target_id,),
                )
            ]
            removed_replies = len(reply_ids)
            connection.execute(
                "DELETE FROM forum_reactions WHERE target_type = 'topic' AND target_id = ?",
                (target_id,),
            )
            self._delete_target_rows(
                connection,
                table="forum_reactions",
                column="target_id",
                values=reply_ids,
                prefix="target_type = 'reply' AND ",
            )
            notifications_available = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'notifications'"
            ).fetchone()
            if notifications_available:
                connection.execute(
                    "DELETE FROM notifications WHERE comment_id = ?", (target_id,)
                )
                self._delete_target_rows(
                    connection,
                    table="notifications",
                    column="comment_id",
                    values=reply_ids,
                )
            cursor = connection.execute(
                "DELETE FROM forum_topics WHERE topic_id = ?", (target_id,)
            )
            if cursor.rowcount != 1:
                raise ForumNotFound("论坛主题不存在")
        else:
            row = self._reply_row(connection, target_id)
            topic_id = row["topic_id"]
            current = row["status"]
            connection.execute(
                "DELETE FROM forum_reactions WHERE target_type = 'reply' AND target_id = ?",
                (target_id,),
            )
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'notifications'"
            ).fetchone():
                connection.execute(
                    "DELETE FROM notifications WHERE comment_id = ?", (target_id,)
                )
            connection.execute(
                "UPDATE forum_replies SET parent_reply_id = NULL WHERE parent_reply_id = ?",
                (target_id,),
            )
            cursor = connection.execute(
                "DELETE FROM forum_replies WHERE reply_id = ?", (target_id,)
            )
            if cursor.rowcount != 1:
                raise ForumNotFound("论坛回复不存在")
        audit_id = self._record_moderation(
            connection,
            target_type=target_type,
            target_id=target_id,
            topic_id=topic_id,
            moderator_name=moderator_name,
            action="purge",
            previous_status=current,
            new_status="purged",
            reason=reason,
            created_at=now,
        )
        return {
            "audit_id": audit_id,
            "target_type": target_type,
            "target_id": target_id,
            "topic_id": topic_id,
            "action": "purge",
            "previous_status": current,
            "new_status": "purged",
            "reason": reason,
            "removed_replies": removed_replies,
            "created_at": now,
        }

    @staticmethod
    def _delete_target_rows(
        connection: sqlite3.Connection,
        *,
        table: str,
        column: str,
        values: list[str],
        prefix: str = "",
    ) -> None:
        if not values:
            return
        for index in range(0, len(values), 400):
            chunk = values[index : index + 400]
            placeholders = ",".join("?" for _ in chunk)
            connection.execute(
                f"DELETE FROM {table} WHERE {prefix}{column} IN ({placeholders})",
                chunk,
            )

    def _moderate_content_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        target_type: str,
        target_id: str,
        action: str,
        reason: str,
        moderator_name: str,
        now: str,
    ) -> dict[str, Any]:
        if target_type == "topic":
            row = self._topic_row(connection, target_id)
            topic_id = target_id
            current = row["status"]
            transitions = {
                "hide": ("open", "hidden"),
                "restore": ("hidden", "open"),
            }
            if action == "delete":
                if current == "deleted":
                    raise ForumForbidden("该主题已经删除")
                new_status = "deleted"
            else:
                expected, new_status = transitions[action]
                if current != expected:
                    raise ForumForbidden("当前主题状态不允许执行该操作")
            connection.execute(
                "UPDATE forum_topics SET status = ?, updated_at = ? WHERE topic_id = ?",
                (new_status, now, target_id),
            )
        else:
            row = connection.execute(
                """
                SELECT r.*, t.status AS topic_status
                FROM forum_replies r
                JOIN forum_topics t ON t.topic_id = r.topic_id
                WHERE r.reply_id = ?
                """,
                (target_id,),
            ).fetchone()
            if row is None:
                raise ForumNotFound("论坛回复不存在")
            topic_id = row["topic_id"]
            current = row["status"]
            if action == "delete":
                if current == "deleted":
                    raise ForumForbidden("该回复已经删除")
                new_status = "deleted"
            elif action == "hide":
                if current != "visible":
                    raise ForumForbidden("当前回复状态不允许隐藏")
                new_status = "hidden"
            else:
                if current != "hidden":
                    raise ForumForbidden("当前回复状态不允许恢复")
                if row["topic_status"] != "open":
                    raise ForumForbidden("请先恢复所属主题")
                new_status = "visible"
            connection.execute(
                "UPDATE forum_replies SET status = ?, updated_at = ? WHERE reply_id = ?",
                (new_status, now, target_id),
            )
        audit_id = self._record_moderation(
            connection,
            target_type=target_type,
            target_id=target_id,
            topic_id=topic_id,
            moderator_name=moderator_name,
            action=action,
            previous_status=current,
            new_status=new_status,
            reason=reason,
            created_at=now,
        )
        return {
            "audit_id": audit_id,
            "target_type": target_type,
            "target_id": target_id,
            "topic_id": topic_id,
            "action": action,
            "previous_status": current,
            "new_status": new_status,
            "reason": reason,
            "created_at": now,
        }

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

    def _reaction_target_row(
        self, connection: sqlite3.Connection, target_type: str, target_id: str
    ) -> sqlite3.Row:
        if target_type == "topic":
            row = self._topic_row(connection, target_id)
            if row["status"] != "open":
                raise ForumForbidden("该主题不可互动")
            return row
        row = connection.execute(
            """
            SELECT r.*, t.title AS topic_title, t.status AS topic_status
            FROM forum_replies r JOIN forum_topics t ON t.topic_id = r.topic_id
            WHERE r.reply_id = ?
            """,
            (target_id,),
        ).fetchone()
        if row is None:
            raise ForumNotFound("论坛回复不存在")
        if row["status"] != "visible" or row["topic_status"] != "open":
            raise ForumForbidden("该回复不可互动")
        return row

    def _reaction_payload(
        self, connection: sqlite3.Connection, target_type: str, target_id: str
    ) -> dict[str, Any]:
        rows = connection.execute(
            "SELECT reaction, COUNT(*) AS count FROM forum_reactions WHERE target_type = ? AND target_id = ? GROUP BY reaction",
            (target_type, target_id),
        ).fetchall()
        selected = connection.execute(
            "SELECT reaction FROM forum_reactions WHERE target_type = ? AND target_id = ? AND profile_id = ?",
            (target_type, target_id, self.profile_id),
        ).fetchone()
        payload = {
            "target_type": target_type,
            "target_id": target_id,
            "my_reaction": selected["reaction"] if selected else "",
            "agree_count": 0,
            "disagree_count": 0,
        }
        for row in rows:
            payload[f"{row['reaction']}_count"] = int(row["count"])
        return payload

    def _require_owner(self, author_id: str, viewer_is_admin: bool) -> None:
        if author_id != self.profile_id and not viewer_is_admin:
            raise ForumForbidden("只能管理自己的论坛内容")

    def _record_moderation(
        self,
        connection: sqlite3.Connection,
        *,
        target_type: str,
        target_id: str,
        topic_id: str,
        moderator_name: object,
        action: str,
        previous_status: str,
        new_status: str,
        reason: object,
        created_at: str,
    ) -> str:
        moderator_name = display_text(moderator_name)[:32] or "管理员"
        reason = display_text(reason)[:500] or "管理员操作"
        audit_id = f"aud_{secrets.token_hex(12)}"
        connection.execute(
            """
            INSERT INTO forum_moderation_log(
                audit_id, target_type, target_id, topic_id,
                moderator_id, moderator_name, action,
                previous_status, new_status, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                target_type,
                target_id,
                topic_id,
                self.profile_id,
                moderator_name,
                action,
                previous_status,
                new_status,
                reason,
                created_at,
            ),
        )
        return audit_id

    def _notify_reply(
        self,
        connection: sqlite3.Connection,
        *,
        reply_id: str,
        topic_author_id: str,
        parent_author_id: str,
        topic_title: str,
        created_at: str,
    ) -> set[str]:
        notifications_available = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'notifications'"
        ).fetchone()
        if notifications_available is None:
            return set()
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
        return recipients

    def _notify_mentions(
        self,
        connection: sqlite3.Connection,
        *,
        reply_id: str,
        body: str,
        topic_title: str,
        created_at: str,
        excluded_recipients: set[str],
    ) -> None:
        required_tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('notifications', 'users')"
            )
        }
        if required_tables != {"notifications", "users"}:
            return
        usernames: list[str] = []
        seen: set[str] = set()
        for match in _MENTION_RE.finditer(body):
            username = unicodedata.normalize("NFKC", match.group(1)).casefold()
            if username in seen:
                continue
            seen.add(username)
            usernames.append(username)
            if len(usernames) >= _MENTION_LIMIT:
                break
        if not usernames:
            return
        placeholders = ", ".join("?" for _ in usernames)
        recipients = {
            row["user_id"]
            for row in connection.execute(
                f"SELECT user_id FROM users WHERE status = 'active' AND normalized_username IN ({placeholders})",
                usernames,
            )
            if row["user_id"] != self.profile_id
            and row["user_id"] not in excluded_recipients
        }
        for recipient_id in sorted(recipients):
            connection.execute(
                """
                INSERT INTO notifications(
                    notification_id, profile_id, kind, actor_id,
                    comment_id, body, created_at
                ) VALUES (?, ?, 'mention', ?, ?, ?, ?)
                """,
                (
                    f"ntf_{secrets.token_hex(10)}",
                    recipient_id,
                    self.profile_id,
                    reply_id,
                    f"论坛提及：{display_text(topic_title)[:160]}",
                    created_at,
                ),
            )

    def _notify_agree(
        self,
        connection: sqlite3.Connection,
        target_type: str,
        target_id: str,
        target: sqlite3.Row,
    ) -> None:
        if target["author_id"] == self.profile_id:
            return
        notifications_available = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'notifications'"
        ).fetchone()
        if notifications_available is None:
            return
        label = "论坛主题" if target_type == "topic" else "论坛回复"
        title = target["title"] if target_type == "topic" else target["topic_title"]
        connection.execute(
            """
            INSERT INTO notifications(
                notification_id, profile_id, kind, actor_id,
                comment_id, body, created_at
            ) VALUES (?, ?, 'agree', ?, ?, ?, ?)
            """,
            (
                f"ntf_{secrets.token_hex(10)}",
                target["author_id"],
                self.profile_id,
                target_id,
                f"赞同了你的{label}：{display_text(title)[:140]}",
                _utc_now(),
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
        if row["status"] == "hidden":
            payload["body"] = "该回复已被管理员隐藏"
        elif row["status"] == "deleted":
            payload["body"] = "该回复已被删除"
        payload["can_manage"] = row["author_id"] == self.profile_id or viewer_is_admin
        return payload


def _reaction_target(value: object) -> str:
    target = display_text(value).lower()
    if target not in {"topic", "reply"}:
        raise ValueError("论坛互动目标无效")
    return target

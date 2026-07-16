from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .catalog import Catalog
from .normalize import display_text
from .providers import ProviderError, complete_chat_provider
from .settings import SettingsStore, SettingsUnavailable


AI_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS ai_conversations (
    conversation_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '新对话',
    context_occurrence_id INTEGER,
    context_collection_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES ai_conversations(conversation_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    citations_json TEXT NOT NULL DEFAULT '[]',
    context_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_conversations_profile
ON ai_conversations(profile_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation
ON ai_messages(conversation_id, created_at, message_id);
"""

MAX_MESSAGE_LENGTH = 8000
MAX_CONTEXT_ITEMS = 6


class AIUnavailable(RuntimeError):
    pass


class AINotFound(LookupError):
    pass


class AIAccessDenied(PermissionError):
    pass


class AIConflict(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _clean_text(value: object, *, limit: int = MAX_MESSAGE_LENGTH) -> str:
    text = display_text(value)
    if len(text) > limit:
        raise ValueError(f"内容不能超过 {limit} 个字符")
    return text


class AIStore:
    """Account-scoped conversation persistence and bounded RAG prompting."""

    def __init__(
        self,
        database_path: str | Path,
        catalog: Catalog,
        settings: SettingsStore,
        profile_id: str,
        *,
        provider_timeout: int = 90,
    ):
        self.database_path = Path(database_path).resolve()
        self.catalog = catalog
        self.settings = settings
        self.profile_id = display_text(profile_id)
        self.provider_timeout = max(10, min(int(provider_timeout), 180))

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(AI_SCHEMA_SQL)
            conversation_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(ai_conversations)")
            }
            if "context_occurrence_id" not in conversation_columns:
                connection.execute(
                    "ALTER TABLE ai_conversations ADD COLUMN context_occurrence_id INTEGER"
                )
            if "context_collection_id" not in conversation_columns:
                connection.execute(
                    "ALTER TABLE ai_conversations ADD COLUMN context_collection_id TEXT NOT NULL DEFAULT ''"
                )
            message_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(ai_messages)")
            }
            if "context_json" not in message_columns:
                connection.execute(
                    "ALTER TABLE ai_messages ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}'"
                )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def list_conversations(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.conversation_id, c.title, c.context_occurrence_id,
                       c.context_collection_id, c.created_at, c.updated_at,
                       (SELECT content FROM ai_messages m
                        WHERE m.conversation_id = c.conversation_id
                        ORDER BY m.created_at DESC, m.message_id DESC LIMIT 1) AS preview,
                       (SELECT COUNT(*) FROM ai_messages m
                        WHERE m.conversation_id = c.conversation_id) AS message_count
                FROM ai_conversations c
                WHERE c.profile_id = ?
                ORDER BY c.updated_at DESC, c.conversation_id DESC
                LIMIT 100
                """,
                (self.profile_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_conversation(
        self, title: object = "", *, context: object = None
    ) -> dict[str, Any]:
        clean_title = _clean_text(title, limit=80) or "新对话"
        context_occurrence_id, context_collection_id = self._conversation_context(context)
        now = _utc_now()
        conversation_id = secrets.token_urlsafe(18)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_conversations(
                    conversation_id, profile_id, title, context_occurrence_id,
                    context_collection_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    self.profile_id,
                    clean_title,
                    context_occurrence_id,
                    context_collection_id,
                    now,
                    now,
                ),
            )
            connection.commit()
        return {
            "conversation_id": conversation_id,
            "title": clean_title,
            "context_occurrence_id": context_occurrence_id,
            "context_collection_id": context_collection_id,
            "created_at": now,
            "updated_at": now,
            "preview": "",
            "message_count": 0,
        }

    def rename_conversation(self, conversation_id: str, title: object) -> dict[str, Any]:
        conversation_id = display_text(conversation_id)
        clean_title = _clean_text(title, limit=80)
        if not clean_title:
            raise ValueError("标题不能为空")
        with self._connect() as connection:
            row = self._owned_conversation(connection, conversation_id)
            now = _utc_now()
            connection.execute(
                "UPDATE ai_conversations SET title = ?, updated_at = ? WHERE conversation_id = ?",
                (clean_title, now, conversation_id),
            )
            connection.commit()
        row = dict(row)
        row.update({"title": clean_title, "updated_at": now})
        return row

    def delete_conversation(self, conversation_id: str) -> bool:
        conversation_id = display_text(conversation_id)
        with self._connect() as connection:
            self._owned_conversation(connection, conversation_id)
            cursor = connection.execute(
                "DELETE FROM ai_conversations WHERE conversation_id = ? AND profile_id = ?",
                (conversation_id, self.profile_id),
            )
            connection.commit()
        return cursor.rowcount > 0

    def list_messages(self, conversation_id: str) -> dict[str, Any]:
        conversation_id = display_text(conversation_id)
        with self._connect() as connection:
            conversation = self._owned_conversation(connection, conversation_id)
            rows = connection.execute(
                "SELECT message_id, role, content, citations_json, created_at FROM ai_messages WHERE conversation_id = ? AND profile_id = ? ORDER BY rowid",
                (conversation_id, self.profile_id),
            ).fetchall()
        messages = []
        for row in rows:
            try:
                citations = json.loads(row["citations_json"] or "[]")
            except (TypeError, ValueError):
                citations = []
            messages.append(
                {
                    "message_id": row["message_id"],
                    "role": row["role"],
                    "content": row["content"],
                    "citations": citations if isinstance(citations, list) else [],
                    "created_at": row["created_at"],
                }
            )
        return {"conversation": dict(conversation), "messages": messages}

    def send_message(
        self,
        conversation_id: str,
        content: object,
        *,
        context: object = None,
    ) -> dict[str, Any]:
        conversation_id = display_text(conversation_id)
        prompt = _clean_text(content)
        if not prompt:
            raise ValueError("请输入问题")
        context_payload = context if isinstance(context, dict) else {}
        with self._connect() as connection:
            conversation = self._owned_conversation(connection, conversation_id)
            history_rows = connection.execute(
                "SELECT role, content FROM ai_messages WHERE conversation_id = ? AND profile_id = ? ORDER BY rowid DESC LIMIT 12",
                (conversation_id, self.profile_id),
            ).fetchall()
        answer, citations = self._complete_answer(
            prompt, context_payload, history_rows
        )
        now = _utc_now()
        user_message = {
            "message_id": secrets.token_urlsafe(18),
            "role": "user",
            "content": prompt,
            "citations": [],
            "created_at": now,
        }
        assistant_message = {
            "message_id": secrets.token_urlsafe(18),
            "role": "assistant",
            "content": answer,
            "citations": citations,
            "created_at": now,
        }
        title = str(conversation["title"] or "新对话")
        if title == "新对话":
            title = prompt.replace("\n", " ").strip()[:32] or title
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO ai_messages(message_id, conversation_id, profile_id, role, content, citations_json, context_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_message["message_id"],
                    conversation_id,
                    self.profile_id,
                    "user",
                    prompt,
                    "[]",
                    json.dumps(context_payload, ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO ai_messages(message_id, conversation_id, profile_id, role, content, citations_json, context_json, created_at) VALUES (?, ?, ?, ?, ?, ?, '{}', ?)",
                (
                    assistant_message["message_id"],
                    conversation_id,
                    self.profile_id,
                    "assistant",
                    answer,
                    json.dumps(citations, ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                "UPDATE ai_conversations SET title = ?, updated_at = ? WHERE conversation_id = ? AND profile_id = ?",
                (title, now, conversation_id, self.profile_id),
            )
            connection.commit()
        return {"conversation": {**dict(conversation), "title": title, "updated_at": now}, "user": user_message, "assistant": assistant_message}

    def regenerate_last(self, conversation_id: str) -> dict[str, Any]:
        conversation_id = display_text(conversation_id)
        with self._connect() as connection:
            conversation = self._owned_conversation(connection, conversation_id)
            latest = connection.execute(
                """
                SELECT rowid, message_id, role, content, citations_json,
                       context_json, created_at
                FROM ai_messages
                WHERE conversation_id = ? AND profile_id = ?
                ORDER BY rowid DESC LIMIT 2
                """,
                (conversation_id, self.profile_id),
            ).fetchall()
            if (
                len(latest) != 2
                or latest[0]["role"] != "assistant"
                or latest[1]["role"] != "user"
            ):
                raise AIConflict("当前会话没有可重新生成的回答")
            assistant_row, user_row = latest
            history_rows = connection.execute(
                """
                SELECT role, content FROM ai_messages
                WHERE conversation_id = ? AND profile_id = ? AND rowid < ?
                ORDER BY rowid DESC LIMIT 12
                """,
                (conversation_id, self.profile_id, user_row["rowid"]),
            ).fetchall()
        try:
            context_payload = json.loads(user_row["context_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            context_payload = {}
        if not isinstance(context_payload, dict):
            context_payload = {}
        prompt = _clean_text(user_row["content"])
        answer, citations = self._complete_answer(
            prompt, context_payload, history_rows
        )
        now = _utc_now()
        citations_json = json.dumps(citations, ensure_ascii=False)
        with self._connect() as connection:
            self._owned_conversation(connection, conversation_id)
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE ai_messages
                SET content = ?, citations_json = ?, created_at = ?
                WHERE message_id = ? AND conversation_id = ? AND profile_id = ?
                  AND role = 'assistant' AND content = ? AND citations_json = ?
                """,
                (
                    answer,
                    citations_json,
                    now,
                    assistant_row["message_id"],
                    conversation_id,
                    self.profile_id,
                    assistant_row["content"],
                    assistant_row["citations_json"],
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise AIConflict("会话已更新，请刷新后重试")
            connection.execute(
                """
                UPDATE ai_conversations SET updated_at = ?
                WHERE conversation_id = ? AND profile_id = ?
                """,
                (now, conversation_id, self.profile_id),
            )
            connection.commit()
        assistant = {
            "message_id": assistant_row["message_id"],
            "role": "assistant",
            "content": answer,
            "citations": citations,
            "created_at": now,
        }
        return {
            "conversation": {**dict(conversation), "updated_at": now},
            "assistant": assistant,
        }

    def _complete_answer(
        self,
        prompt: str,
        context_payload: dict[str, Any],
        history_rows: list[sqlite3.Row],
    ) -> tuple[str, list[dict[str, Any]]]:
        history = []
        history_budget = 24000
        for row in reversed(history_rows):
            history_content = str(row["content"] or "")[:4000]
            if not history_content or history_budget <= 0:
                continue
            history_content = history_content[:history_budget]
            history.append({"role": row["role"], "content": history_content})
            history_budget -= len(history_content)
        rag_text, citations = self._build_context(prompt, context_payload)
        system = (
            "你是本地题库学习助手。请用简洁、准确的中文回答，优先解释推理过程，"
            "不要编造题库不存在的来源。引用题库材料时使用 [1]、[2] 这样的编号，"
            "编号必须对应用户消息返回的 citations。若材料不足，请明确说明。"
        )
        if rag_text:
            system += "\n\n可用题库材料：\n" + rag_text
        try:
            config = self.settings.provider_config("llm", include_secret=True)
            answer = complete_chat_provider(
                config,
                [
                    {"role": "system", "content": system},
                    *history,
                    {"role": "user", "content": prompt},
                ],
                timeout=self.provider_timeout,
            )
        except (ProviderError, SettingsUnavailable, ValueError) as error:
            raise AIUnavailable(str(error)) from error
        return answer, citations

    def _owned_conversation(self, connection: sqlite3.Connection, conversation_id: str) -> sqlite3.Row:
        if not conversation_id:
            raise AINotFound("会话不存在")
        row = connection.execute(
            """
            SELECT conversation_id, profile_id, title, context_occurrence_id,
                   context_collection_id, created_at, updated_at
            FROM ai_conversations WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise AINotFound("会话不存在")
        if row["profile_id"] != self.profile_id:
            raise AIAccessDenied("无权访问该会话")
        return row

    @staticmethod
    def _conversation_context(context: object) -> tuple[int | None, str]:
        if context in (None, ""):
            return None, ""
        if not isinstance(context, dict):
            raise ValueError("context must be an object")
        occurrence = context.get("occurrence_id")
        occurrence_id = None
        if occurrence not in (None, ""):
            try:
                occurrence_id = int(occurrence)
            except (TypeError, ValueError) as error:
                raise ValueError("occurrence_id is invalid") from error
            if occurrence_id <= 0:
                raise ValueError("occurrence_id is invalid")
        collection_id = display_text(context.get("collection_id"))
        if len(collection_id) > 200:
            raise ValueError("collection_id is invalid")
        return occurrence_id, collection_id

    def _build_context(self, prompt: str, context: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        scope = display_text(context.get("scope") or "catalog").lower()
        if scope not in {"question", "collection", "directory", "catalog"}:
            raise ValueError("context scope is invalid")
        questions: list[dict[str, Any]] = []
        if scope == "question" and context.get("occurrence_id") not in (None, ""):
            questions = self.catalog.get_occurrence_questions([int(context["occurrence_id"])], include_solution=True)
        elif scope == "collection" and context.get("collection_id"):
            outline = self.catalog.collection_outline(display_text(context["collection_id"]))[:MAX_CONTEXT_ITEMS]
            questions = self.catalog.get_occurrence_questions([int(item["occurrence_id"]) for item in outline], include_solution=True)
        else:
            questions = self.catalog.search(
                prompt,
                limit=MAX_CONTEXT_ITEMS,
                include_solution=True,
                search_fields=["stem", "answer", "point", "explanation"],
            )
        citations: list[dict[str, Any]] = []
        blocks: list[str] = []
        for index, question in enumerate(questions[:MAX_CONTEXT_ITEMS], start=1):
            source = (question.get("sources") or [{}])[0]
            citation = {
                "index": index,
                "question_id": question.get("question_id", ""),
                "occurrence_id": question.get("occurrence_id"),
                "source": source.get("relative_path", ""),
                "title": source.get("title", "") or source.get("relative_path", ""),
            }
            citations.append(citation)
            options = "；".join(f"{item.get('label', '')}. {item.get('text', '')}" for item in question.get("options", []))
            blocks.append(
                f"[{index}] {question.get('type_label') or question.get('question_type') or ''} {question.get('stem', '')}\n"
                f"选项：{options}\n答案：{question.get('answer', '')}\n考点：{question.get('point', '')}\n解析：{question.get('explanation', '')}\n来源：{citation['source']}"
            )
        return "\n\n".join(blocks), citations

from __future__ import annotations

from pathlib import Path

import pytest

from medical_app.ai import AIAccessDenied, AIConflict, AIStore
from medical_app.builder import build_catalog
from medical_app.catalog import Catalog


class FakeSettings:
    def provider_config(self, provider: str, *, include_secret: bool = False):
        assert provider == "llm"
        assert include_secret is True
        return {
            "api_url": "https://example.invalid/v1/chat/completions",
            "model": "test-model",
            "api_key": "test-key-123456",
        }


def _store(sample_source_tree: Path, tmp_path: Path, profile_id: str = "alice"):
    manifest = build_catalog(sample_source_tree, tmp_path / "builds")
    catalog = Catalog(tmp_path / "builds" / manifest["build_id"] / "catalog.sqlite3")
    store = AIStore(tmp_path / "state" / "app.sqlite3", catalog, FakeSettings(), profile_id)
    store.initialize()
    return store, catalog


def test_ai_conversations_are_account_scoped_and_messages_are_persisted(
    sample_source_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, catalog = _store(sample_source_tree, tmp_path)
    monkeypatch.setattr(
        "medical_app.ai.complete_chat_provider",
        lambda config, messages, **kwargs: "答案见 [1]，因为题干描述了膜转运。",
    )
    conversation = store.create_conversation()
    assert conversation["context_occurrence_id"] is None
    result = store.send_message(conversation["conversation_id"], "膜转运方式是什么？")
    assert result["assistant"]["role"] == "assistant"
    assert result["assistant"]["citations"]
    assert store.list_messages(conversation["conversation_id"])["messages"][-1]["content"].startswith("答案见")
    other = AIStore(store.database_path, catalog, FakeSettings(), "bob")
    other.initialize()
    assert other.list_conversations() == []
    with pytest.raises(AIAccessDenied):
        other.list_messages(conversation["conversation_id"])


def test_ai_collection_context_is_bounded(sample_source_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store, catalog = _store(sample_source_tree, tmp_path)
    monkeypatch.setattr(
        "medical_app.ai.complete_chat_provider",
        lambda config, messages, **kwargs: "已根据当前文件整理。",
    )
    collection = catalog.list_collections()[0]["collection_id"]
    conversation = store.create_conversation("文件复习")
    result = store.send_message(
        conversation["conversation_id"],
        "总结这份文件。",
        context={"scope": "collection", "collection_id": collection},
    )
    assert len(result["assistant"]["citations"]) <= 6
    assert result["conversation"]["title"] == "文件复习"


def test_ai_conversation_can_bind_to_one_practice_occurrence(
    sample_source_tree: Path, tmp_path: Path
):
    store, catalog = _store(sample_source_tree, tmp_path)
    collection = catalog.list_collections()[0]
    occurrence = catalog.collection_outline(collection["collection_id"])[0]
    conversation = store.create_conversation(
        "本题解析",
        context={
            "occurrence_id": occurrence["occurrence_id"],
            "collection_id": collection["collection_id"],
        },
    )
    listed = store.list_conversations()
    assert listed[0]["conversation_id"] == conversation["conversation_id"]
    assert listed[0]["context_occurrence_id"] == occurrence["occurrence_id"]
    assert listed[0]["context_collection_id"] == collection["collection_id"]
    with pytest.raises(ValueError):
        store.create_conversation(context={"occurrence_id": 0})


def test_ai_regenerate_replaces_last_answer_and_preserves_account_scope(
    sample_source_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, catalog = _store(sample_source_tree, tmp_path)
    calls = []

    def fake_provider(config, messages, **kwargs):
        calls.append(messages)
        return f"重新生成回答 {len(calls)}"

    monkeypatch.setattr("medical_app.ai.complete_chat_provider", fake_provider)
    collection = catalog.list_collections()[0]["collection_id"]
    conversation = store.create_conversation("重新生成验收")
    first = store.send_message(
        conversation["conversation_id"],
        "请总结当前文件。",
        context={"scope": "collection", "collection_id": collection},
    )
    before = store.list_messages(conversation["conversation_id"])["messages"]
    regenerated = store.regenerate_last(conversation["conversation_id"])
    after = store.list_messages(conversation["conversation_id"])["messages"]

    assert len(calls) == 2
    assert len(after) == len(before) == 2
    assert after[-1]["message_id"] == first["assistant"]["message_id"]
    assert after[-1]["content"] == "重新生成回答 2"
    assert regenerated["assistant"]["message_id"] == first["assistant"]["message_id"]
    assert calls[1][-1] == calls[0][-1]

    other = AIStore(store.database_path, catalog, FakeSettings(), "bob")
    other.initialize()
    with pytest.raises(AIAccessDenied):
        other.regenerate_last(conversation["conversation_id"])
    empty = store.create_conversation("没有回答")
    with pytest.raises(AIConflict):
        store.regenerate_last(empty["conversation_id"])


def test_ai_initialize_migrates_existing_messages_context_column(
    sample_source_tree: Path, tmp_path: Path
):
    manifest = build_catalog(sample_source_tree, tmp_path / "builds")
    catalog = Catalog(tmp_path / "builds" / manifest["build_id"] / "catalog.sqlite3")
    database = tmp_path / "state" / "app.sqlite3"
    database.parent.mkdir(parents=True)
    with database.open("wb"):
        pass
    import sqlite3

    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE ai_conversations (
                conversation_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE ai_messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                citations_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            """
        )
    AIStore(database, catalog, FakeSettings(), "alice").initialize()
    with sqlite3.connect(database) as connection:
        message_columns = {row[1] for row in connection.execute("PRAGMA table_info(ai_messages)")}
        conversation_columns = {row[1] for row in connection.execute("PRAGMA table_info(ai_conversations)")}
    assert "context_json" in message_columns
    assert "context_occurrence_id" in conversation_columns
    assert "context_collection_id" in conversation_columns

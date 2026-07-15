from __future__ import annotations

from pathlib import Path

import pytest

from medical_app.ai import AIAccessDenied, AIStore
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

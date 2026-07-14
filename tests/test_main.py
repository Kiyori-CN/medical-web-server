from __future__ import annotations

import importlib
import sys
from pathlib import Path

from medical_v2.builder import build_catalog
from medical_v2.catalog import Catalog
from medical_v2.practice import PracticeStore


def test_main_serves_public_assets_and_v2_health(
    sample_source_tree: Path, tmp_path: Path, monkeypatch
):
    manifest = build_catalog(sample_source_tree, tmp_path / "builds")
    catalog_path = tmp_path / "builds" / manifest["build_id"] / "catalog.sqlite3"
    practice_path = tmp_path / "state" / "practice.sqlite3"
    PracticeStore(practice_path, Catalog(catalog_path)).initialize()

    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("CATALOG_DB", str(catalog_path))
    monkeypatch.setenv("PRACTICE_DB", str(practice_path))
    monkeypatch.setenv("SETTINGS_DB", str(tmp_path / "state" / "settings.sqlite3"))
    monkeypatch.setenv("SETTINGS_KEY_PATH", str(tmp_path / "state" / "settings.key"))
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setenv("LEGACY_SEARCH_ENABLED", "false")
    for module_name in ("main", "config"):
        sys.modules.pop(module_name, None)
    main = importlib.import_module("main")
    client = main.app.test_client()

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.get_json()["engine"] == "sqlite-fts5"
    assert health.get_json()["legacy"] == {"enabled": False, "status": "disabled"}

    index = client.get("/")
    assert index.status_code == 200
    assert "找到原题，沿原题库路径练会它。" in index.get_data(as_text=True)
    assert client.get("/app.js").status_code == 200
    assert client.get("/api/v2/practice/health").status_code == 200
    settings = client.get("/api/v2/settings")
    assert settings.status_code == 200
    assert settings.get_json()["embedding"]["model"] == "Qwen/Qwen3-Embedding-0.6B"

    missing_ai = client.post(
        "/api/ai/chat",
        json={"messages": [{"role": "user", "content": "test"}]},
    )
    assert missing_ai.status_code == 503

    main.settings_store.update(
        {
            "providers": {
                "llm": {
                    "api_url": "https://example.invalid/v1/chat/completions",
                    "model": "test-chat-model",
                    "api_key": "sk-test-chat-secret",
                }
            }
        }
    )
    captured = {}

    class FakeResponse:
        status_code = 200
        encoding = "utf-8"

        @staticmethod
        def iter_lines(decode_unicode=True):
            assert decode_unicode is True
            return iter(
                [
                    'data: {"choices":[{"delta":{"content":"OK"}}]}',
                    "data: [DONE]",
                ]
            )

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(main.http_requests, "post", fake_post)
    chat = client.post(
        "/api/ai/chat",
        json={"messages": [{"role": "user", "content": "test"}]},
    )
    assert chat.status_code == 200
    assert "OK" in chat.get_data(as_text=True)
    assert "sk-test-chat-secret" not in chat.get_data(as_text=True)
    assert captured["url"] == "https://example.invalid/v1/chat/completions"
    assert captured["json"]["model"] == "test-chat-model"
    assert captured["headers"]["Authorization"] == "Bearer sk-test-chat-secret"

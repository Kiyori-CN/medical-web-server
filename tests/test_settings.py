from __future__ import annotations

import pytest
import requests
from flask import Flask, g

from medical_app.providers import (
    normalize_provider_base_url,
    provider_api_url,
    provider_models_url,
)
from medical_app.settings import SettingsStore
from medical_app.settings_api import create_settings_blueprint


def _app(store: SettingsStore) -> Flask:
    app = Flask(__name__)

    @app.before_request
    def authenticate():
        role = app.config.get("ROLE")
        g.current_user = {"user_id": "usr_test", "role": role} if role else None

    app.register_blueprint(
        create_settings_blueprint(
            store,
            vector_status=lambda: {"status": "unavailable"},
        )
    )
    return app


def test_settings_encrypt_keys_and_enforce_admin_role(tmp_path):
    database = tmp_path / "state" / "settings.sqlite3"
    key_path = tmp_path / "state" / "settings.key"
    store = SettingsStore(database, key_path)
    public = store.initialize()
    assert public["embedding"]["model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert public["embedding"]["api_key_configured"] is False

    updated = store.update(
        {
            "app": {"semantic_enabled": True, "default_search_mode": "hybrid"},
            "providers": {
                "embedding": {"dimensions": 512, "api_key": "sk-test-secret-value"}
            },
        }
    )
    assert updated["embedding"]["api_key_masked"].endswith("alue")
    assert "sk-test-secret-value" not in database.read_bytes().decode("latin-1")

    app = _app(store)
    client = app.test_client()
    assert client.get("/api/settings").status_code == 401
    app.config["ROLE"] = "user"
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/admin/settings").status_code == 403
    app.config["ROLE"] = "admin"
    response = client.get("/api/admin/settings")
    assert response.status_code == 200
    serialized = response.get_data(as_text=True)
    assert "sk-test-secret-value" not in serialized
    assert response.get_json()["embedding"]["api_key_configured"] is True


def test_settings_mutation_requires_csrf_and_provider_failure_hides_key(
    tmp_path, monkeypatch
):
    secret = "sk-test-never-echo-this-value"
    store = SettingsStore(
        tmp_path / "state" / "settings.sqlite3",
        tmp_path / "state" / "settings.key",
    )
    store.initialize()
    store.update({"providers": {"embedding": {"api_key": secret}}})

    def fail_request(*args, **kwargs):
        raise requests.RequestException(f"provider failed with {secret}")

    monkeypatch.setattr("medical_app.providers.requests.post", fail_request)
    app = _app(store)
    app.config["ROLE"] = "admin"
    client = app.test_client()
    assert client.post("/api/admin/settings/test", json={"provider": "embedding"}).status_code == 403
    client.set_cookie("medical_csrf", "csrf-test")
    response = client.post(
        "/api/admin/settings/test",
        headers={"X-CSRF-Token": "csrf-test"},
        json={"provider": "embedding"},
    )
    assert response.status_code == 502
    assert secret not in response.get_data(as_text=True)
    assert response.get_json() == {
        "error": "Embedding provider request failed: RequestException",
        "status": "provider_error",
    }


def test_provider_url_rejects_embedded_credentials(tmp_path):
    store = SettingsStore(
        tmp_path / "state" / "settings.sqlite3",
        tmp_path / "state" / "settings.key",
    )
    store.initialize()

    with pytest.raises(ValueError, match="must not contain credentials"):
        store.update(
            {"providers": {"embedding": {"api_url": "https://secret@example.invalid/v1/embeddings"}}}
        )
    with pytest.raises(ValueError, match="query parameters"):
        store.update(
            {"providers": {"embedding": {"api_url": "https://example.invalid/v1/embeddings?api_key=secret"}}}
        )


def test_provider_base_url_normalizes_complete_endpoints_and_derives_urls(tmp_path):
    assert normalize_provider_base_url("https://example.invalid") == "https://example.invalid/v1"
    assert normalize_provider_base_url("https://example.invalid/v1/models") == "https://example.invalid/v1"
    assert normalize_provider_base_url("https://example.invalid/openai/v1/chat/completions/") == "https://example.invalid/openai/v1"
    assert provider_models_url("https://example.invalid/v1/embeddings") == "https://example.invalid/v1/models"
    assert provider_api_url("https://example.invalid/v1/models", "embedding") == "https://example.invalid/v1/embeddings"
    assert provider_api_url("https://example.invalid/v1/chat/completions", "llm") == "https://example.invalid/v1/chat/completions"

    store = SettingsStore(
        tmp_path / "state" / "settings.sqlite3",
        tmp_path / "state" / "settings.key",
    )
    store.initialize()
    updated = store.update(
        {
            "providers": {
                "embedding": {"base_url": "https://example.invalid"},
                "llm": {"api_url": "https://gateway.invalid/openai/v1/chat/completions"},
            }
        }
    )
    assert updated["embedding"]["base_url"] == "https://example.invalid/v1"
    assert updated["embedding"]["api_url"] == "https://example.invalid/v1/embeddings"
    assert updated["embedding"]["models_url"] == "https://example.invalid/v1/models"
    assert updated["llm"]["base_url"] == "https://gateway.invalid/openai/v1"
    assert updated["llm"]["api_url"] == "https://gateway.invalid/openai/v1/chat/completions"


def test_model_discovery_accepts_unsaved_key_and_separates_provider_models(tmp_path, monkeypatch):
    store = SettingsStore(
        tmp_path / "state" / "settings.sqlite3",
        tmp_path / "state" / "settings.key",
    )
    store.initialize()

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"data": [{"id": "Qwen/Qwen3-Embedding-0.6B"}, {"id": "Qwen/Qwen2.5-7B-Instruct"}, {"id": "BAAI/bge-reranker-v2-m3"}]}

    requested_urls = []

    def fake_get(url, *args, **kwargs):
        requested_urls.append(url)
        return Response()

    monkeypatch.setattr("medical_app.providers.requests.get", fake_get)
    app = _app(store)
    app.config["ROLE"] = "admin"
    client = app.test_client()
    client.set_cookie("medical_csrf", "csrf-test")
    headers = {"X-CSRF-Token": "csrf-test"}

    embedding = client.post(
        "/api/admin/settings/models",
        headers=headers,
        json={
            "provider": "embedding",
            "base_url": "https://example.invalid",
            "api_key": "sk-unsaved-preview",
            "model": "Qwen/Qwen3-Embedding-0.6B",
            "dimensions": 1024,
        },
    )
    assert [item["id"] for item in embedding.get_json()["models"]] == [
        "Qwen/Qwen3-Embedding-0.6B"
    ]
    assert store.provider_config("embedding", include_secret=True)["api_key"] == ""
    assert requested_urls[-1] == "https://example.invalid/v1/models"

    llm = client.post(
        "/api/admin/settings/models",
        headers=headers,
        json={
            "provider": "llm",
            "api_url": "https://example.invalid/v1/chat/completions",
            "api_key": "sk-unsaved-preview",
            "model": "Qwen/Qwen2.5-7B-Instruct",
        },
    )
    assert [item["id"] for item in llm.get_json()["models"]] == [
        "Qwen/Qwen2.5-7B-Instruct"
    ]
    assert requested_urls[-1] == "https://example.invalid/v1/models"

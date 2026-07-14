from __future__ import annotations

import pytest
import requests
from flask import Flask, g

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

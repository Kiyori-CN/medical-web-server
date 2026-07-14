from __future__ import annotations

import requests
import pytest
from flask import Flask

from medical_v2.settings import SettingsStore
from medical_v2.settings_api import create_settings_blueprint


def test_settings_encrypt_keys_and_admin_api_never_returns_plaintext(tmp_path):
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
                "embedding": {
                    "dimensions": 512,
                    "api_key": "sk-test-secret-value",
                }
            },
        }
    )
    assert updated["embedding"]["api_key_masked"].endswith("alue")
    assert "sk-test-secret-value" not in database.read_bytes().decode("latin-1")
    assert store.provider_config("embedding", include_secret=True)["api_key"] == "sk-test-secret-value"

    app = Flask(__name__)
    app.register_blueprint(
        create_settings_blueprint(
            store,
            admin_token="admin-test-token",
            vector_status=lambda: {"status": "unavailable"},
        )
    )
    client = app.test_client()
    assert client.get("/api/v2/admin/settings").status_code == 401
    response = client.get(
        "/api/v2/admin/settings", headers={"X-Admin-Token": "admin-test-token"}
    )
    assert response.status_code == 200
    serialized = response.get_data(as_text=True)
    assert "sk-test-secret-value" not in serialized
    assert response.get_json()["embedding"]["api_key_configured"] is True

    public_response = client.get("/api/v2/settings")
    assert public_response.status_code == 200
    assert "api_key" not in public_response.get_data(as_text=True).replace(
        "api_key_configured", ""
    ).replace("api_key_masked", "")


def test_provider_failure_never_echoes_api_key(tmp_path, monkeypatch):
    secret = "sk-test-never-echo-this-value"
    store = SettingsStore(
        tmp_path / "state" / "settings.sqlite3",
        tmp_path / "state" / "settings.key",
    )
    store.initialize()
    store.update({"providers": {"embedding": {"api_key": secret}}})

    def fail_request(*args, **kwargs):
        raise requests.RequestException(f"provider failed with {secret}")

    monkeypatch.setattr("medical_v2.providers.requests.post", fail_request)
    app = Flask(__name__)
    app.register_blueprint(
        create_settings_blueprint(store, admin_token="admin-test-token")
    )
    response = app.test_client().post(
        "/api/v2/admin/settings/test",
        headers={"X-Admin-Token": "admin-test-token"},
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
            {
                "providers": {
                    "embedding": {
                        "api_url": "https://secret@example.invalid/v1/embeddings"
                    }
                }
            }
        )

    with pytest.raises(ValueError, match="query parameters"):
        store.update(
            {
                "providers": {
                    "embedding": {
                        "api_url": "https://example.invalid/v1/embeddings?api_key=secret"
                    }
                }
            }
        )

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from medical_app.builder import build_catalog


def _load_app(sample_source_tree: Path, tmp_path: Path, monkeypatch):
    manifest = build_catalog(sample_source_tree, tmp_path / "builds")
    catalog_path = tmp_path / "builds" / manifest["build_id"] / "catalog.sqlite3"
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("CATALOG_DB", str(catalog_path))
    monkeypatch.setenv("APP_DB", str(tmp_path / "state" / "app.sqlite3"))
    monkeypatch.setenv("SETTINGS_DB", str(tmp_path / "state" / "settings.sqlite3"))
    monkeypatch.setenv("SETTINGS_KEY_PATH", str(tmp_path / "state" / "settings.key"))
    monkeypatch.setenv("SECURE_COOKIES", "false")
    for module_name in ("main", "config"):
        sys.modules.pop(module_name, None)
    return importlib.import_module("main").app


def _csrf(client) -> dict[str, str]:
    cookie = client.get_cookie("medical_csrf")
    assert cookie is not None
    return {"X-CSRF-Token": cookie.value}


def test_main_auth_roles_health_assets_and_practice(
    sample_source_tree: Path, tmp_path: Path, monkeypatch
):
    app = _load_app(sample_source_tree, tmp_path, monkeypatch)
    client = app.test_client()

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.get_json()["catalog"]["engine"] == "sqlite-fts5"
    assert health.get_json()["setup_required"] is True
    assert client.get("/api/stats").status_code == 401

    index = client.get("/")
    assert index.status_code == 200
    html = index.get_data(as_text=True)
    assert "把检索、练习与复习" in html
    assert "找到原题，沿原题库路径练会它" not in html
    assert client.get("/app.js").status_code == 200
    assert "frame-ancestors 'none'" in index.headers["Content-Security-Policy"]

    setup = client.post(
        "/api/auth/setup",
        json={"username": "test-admin", "password": "StrongPass1!"},
    )
    assert setup.status_code == 201
    assert setup.get_json()["user"]["role"] == "admin"
    assert client.post(
        "/api/auth/setup",
        json={"username": "again", "password": "StrongPass1!"},
    ).status_code == 409

    profile = client.put(
        "/api/auth/profile",
        headers=_csrf(client),
        json={"username": "test-admin-updated", "avatar": "🩺"},
    )
    assert profile.status_code == 200
    assert profile.get_json()["user"]["avatar"] == "🩺"

    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/admin/dashboard").status_code == 200
    assert client.get("/api/admin/comments").status_code == 200
    assert client.get("/api/admin/vectors").get_json()["operation"]["status"] == "idle"
    stats = client.get("/api/stats").get_json()
    assert stats["questions"] == 5
    search = client.post(
        "/api/search",
        json={"query": "细胞膜运输", "include_solution": False},
    )
    assert search.status_code == 200
    assert "answer" not in search.get_json()["results"][0]

    created = client.post(
        "/api/practice/sessions",
        headers=_csrf(client),
        json={"count": 1, "filters": {"question_type": "单选题"}},
    )
    assert created.status_code == 201
    assert "answer" not in created.get_json()["items"][0]["question"]

    ordinary = app.test_client()
    registered = ordinary.post(
        "/api/auth/register",
        json={"username": "ordinary-user", "password": "UserStrong1!"},
    )
    assert registered.status_code == 201
    assert registered.get_json()["user"]["role"] == "user"
    assert ordinary.get("/api/admin/dashboard").status_code == 403
    assert ordinary.get("/api/admin/comments").status_code == 403
    assert ordinary.get("/api/admin/vectors").status_code == 403
    assert ordinary.get("/api/settings").status_code == 200
    wrong_password = ordinary.post(
        "/api/auth/password",
        headers=_csrf(ordinary),
        json={"current_password": "WrongPass1!", "new_password": "RenewedUser2!"},
    )
    assert wrong_password.status_code == 409
    changed_password = ordinary.post(
        "/api/auth/password",
        headers=_csrf(ordinary),
        json={"current_password": "UserStrong1!", "new_password": "RenewedUser2!"},
    )
    assert changed_password.status_code == 200
    assert ordinary.get("/api/auth/me").get_json()["authenticated"] is False
    assert ordinary.post(
        "/api/auth/login",
        json={"username": "ordinary-user", "password": "RenewedUser2!"},
    ).status_code == 200


def test_registration_can_be_disabled_by_admin(
    sample_source_tree: Path, tmp_path: Path, monkeypatch
):
    app = _load_app(sample_source_tree, tmp_path, monkeypatch)
    admin = app.test_client()
    admin.post(
        "/api/auth/setup",
        json={"username": "test-admin", "password": "StrongPass1!"},
    )
    response = admin.put(
        "/api/admin/settings",
        headers=_csrf(admin),
        json={"app": {"allow_registration": False}},
    )
    assert response.status_code == 200
    visitor = app.test_client()
    denied = visitor.post(
        "/api/auth/register",
        json={"username": "blocked-user", "password": "UserStrong1!"},
    )
    assert denied.status_code == 409
    assert "未开放注册" in denied.get_json()["error"]

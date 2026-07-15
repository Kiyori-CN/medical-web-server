from __future__ import annotations

import csv
import importlib
import io
import json
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
    assert client.put(
        "/api/auth/preferences",
        json={"privacy": {"followers": False}},
    ).status_code == 403
    assert client.put(
        "/api/auth/preferences",
        headers=_csrf(client),
        json={"privacy": {"followers": "false"}},
    ).status_code == 400
    preferences = client.put(
        "/api/auth/preferences",
        headers=_csrf(client),
        json={
            "privacy": {"followers": False, "direct_messages": False},
            "notifications": {"direct": False},
        },
    )
    assert preferences.status_code == 200
    preference_user = preferences.get_json()["user"]
    assert preference_user["privacy"]["followers"] is False
    assert preference_user["privacy"]["direct_messages"] is False
    assert preference_user["notifications"]["direct"] is False
    me = client.get("/api/auth/me").get_json()["user"]
    assert me["privacy"]["followers"] is False
    assert me["notifications"]["direct"] is False

    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/admin/dashboard").status_code == 200
    assert client.get("/api/admin/comments").status_code == 200
    assert client.get("/api/admin/vectors").get_json()["operation"]["status"] == "idle"
    personal_export = client.get("/api/study/export?format=jsonl")
    assert personal_export.status_code == 200
    assert personal_export.headers["Cache-Control"] == "private, no-store"
    assert personal_export.headers["X-Accel-Buffering"] == "no"
    stats = client.get("/api/stats").get_json()
    assert stats["questions"] == 5
    jsonl_export = client.get("/api/admin/catalog/export?format=jsonl")
    assert jsonl_export.status_code == 200
    assert jsonl_export.mimetype == "application/x-ndjson"
    assert jsonl_export.headers["Cache-Control"] == "private, no-store"
    assert jsonl_export.headers["X-Accel-Buffering"] == "no"
    assert "medical-catalog-all.jsonl" in jsonl_export.headers["Content-Disposition"]
    exported = [
        json.loads(line)
        for line in jsonl_export.get_data(as_text=True).splitlines()
        if line
    ]
    assert len(exported) == stats["question_occurrences"] == 6
    assert all(item["relative_path"] and item["options"] for item in exported if item["canonical_type"] == "single")
    assert {item["occurrence_id"] for item in exported} == set(range(1, 7))
    conflict_row = next(
        item for item in exported if item["relative_path"].endswith("重复题.xlsx")
    )
    assert conflict_row["answer"] == "B"
    assert conflict_row["explanation"].startswith("此行故意制造答案冲突")
    assert conflict_row["options"][0]["text"] == "易化扩散"

    collection_id = exported[0]["collection_id"]
    collection_export = client.get(
        f"/api/admin/catalog/export?format=jsonl&collection_id={collection_id}"
    )
    filtered_export = [
        json.loads(line)
        for line in collection_export.get_data(as_text=True).splitlines()
        if line
    ]
    assert filtered_export
    assert {item["collection_id"] for item in filtered_export} == {collection_id}

    csv_export = client.get("/api/admin/catalog/export?format=csv")
    assert csv_export.status_code == 200
    csv_rows = list(
        csv.DictReader(io.StringIO(csv_export.get_data(as_text=True).lstrip("\ufeff")))
    )
    assert len(csv_rows) == 6
    assert csv_rows[0]["option_a"]
    assert client.get("/api/admin/catalog/export?format=xlsx").status_code == 400
    app.config["MAX_CONTENT_LENGTH"] = 64
    oversized = client.post(
        "/api/admin/catalog/imports",
        headers=_csrf(client),
        data={"file": (io.BytesIO(b"x" * 200), "questions.csv")},
    )
    assert oversized.status_code == 413
    assert "18 MB" in oversized.get_json()["error"]
    app.config["MAX_CONTENT_LENGTH"] = 18 * 1024 * 1024
    import_preview = client.post(
        "/api/admin/catalog/imports",
        headers=_csrf(client),
        data={
            "file": (
                io.BytesIO("题目,答案\n人体最大的器官是,A\n".encode("utf-8")),
                "questions.csv",
            )
        },
    )
    assert import_preview.status_code == 201
    import_payload = import_preview.get_json()
    assert import_payload["status"] == "ready"
    assert import_payload["valid_rows"] == 1
    assert client.get("/api/admin/catalog/imports").get_json()["imports"][0][
        "import_id"
    ] == import_payload["import_id"]
    assert client.delete(
        f"/api/admin/catalog/imports/{import_payload['import_id']}",
        headers=_csrf(client),
        json={},
    ).status_code == 200
    forum_topic = client.post(
        "/api/forum/topics",
        headers=_csrf(client),
        json={
            "section_id": "study",
            "title": "集成测试论坛主题",
            "body": "验证论坛互动 API 的鉴权与计数。",
        },
    ).get_json()
    forum_admin = client.get("/api/admin/forum/content?limit=100")
    assert forum_admin.status_code == 200
    assert forum_admin.get_json()["stats"]["topics"] == 1
    assert forum_admin.get_json()["items"][0]["target_id"] == forum_topic["topic_id"]
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
    assert ordinary.get("/api/admin/catalog/export?format=jsonl").status_code == 403
    assert ordinary.get("/api/admin/catalog/imports").status_code == 403
    assert ordinary.get("/api/admin/forum/content").status_code == 403
    assert ordinary.patch(
        f"/api/admin/forum/content/topic/{forum_topic['topic_id']}",
        headers=_csrf(ordinary),
        json={"action": "hide", "reason": "普通用户不能审核"},
    ).status_code == 403
    assert ordinary.put(
        f"/api/forum/topics/{forum_topic['topic_id']}/reaction",
        json={"reaction": "agree"},
    ).status_code == 403
    forum_reaction = ordinary.put(
        f"/api/forum/topics/{forum_topic['topic_id']}/reaction",
        headers=_csrf(ordinary),
        json={"reaction": "agree"},
    )
    assert forum_reaction.status_code == 200
    assert forum_reaction.get_json()["agree_count"] == 1
    forum_detail = client.get(
        f"/api/forum/topics/{forum_topic['topic_id']}"
    ).get_json()
    assert forum_detail["topic"]["agree_count"] == 1
    assert client.patch(
        f"/api/admin/forum/content/topic/{forum_topic['topic_id']}",
        json={"action": "hide", "reason": "缺少 CSRF"},
    ).status_code == 403
    hidden_forum = client.patch(
        f"/api/admin/forum/content/topic/{forum_topic['topic_id']}",
        headers=_csrf(client),
        json={"action": "hide", "reason": "集成测试隐藏主题"},
    )
    assert hidden_forum.status_code == 200
    assert hidden_forum.get_json()["new_status"] == "hidden"
    assert ordinary.get(
        f"/api/forum/topics/{forum_topic['topic_id']}"
    ).status_code == 404
    restored_forum = client.patch(
        f"/api/admin/forum/content/topic/{forum_topic['topic_id']}",
        headers=_csrf(client),
        json={"action": "restore", "reason": "集成测试恢复主题"},
    )
    assert restored_forum.status_code == 200
    moderated_forum = client.get(
        "/api/admin/forum/content?target_type=topic&status=visible"
    ).get_json()
    assert moderated_forum["total"] == 1
    assert [item["action"] for item in moderated_forum["audit"][:2]] == [
        "restore",
        "hide",
    ]
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

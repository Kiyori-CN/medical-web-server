from __future__ import annotations

from test_main import _csrf, _load_app


def test_feedback_api_is_authenticated_isolated_and_admin_managed(
    sample_source_tree, tmp_path, monkeypatch
):
    app = _load_app(sample_source_tree, tmp_path, monkeypatch)
    admin = app.test_client()
    assert admin.post(
        "/api/auth/setup", json={"username": "feedback-admin", "password": "StrongPass1!"}
    ).status_code == 201
    assert admin.post(
        "/api/feedback", json={"category": "bug", "body": "缺少登录错误提示"}
    ).status_code == 403
    ordinary = app.test_client()
    assert ordinary.post(
        "/api/auth/register", json={"username": "feedback-user", "password": "UserStrong1!"}
    ).status_code == 201
    assert ordinary.post(
        "/api/feedback", headers=_csrf(ordinary), json={"category": "suggestion", "body": "希望增加学习统计"}
    ).status_code == 201
    assert ordinary.post(
        "/api/feedback", headers=_csrf(ordinary), json={"category": "invalid", "body": "无效类型"}
    ).status_code == 400
    assert ordinary.post(
        "/api/feedback", headers=_csrf(ordinary), json={"category": "bug", "body": "x"}
    ).status_code == 400
    assert ordinary.get("/api/admin/feedback").status_code == 403
    assert ordinary.patch(
        "/api/admin/feedback/fake", headers=_csrf(ordinary), json={"status": "resolved"}
    ).status_code == 403
    admin_item = admin.post(
        "/api/feedback", headers=_csrf(admin), json={"category": "content", "body": "题库内容需要校对"}
    )
    assert admin_item.status_code == 201
    feedback_id = admin_item.get_json()["feedback_id"]
    listing = admin.get("/api/admin/feedback?status=open&limit=1")
    assert listing.status_code == 200
    payload = listing.get_json()
    assert payload["total"] == 2
    assert payload["stats"] == {"total": 2, "open": 2, "resolved": 0}
    assert len(payload["items"]) == 1
    second_page = admin.get("/api/admin/feedback?status=open&limit=1&offset=1").get_json()
    assert second_page["offset"] == 1
    assert len(second_page["items"]) == 1
    resolved = admin.patch(
        f"/api/admin/feedback/{feedback_id}", headers=_csrf(admin), json={"status": "resolved"}
    )
    assert resolved.status_code == 200
    assert resolved.get_json()["status"] == "resolved"
    assert admin.patch(
        f"/api/admin/feedback/{feedback_id}", json={"status": "open"}
    ).status_code == 403
    assert admin.patch(
        f"/api/admin/feedback/{feedback_id}", headers=_csrf(admin), json={"status": "invalid"}
    ).status_code == 400
    assert admin.patch(
        "/api/admin/feedback/fb_missing_123", headers=_csrf(admin), json={"status": "open"}
    ).status_code == 404

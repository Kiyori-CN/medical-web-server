from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from .auth import admin_required, csrf_required, login_required
from .feedback import FeedbackNotFound, FeedbackStore


def create_feedback_blueprint(database_path) -> Blueprint:
    blueprint = Blueprint("feedback", __name__)
    store = FeedbackStore(database_path)
    store.initialize()

    @blueprint.errorhandler(FeedbackNotFound)
    def handle_not_found(error):
        return jsonify({"error": str(error)}), 404

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.post("/api/feedback")
    @login_required
    @csrf_required
    def create_feedback():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return jsonify(
            store.create(
                g.current_user["user_id"],
                data.get("category", "suggestion"),
                data.get("body", ""),
            )
        ), 201

    @blueprint.get("/api/admin/feedback")
    @admin_required
    def list_feedback():
        return jsonify(
            store.list_admin(
                query=request.args.get("query", ""),
                status=request.args.get("status", ""),
                limit=request.args.get("limit", 100),
                offset=request.args.get("offset", 0),
            )
        )

    @blueprint.patch("/api/admin/feedback/<feedback_id>")
    @admin_required
    @csrf_required
    def update_feedback(feedback_id: str):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return jsonify(store.update_status(feedback_id, data.get("status", "")))

    return blueprint

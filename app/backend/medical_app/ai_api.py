from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from .ai import AIAccessDenied, AIConflict, AINotFound, AIStore, AIUnavailable
from .auth import csrf_required, login_required


def create_ai_blueprint(
    database_path,
    catalog,
    settings_store,
    *,
    provider_timeout: int = 90,
) -> Blueprint:
    blueprint = Blueprint("ai", __name__, url_prefix="/api/ai")
    AIStore(database_path, catalog, settings_store, profile_id="system", provider_timeout=provider_timeout).initialize()

    def store() -> AIStore:
        return AIStore(
            database_path,
            catalog,
            settings_store,
            profile_id=g.current_user["user_id"],
            provider_timeout=provider_timeout,
        )

    @blueprint.errorhandler(AIUnavailable)
    def handle_unavailable(error):
        return jsonify({"error": str(error), "status": "ai_unavailable"}), 503

    @blueprint.errorhandler(AINotFound)
    def handle_not_found(error):
        return jsonify({"error": str(error)}), 404

    @blueprint.errorhandler(AIAccessDenied)
    def handle_forbidden(error):
        return jsonify({"error": str(error)}), 403

    @blueprint.errorhandler(AIConflict)
    def handle_conflict(error):
        return jsonify({"error": str(error)}), 409

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.get("/conversations")
    @login_required
    def conversations():
        return jsonify({"conversations": store().list_conversations()})

    @blueprint.post("/conversations")
    @login_required
    @csrf_required
    def create_conversation():
        data = _json_object()
        return jsonify(
            store().create_conversation(
                data.get("title", ""), context=data.get("context")
            )
        ), 201

    @blueprint.patch("/conversations/<conversation_id>")
    @login_required
    @csrf_required
    def rename_conversation(conversation_id: str):
        data = _json_object()
        return jsonify(store().rename_conversation(conversation_id, data.get("title", "")))

    @blueprint.delete("/conversations/<conversation_id>")
    @login_required
    @csrf_required
    def delete_conversation(conversation_id: str):
        return jsonify({"removed": store().delete_conversation(conversation_id)})

    @blueprint.get("/conversations/<conversation_id>/messages")
    @login_required
    def messages(conversation_id: str):
        return jsonify(store().list_messages(conversation_id))

    @blueprint.post("/conversations/<conversation_id>/messages")
    @login_required
    @csrf_required
    def send_message(conversation_id: str):
        data = _json_object()
        return jsonify(
            store().send_message(
                conversation_id,
                data.get("content", ""),
                context=data.get("context"),
            )
        ), 201

    @blueprint.post("/conversations/<conversation_id>/regenerate")
    @login_required
    @csrf_required
    def regenerate_message(conversation_id: str):
        return jsonify(store().regenerate_last(conversation_id))

    return blueprint


def _json_object() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return data

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from flask import Blueprint, Response, current_app, g, jsonify, request, stream_with_context

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

    @blueprint.post("/conversations/<conversation_id>/messages/stream")
    @login_required
    @csrf_required
    def stream_message(conversation_id: str):
        data = _json_object()
        ai_store = store()
        plan = ai_store.prepare_stream_message(
            conversation_id,
            data.get("content", ""),
            context=data.get("context"),
        )
        return _event_stream(ai_store.stream_prepared_message(plan))

    @blueprint.post("/conversations/<conversation_id>/regenerate")
    @login_required
    @csrf_required
    def regenerate_message(conversation_id: str):
        return jsonify(store().regenerate_last(conversation_id))

    @blueprint.post("/conversations/<conversation_id>/regenerate/stream")
    @login_required
    @csrf_required
    def stream_regenerated_message(conversation_id: str):
        ai_store = store()
        plan = ai_store.prepare_stream_regeneration(conversation_id)
        return _event_stream(ai_store.stream_prepared_regeneration(plan))

    return blueprint


def _json_object() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return data


def _event_stream(events: Iterable[tuple[str, dict[str, Any]]]) -> Response:
    @stream_with_context
    def generate():
        try:
            for event, payload in events:
                yield _sse(event, payload)
        except GeneratorExit:
            raise
        except AIUnavailable as error:
            yield _sse("error", {"status": "ai_unavailable", "error": str(error)})
        except AIConflict as error:
            yield _sse("error", {"status": "conflict", "error": str(error)})
        except AINotFound as error:
            yield _sse("error", {"status": "not_found", "error": str(error)})
        except AIAccessDenied as error:
            yield _sse("error", {"status": "forbidden", "error": str(error)})
        except ValueError as error:
            yield _sse("error", {"status": "invalid_request", "error": str(error)})
        except Exception:
            current_app.logger.exception("AI stream failed")
            yield _sse(
                "error",
                {"status": "stream_error", "error": "AI 回答意外中断，请稍后重试"},
            )

    return Response(
        generate(),
        status=200,
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "private, no-store",
            "X-Accel-Buffering": "no",
            "Vary": "Cookie",
        },
    )


def _sse(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"

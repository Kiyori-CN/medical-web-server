from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from .auth import admin_required, csrf_required, login_required
from .catalog import Catalog, CatalogUnavailable
from .practice import (
    PracticeConflict,
    PracticeForbidden,
    PracticeNotFound,
    PracticeStore,
    PracticeUnavailable,
)


def create_practice_blueprint(catalog_path, app_database_path) -> Blueprint:
    blueprint = Blueprint("practice", __name__, url_prefix="/api")
    catalog = Catalog(catalog_path)
    PracticeStore(app_database_path, catalog, profile_id="system").initialize()

    def store() -> PracticeStore:
        return PracticeStore(
            app_database_path,
            catalog,
            profile_id=g.current_user["user_id"],
        )

    @blueprint.errorhandler(CatalogUnavailable)
    @blueprint.errorhandler(PracticeUnavailable)
    def handle_unavailable(error):
        return jsonify({"error": str(error), "status": "unavailable"}), 503

    @blueprint.errorhandler(PracticeNotFound)
    def handle_not_found(error):
        return jsonify({"error": str(error)}), 404

    @blueprint.errorhandler(PracticeConflict)
    def handle_conflict(error):
        return jsonify({"error": str(error)}), 409

    @blueprint.errorhandler(PracticeForbidden)
    def handle_forbidden(error):
        return jsonify({"error": str(error)}), 403

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.get("/practice/health")
    @login_required
    def health():
        payload = store().health()
        return jsonify(payload), 200 if payload["status"] == "healthy" else 503

    @blueprint.post("/practice/sessions")
    @login_required
    @csrf_required
    def create_session():
        data = _json_object()
        filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
        session = store().create_session(
            count=data.get("count", None if data.get("collection_id") else 10),
            collection_id=data.get("collection_id", ""),
            filters=filters,
            mode=data.get("mode", "practice"),
            order_mode=data.get("order_mode", "sequential"),
            start_position=data.get("start_position", 1),
            saved_kind=_saved_kind(data.get("saved_kind", "")),
        )
        return jsonify(session), 201

    @blueprint.get("/practice/collections/<collection_id>/outline")
    @login_required
    def collection_outline(collection_id: str):
        return jsonify(store().collection_outline(collection_id))

    @blueprint.post("/practice/collections/<collection_id>/progress/reset")
    @login_required
    @csrf_required
    def reset_collection_progress(collection_id: str):
        return jsonify(store().reset_collection_progress(collection_id))

    @blueprint.get("/practice/sessions/<session_id>")
    @login_required
    def session_detail(session_id: str):
        return jsonify(store().get_session(session_id))

    @blueprint.post("/practice/sessions/<session_id>/answers")
    @login_required
    @csrf_required
    def submit_answer(session_id: str):
        data = _json_object()
        if "position" not in data or "answer" not in data:
            return jsonify({"error": "position and answer are required"}), 400
        result = store().submit_answer(
            session_id,
            position=data["position"],
            answer=data["answer"],
            elapsed_ms=data.get("elapsed_ms"),
        )
        return jsonify(result)

    @blueprint.post("/practice/sessions/<session_id>/finish")
    @login_required
    @csrf_required
    def finish_session(session_id: str):
        return jsonify(store().finish_session(session_id))

    @blueprint.get("/study/<kind>/library")
    @login_required
    def saved_library(kind: str):
        return jsonify(
            store().saved_library(_saved_kind(kind), request.args.get("parent", ""))
        )

    @blueprint.get("/study/<kind>/collections/<collection_id>/outline")
    @login_required
    def saved_outline(kind: str, collection_id: str):
        return jsonify(store().saved_outline(_saved_kind(kind), collection_id))

    @blueprint.post("/study/favorites")
    @login_required
    @csrf_required
    def add_favorite():
        data = _json_object()
        occurrence_id = data.get("occurrence_id")
        item = store().add_favorite(
            occurrence_id=int(occurrence_id) if occurrence_id not in (None, "") else None,
            question_id=data.get("question_id", ""),
        )
        return jsonify(item), 201

    @blueprint.post("/study/favorites/toggle")
    @login_required
    @csrf_required
    def toggle_favorite():
        data = _json_object()
        occurrence_id = data.get("occurrence_id")
        item = store().toggle_favorite(
            occurrence_id=int(occurrence_id) if occurrence_id not in (None, "") else None,
            question_id=data.get("question_id", ""),
        )
        return jsonify(item)

    @blueprint.get("/study/favorites/status")
    @login_required
    def favorite_status():
        raw_ids = str(request.args.get("occurrence_ids", "")).split(",")
        occurrence_ids = [int(value) for value in raw_ids if value.strip()]
        return jsonify(
            {"occurrence_ids": store().favorite_occurrence_ids(occurrence_ids)}
        )

    @blueprint.delete("/study/<kind>/<path:item_key>")
    @login_required
    @csrf_required
    def remove_saved(kind: str, item_key: str):
        removed = store().remove_saved_item(_saved_kind(kind), item_key)
        return jsonify({"removed": removed})

    @blueprint.get("/study/comments")
    @login_required
    def list_comments():
        payload = store().list_comments(
            request.args.get("occurrence_id"),
            viewer_is_admin=g.current_user.get("role") == "admin",
        )
        return jsonify(payload)

    @blueprint.post("/study/comments")
    @login_required
    @csrf_required
    def create_comment():
        data = _json_object()
        item = store().create_comment(
            data.get("occurrence_id"),
            author_name=g.current_user.get("username", ""),
            body=data.get("body"),
            parent_comment_id=data.get("parent_comment_id", ""),
        )
        return jsonify(item), 201

    @blueprint.get("/study/comments/mine")
    @login_required
    def my_comments():
        return jsonify(
            store().my_comments(
                query=request.args.get("query", ""),
                limit=request.args.get("limit", 50),
                offset=request.args.get("offset", 0),
            )
        )

    @blueprint.patch("/study/comments/<comment_id>")
    @login_required
    @csrf_required
    def update_comment(comment_id: str):
        data = _json_object()
        return jsonify(store().update_comment(comment_id, data.get("body")))

    @blueprint.delete("/study/comments/<comment_id>")
    @login_required
    @csrf_required
    def delete_comment(comment_id: str):
        removed = store().delete_comment(
            comment_id, is_admin=g.current_user.get("role") == "admin"
        )
        return jsonify({"removed": removed})

    @blueprint.get("/admin/comments")
    @admin_required
    def admin_comments():
        return jsonify(
            store().all_comments(
                query=request.args.get("query", ""),
                limit=request.args.get("limit", 100),
                offset=request.args.get("offset", 0),
            )
        )

    @blueprint.get("/study/notes")
    @login_required
    def my_notes():
        return jsonify(
            store().my_notes(
                query=request.args.get("query", ""),
                limit=request.args.get("limit", 50),
                offset=request.args.get("offset", 0),
            )
        )

    @blueprint.get("/study/notes/<int:occurrence_id>")
    @login_required
    def get_note(occurrence_id: int):
        return jsonify(store().get_note(occurrence_id))

    @blueprint.put("/study/notes/<int:occurrence_id>")
    @login_required
    @csrf_required
    def save_note(occurrence_id: int):
        data = _json_object()
        return jsonify(store().save_note(occurrence_id, data.get("body")))

    @blueprint.delete("/study/notes/<int:occurrence_id>")
    @login_required
    @csrf_required
    def delete_note(occurrence_id: int):
        return jsonify({"removed": store().delete_note(occurrence_id)})

    @blueprint.get("/study/today-summary")
    @login_required
    def today_summary():
        return jsonify(store().today_summary())

    @blueprint.get("/study/goals")
    @login_required
    def study_goal():
        return jsonify(store().study_goal())

    @blueprint.put("/study/goals")
    @login_required
    @csrf_required
    def update_study_goal():
        data = _json_object()
        return jsonify(
            store().update_study_goal(
                data.get("daily_question_target"),
                data.get("daily_duration_target_minutes"),
            )
        )

    @blueprint.get("/study/sessions")
    @login_required
    def session_history():
        return jsonify(
            store().session_history(
                status=request.args.get("status", ""),
                limit=request.args.get("limit", 20),
                offset=request.args.get("offset", 0),
            )
        )

    @blueprint.put("/study/sessions/<session_id>/archive")
    @login_required
    @csrf_required
    def set_session_archived(session_id: str):
        data = _json_object()
        return jsonify(store().set_session_archived(session_id, data.get("archived")))

    @blueprint.delete("/study/sessions/<session_id>")
    @login_required
    @csrf_required
    def delete_archived_session(session_id: str):
        return jsonify(store().delete_archived_session(session_id))

    @blueprint.delete("/study/sessions")
    @login_required
    @csrf_required
    def delete_all_archived_sessions():
        if request.args.get("status", "") != "archived":
            raise ValueError("仅支持清空已归档练习记录")
        data = _json_object()
        return jsonify(store().delete_all_archived_sessions(data.get("confirmation")))

    @blueprint.post("/study/comments/<comment_id>/reaction")
    @login_required
    @csrf_required
    def react_comment(comment_id: str):
        data = _json_object()
        return jsonify(store().react_comment(comment_id, data.get("reaction", "")))

    @blueprint.get("/study/comments/likes/mine")
    @login_required
    def my_likes():
        return jsonify(store().my_likes(limit=request.args.get("limit", 100), offset=request.args.get("offset", 0)))

    @blueprint.get("/profiles/<user_id>")
    @login_required
    def public_profile(user_id: str):
        return jsonify(store().public_profile(user_id))

    @blueprint.get("/profiles/<user_id>/connections")
    @login_required
    def profile_connections(user_id: str):
        return jsonify(
            store().profile_connections(
                user_id,
                relation=request.args.get("relation", "followers"),
                query=request.args.get("query", ""),
                limit=request.args.get("limit", 30),
                offset=request.args.get("offset", 0),
            )
        )

    @blueprint.post("/profiles/<user_id>/follow")
    @login_required
    @csrf_required
    def follow_profile(user_id: str):
        return jsonify(store().follow_user(user_id))

    @blueprint.delete("/profiles/<user_id>/follow")
    @login_required
    @csrf_required
    def unfollow_profile(user_id: str):
        return jsonify(store().unfollow_user(user_id))

    @blueprint.get("/messages")
    @login_required
    def messages():
        return jsonify(store().list_messages(request.args.get("category", "all")))

    @blueprint.get("/messages/alerts")
    @login_required
    def message_alerts():
        return jsonify(store().message_alerts(limit=request.args.get("limit", 40)))

    @blueprint.post("/messages")
    @login_required
    @csrf_required
    def send_message():
        data = _json_object()
        return jsonify(store().send_message(data.get("receiver_id", ""), data.get("body", ""))), 201

    return blueprint


def _saved_kind(value: object) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "favorite": "favorite",
        "favorites": "favorite",
        "wrong": "wrong",
        "wrongs": "wrong",
        "": "",
    }
    if normalized not in aliases:
        raise ValueError("saved kind must be favorites or wrong")
    return aliases[normalized]


def _json_object() -> dict:
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data

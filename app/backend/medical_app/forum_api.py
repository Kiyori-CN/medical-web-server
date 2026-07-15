from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from .auth import admin_required, csrf_required, login_required
from .forum import ForumForbidden, ForumNotFound, ForumStore


def create_forum_blueprint(database_path) -> Blueprint:
    blueprint = Blueprint("forum", __name__, url_prefix="/api/forum")
    ForumStore(database_path, profile_id="system").initialize()

    def store() -> ForumStore:
        return ForumStore(database_path, profile_id=g.current_user["user_id"])

    def is_admin() -> bool:
        return g.current_user.get("role") == "admin"

    @blueprint.errorhandler(ForumNotFound)
    def handle_not_found(error):
        return jsonify({"error": str(error)}), 404

    @blueprint.errorhandler(ForumForbidden)
    def handle_forbidden(error):
        return jsonify({"error": str(error)}), 403

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.get("/sections")
    @login_required
    def sections():
        return jsonify({"sections": store().sections()})

    @blueprint.get("/topics")
    @login_required
    def topics():
        return jsonify(
            store().list_topics(
                section_id=request.args.get("section", ""),
                query=request.args.get("query", ""),
                sort=request.args.get("sort", "latest"),
                limit=request.args.get("limit", 20),
                offset=request.args.get("offset", 0),
            )
        )

    @blueprint.post("/topics")
    @login_required
    @csrf_required
    def create_topic():
        data = _json_object()
        return jsonify(
            store().create_topic(
                section_id=data.get("section_id", ""),
                title=data.get("title", ""),
                body=data.get("body", ""),
                author_name=g.current_user.get("username", ""),
            )
        ), 201

    @blueprint.get("/topics/<topic_id>")
    @login_required
    def topic(topic_id: str):
        return jsonify(store().get_topic(topic_id, viewer_is_admin=is_admin()))

    @blueprint.patch("/topics/<topic_id>")
    @login_required
    @csrf_required
    def update_topic(topic_id: str):
        data = _json_object()
        return jsonify(
            store().update_topic(
                topic_id,
                title=data.get("title", ""),
                body=data.get("body", ""),
                viewer_is_admin=is_admin(),
                moderator_name=g.current_user.get("username", "管理员"),
                moderation_reason=data.get("moderation_reason", "管理员从主题详情编辑"),
            )
        )

    @blueprint.delete("/topics/<topic_id>")
    @login_required
    @csrf_required
    def delete_topic(topic_id: str):
        return jsonify(
            {
                "removed": store().delete_topic(
                    topic_id,
                    viewer_is_admin=is_admin(),
                    moderator_name=g.current_user.get("username", "管理员"),
                )
            }
        )

    @blueprint.put("/topics/<topic_id>/reaction")
    @login_required
    @csrf_required
    def react_topic(topic_id: str):
        data = _json_object()
        return jsonify(store().set_reaction("topic", topic_id, data.get("reaction", "")))

    @blueprint.delete("/topics/<topic_id>/reaction")
    @login_required
    @csrf_required
    def clear_topic_reaction(topic_id: str):
        return jsonify(store().clear_reaction("topic", topic_id))

    @blueprint.post("/topics/<topic_id>/replies")
    @login_required
    @csrf_required
    def create_reply(topic_id: str):
        data = _json_object()
        return jsonify(
            store().create_reply(
                topic_id,
                body=data.get("body", ""),
                author_name=g.current_user.get("username", ""),
                parent_reply_id=data.get("parent_reply_id", ""),
            )
        ), 201

    @blueprint.patch("/replies/<reply_id>")
    @login_required
    @csrf_required
    def update_reply(reply_id: str):
        data = _json_object()
        return jsonify(
            store().update_reply(
                reply_id,
                body=data.get("body", ""),
                viewer_is_admin=is_admin(),
                moderator_name=g.current_user.get("username", "管理员"),
                moderation_reason=data.get("moderation_reason", "管理员从主题详情编辑"),
            )
        )

    @blueprint.delete("/replies/<reply_id>")
    @login_required
    @csrf_required
    def delete_reply(reply_id: str):
        return jsonify(
            {
                "removed": store().delete_reply(
                    reply_id,
                    viewer_is_admin=is_admin(),
                    moderator_name=g.current_user.get("username", "管理员"),
                )
            }
        )

    @blueprint.put("/replies/<reply_id>/reaction")
    @login_required
    @csrf_required
    def react_reply(reply_id: str):
        data = _json_object()
        return jsonify(store().set_reaction("reply", reply_id, data.get("reaction", "")))

    @blueprint.delete("/replies/<reply_id>/reaction")
    @login_required
    @csrf_required
    def clear_reply_reaction(reply_id: str):
        return jsonify(store().clear_reaction("reply", reply_id))

    return blueprint


def create_forum_admin_blueprint(database_path) -> Blueprint:
    blueprint = Blueprint("forum_admin", __name__, url_prefix="/api/admin/forum")
    ForumStore(database_path, profile_id="system").initialize()

    def store() -> ForumStore:
        return ForumStore(database_path, profile_id=g.current_user["user_id"])

    @blueprint.errorhandler(ForumNotFound)
    def handle_not_found(error):
        return jsonify({"error": str(error)}), 404

    @blueprint.errorhandler(ForumForbidden)
    def handle_forbidden(error):
        return jsonify({"error": str(error)}), 403

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.get("/content")
    @admin_required
    def list_content():
        return jsonify(
            store().admin_content(
                query=request.args.get("query", ""),
                target_type=request.args.get("target_type", ""),
                status=request.args.get("status", ""),
                limit=request.args.get("limit", 100),
                offset=request.args.get("offset", 0),
            )
        )

    @blueprint.patch("/content/<target_type>/<target_id>")
    @admin_required
    @csrf_required
    def moderate_content(target_type: str, target_id: str):
        data = _json_object()
        return jsonify(
            store().moderate_content(
                target_type,
                target_id,
                action=data.get("action", ""),
                reason=data.get("reason", ""),
                moderator_name=g.current_user.get("username", "管理员"),
            )
        )

    return blueprint


def _json_object() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return data

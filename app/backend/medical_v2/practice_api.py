from __future__ import annotations

from flask import Blueprint, jsonify, request

from .catalog import Catalog, CatalogUnavailable
from .practice import (
    PracticeConflict,
    PracticeNotFound,
    PracticeStore,
    PracticeUnavailable,
)


def create_practice_blueprint(catalog_path, practice_path) -> Blueprint:
    blueprint = Blueprint("medical_v2_practice", __name__, url_prefix="/api/v2/practice")
    store = PracticeStore(practice_path, Catalog(catalog_path))

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

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.get("/health")
    def health():
        payload = store.health()
        return jsonify(payload), 200 if payload["status"] == "healthy" else 503

    @blueprint.post("/sessions")
    def create_session():
        data = _json_object()
        filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
        session = store.create_session(
            count=data.get("count", 10),
            collection_id=data.get("collection_id", ""),
            filters=filters,
            mode=data.get("mode", "practice"),
        )
        return jsonify(session), 201

    @blueprint.get("/sessions/<session_id>")
    def session_detail(session_id: str):
        return jsonify(store.get_session(session_id))

    @blueprint.post("/sessions/<session_id>/answers")
    def submit_answer(session_id: str):
        data = _json_object()
        if "position" not in data or "answer" not in data:
            return jsonify({"error": "position and answer are required"}), 400
        result = store.submit_answer(
            session_id,
            position=data["position"],
            answer=data["answer"],
            elapsed_ms=data.get("elapsed_ms"),
        )
        return jsonify(result)

    @blueprint.get("/wrong")
    def wrong_questions():
        rows = store.wrong_questions(
            limit=request.args.get("limit", 50),
            offset=request.args.get("offset", 0),
        )
        return jsonify({"count": len(rows), "items": rows})

    return blueprint


def _json_object() -> dict:
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data

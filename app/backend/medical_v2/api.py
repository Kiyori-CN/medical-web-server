from __future__ import annotations

from flask import Blueprint, jsonify, request

from .catalog import Catalog, CatalogUnavailable


def create_catalog_blueprint(database_path) -> Blueprint:
    blueprint = Blueprint("medical_v2", __name__, url_prefix="/api/v2")
    catalog = Catalog(database_path)

    @blueprint.errorhandler(CatalogUnavailable)
    def handle_unavailable(error):
        return jsonify({"error": str(error), "status": "unavailable"}), 503

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.get("/health")
    def health():
        payload = catalog.health()
        return jsonify(payload), 200 if payload["status"] == "healthy" else 503

    @blueprint.get("/stats")
    def stats():
        return jsonify(catalog.stats())

    @blueprint.post("/search")
    def search():
        data = _json_object()
        query = str(data.get("query", "")).strip()
        if not query:
            return jsonify({"error": "query is required"}), 400
        filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
        results = catalog.search(
            query,
            limit=data.get("limit", data.get("top_k", 10)),
            canonical_type=filters.get("question_type", data.get("question_type", "")),
            exam=filters.get("exam", data.get("exam", "")),
            collection_kind=filters.get("collection_kind", data.get("collection_kind", "")),
            subject=filters.get("subject", data.get("subject", "")),
            year=filters.get("year", data.get("year")),
            include_solution=_json_bool(data.get("include_solution"), default=True),
        )
        return jsonify({"query": query, "count": len(results), "results": results})

    @blueprint.get("/questions/<question_id>")
    def question_detail(question_id: str):
        question = catalog.get_question(
            question_id,
            include_solution=_bool_arg(request.args.get("include_solution"), default=True),
        )
        if question is None:
            return jsonify({"error": "question not found"}), 404
        return jsonify(question)

    @blueprint.get("/collections")
    def collections():
        year = request.args.get("year")
        rows = catalog.list_collections(
            exam=request.args.get("exam", ""),
            collection_kind=request.args.get("collection_kind", ""),
            subject=request.args.get("subject", ""),
            year=int(year) if year not in (None, "") else None,
            limit=request.args.get("limit", 200),
            offset=request.args.get("offset", 0),
        )
        return jsonify({"count": len(rows), "collections": rows})

    @blueprint.get("/collections/<collection_id>/questions")
    def collection_questions(collection_id: str):
        rows = catalog.list_collection_questions(
            collection_id,
            limit=request.args.get("limit", 50),
            offset=request.args.get("offset", 0),
            include_solution=_bool_arg(request.args.get("include_solution"), default=False),
        )
        return jsonify({"count": len(rows), "questions": rows})

    return blueprint


def _bool_arg(value, *, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _json_object() -> dict:
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _json_bool(value, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError("include_solution must be a JSON boolean")
    return value

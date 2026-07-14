from __future__ import annotations

from flask import Blueprint, jsonify, request

from .auth import login_required
from .catalog import Catalog, CatalogUnavailable, normalize_search_fields
from .semantic import SemanticSearchService, SemanticSearchUnavailable


def create_catalog_blueprint(
    database_path, semantic_service: SemanticSearchService | None = None
) -> Blueprint:
    blueprint = Blueprint("catalog", __name__, url_prefix="/api")
    catalog = Catalog(database_path)

    @blueprint.errorhandler(CatalogUnavailable)
    def handle_unavailable(error):
        return jsonify({"error": str(error), "status": "unavailable"}), 503

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.errorhandler(SemanticSearchUnavailable)
    def handle_semantic_unavailable(error):
        return jsonify({"error": str(error), "status": "semantic_unavailable"}), 503

    @blueprint.get("/catalog/health")
    def health():
        payload = catalog.health()
        return jsonify(payload), 200 if payload["status"] == "healthy" else 503

    @blueprint.get("/stats")
    @login_required
    def stats():
        payload = catalog.stats()
        return jsonify(
            {
                **payload["counts"],
                "by_type": payload["by_type"],
                "by_question_type": payload["by_question_type"],
                "by_exam": payload["by_exam"],
            }
        )

    @blueprint.post("/search")
    @login_required
    def search():
        data = _json_object()
        query = str(data.get("query", "")).strip()
        if not query:
            return jsonify({"error": "query is required"}), 400
        filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
        question_types = _json_string_list(
            filters.get("question_types", data.get("question_types")),
            field="question_types",
        )
        merged_filters = {
            "question_type": filters.get("question_type", data.get("question_type", "")),
            "question_types": question_types,
            "exam": filters.get("exam", data.get("exam", "")),
            "collection_kind": filters.get(
                "collection_kind", data.get("collection_kind", "")
            ),
            "subject": filters.get("subject", data.get("subject", "")),
            "year": filters.get("year", data.get("year")),
        }
        limit = data.get("limit", data.get("top_k", 10))
        mode = str(data.get("mode", data.get("search_mode", "keyword"))).strip().lower()
        include_solution = _json_bool(data.get("include_solution"), default=True)
        search_fields = normalize_search_fields(data.get("search_fields"))
        if semantic_service is None:
            if mode != "keyword":
                raise SemanticSearchUnavailable("Semantic search service is not configured")
            results = catalog.search(
                query,
                limit=limit,
                include_solution=include_solution,
                search_fields=search_fields,
                canonical_type=merged_filters["question_type"],
                question_types=merged_filters["question_types"],
                exam=merged_filters["exam"],
                collection_kind=merged_filters["collection_kind"],
                subject=merged_filters["subject"],
                year=merged_filters["year"],
            )
            metadata = {"mode": "keyword"}
        else:
            results, metadata = semantic_service.search(
                query,
                mode=mode,
                limit=limit,
                include_solution=include_solution,
                filters=merged_filters,
                search_fields=search_fields,
            )
        return jsonify(
            {
                "query": query,
                "count": len(results),
                "results": results,
                "search_fields": search_fields,
                "question_types": question_types,
                **metadata,
            }
        )

    @blueprint.get("/questions/<question_id>")
    @login_required
    def question_detail(question_id: str):
        question = catalog.get_question(
            question_id,
            include_solution=_bool_arg(request.args.get("include_solution"), default=True),
        )
        if question is None:
            return jsonify({"error": "question not found"}), 404
        return jsonify(question)

    @blueprint.get("/collections")
    @login_required
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

    @blueprint.get("/collections/<collection_id>")
    @login_required
    def collection_detail(collection_id: str):
        collection = catalog.get_collection(collection_id)
        if collection is None:
            return jsonify({"error": "collection not found"}), 404
        return jsonify(collection)

    @blueprint.get("/library")
    @login_required
    def library():
        return jsonify(catalog.browse_library(request.args.get("parent", "")))

    @blueprint.get("/collections/<collection_id>/questions")
    @login_required
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


def _json_string_list(value: object, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON array")
    values = list(dict.fromkeys(str(item or "").strip() for item in value))
    values = [item for item in values if item]
    if len(values) > 50:
        raise ValueError(f"{field} contains too many values")
    return values


def _json_bool(value, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError("include_solution must be a JSON boolean")
    return value

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Blueprint, jsonify, request

from .auth import admin_required, csrf_required, login_required
from .providers import EmbeddingClient, ProviderError, test_chat_provider
from .settings import SettingsStore, SettingsUnavailable


def create_settings_blueprint(
    store: SettingsStore,
    *,
    vector_status: Callable[[], dict[str, Any]] | None = None,
    provider_timeout: int = 30,
) -> Blueprint:
    blueprint = Blueprint("settings", __name__, url_prefix="/api")

    @blueprint.errorhandler(SettingsUnavailable)
    def handle_unavailable(error):
        return jsonify({"error": str(error), "status": "unavailable"}), 503

    @blueprint.errorhandler(ProviderError)
    def handle_provider_error(error):
        return jsonify({"error": str(error), "status": "provider_error"}), 502

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.get("/settings")
    @login_required
    def public_settings():
        payload = store.public_settings()
        payload["vector_index"] = vector_status() if vector_status else {"status": "unavailable"}
        return jsonify(payload)

    @blueprint.get("/admin/settings")
    @admin_required
    def admin_settings():
        payload = store.admin_settings()
        payload["vector_index"] = vector_status() if vector_status else {"status": "unavailable"}
        return jsonify(payload)

    @blueprint.put("/admin/settings")
    @admin_required
    @csrf_required
    def update_settings():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        payload = store.update(data)
        payload["vector_index"] = vector_status() if vector_status else {"status": "unavailable"}
        return jsonify(payload)

    @blueprint.post("/admin/settings/test")
    @admin_required
    @csrf_required
    def test_provider():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        provider = str(data.get("provider", "")).strip().lower()
        config = store.provider_config(provider, include_secret=True)
        if provider == "embedding":
            vectors = EmbeddingClient(
                api_url=config["api_url"],
                api_key=config["api_key"],
                model=config["model"],
                dimensions=config["dimensions"],
                timeout=provider_timeout,
            ).embed(["医学题库嵌入模型连接测试"])
            return jsonify(
                {
                    "status": "ok",
                    "provider": provider,
                    "model": config["model"],
                    "dimensions": int(vectors.shape[1]),
                }
            )
        if provider == "llm":
            return jsonify(test_chat_provider(config, timeout=provider_timeout))
        raise ValueError("provider must be embedding or llm")

    return blueprint

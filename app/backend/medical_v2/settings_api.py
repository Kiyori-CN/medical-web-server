from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

from flask import Blueprint, jsonify, request

from .providers import EmbeddingClient, ProviderError, test_chat_provider
from .settings import SettingsStore, SettingsUnavailable


def create_settings_blueprint(
    store: SettingsStore,
    *,
    admin_token: str,
    vector_status: Callable[[], dict[str, Any]] | None = None,
    provider_timeout: int = 30,
) -> Blueprint:
    blueprint = Blueprint("medical_v2_settings", __name__, url_prefix="/api/v2")

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
    def public_settings():
        payload = store.public_settings()
        payload["admin_enabled"] = bool(admin_token)
        payload["vector_index"] = vector_status() if vector_status else {"status": "unavailable"}
        return jsonify(payload)

    @blueprint.get("/admin/settings")
    def admin_settings():
        denied = _authorize(admin_token)
        if denied:
            return denied
        payload = store.admin_settings()
        payload["vector_index"] = vector_status() if vector_status else {"status": "unavailable"}
        return jsonify(payload)

    @blueprint.put("/admin/settings")
    def update_settings():
        denied = _authorize(admin_token)
        if denied:
            return denied
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return jsonify(store.update(data))

    @blueprint.post("/admin/settings/test")
    def test_provider():
        denied = _authorize(admin_token)
        if denied:
            return denied
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


def _authorize(admin_token: str):
    if not admin_token:
        return jsonify({"error": "Admin settings are disabled"}), 503
    provided = request.headers.get("X-Admin-Token", "")
    if not provided or not hmac.compare_digest(provided, admin_token):
        return jsonify({"error": "Admin authorization required"}), 401
    return None

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

from flask import Blueprint, jsonify, request, send_file

from .auth import admin_required, csrf_required, login_required
from .providers import (
    EmbeddingClient,
    ProviderError,
    list_provider_models,
    normalize_provider_base_url,
    provider_api_url,
    provider_models_url,
    test_chat_provider,
)
from .settings import (
    LOGIN_BACKGROUND_MAX_BYTES,
    SettingsStore,
    SettingsUnavailable,
)


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

    @blueprint.get("/site")
    def site_settings():
        response = jsonify(store.public_site_settings())
        response.headers["Cache-Control"] = "no-cache"
        return response

    @blueprint.get("/site/login-background")
    def login_background():
        asset = store.login_background()
        if asset is None:
            return jsonify({"error": "登录背景图片不存在"}), 404
        immutable = request.args.get("v", "") == asset["sha256"][:20]
        response = send_file(
            io.BytesIO(asset["content"]),
            mimetype=asset["media_type"],
            download_name=f"login-background-{asset['sha256'][:20]}.webp",
            conditional=True,
            etag=asset["sha256"],
            max_age=31536000 if immutable else 0,
        )
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable"
            if immutable
            else "public, no-cache"
        )
        return response

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

    @blueprint.post("/admin/settings/login-background")
    @admin_required
    @csrf_required
    def update_login_background():
        upload = request.files.get("background")
        if upload is None or not upload.filename:
            raise ValueError("请选择登录背景图片")
        payload = upload.stream.read(LOGIN_BACKGROUND_MAX_BYTES + 1)
        site = store.set_login_background(payload)
        return jsonify({"site": site})

    @blueprint.delete("/admin/settings/login-background")
    @admin_required
    @csrf_required
    def delete_login_background():
        removed = store.delete_login_background()
        return jsonify({"removed": removed, "site": store.site_settings()})

    @blueprint.post("/admin/settings/test")
    @admin_required
    @csrf_required
    def test_provider():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        provider = str(data.get("provider", "")).strip().lower()
        config = _preview_provider_config(store, provider, data)
        if provider == "embedding":
            vectors = EmbeddingClient(
                api_url=config["api_url"],
                api_key=config["api_key"],
                model=config["model"],
                dimensions=config["dimensions"],
                timeout=provider_timeout,
            ).embed(["题库嵌入模型连接测试"])
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

    @blueprint.post("/admin/settings/models")
    @admin_required
    @csrf_required
    def discover_models():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        provider = str(data.get("provider", "")).strip().lower()
        config = _preview_provider_config(store, provider, data)
        models = list_provider_models(
            api_url=config["api_url"],
            api_key=config["api_key"],
            provider=provider,
            timeout=provider_timeout,
        )
        return jsonify({"provider": provider, "count": len(models), "models": models})

    return blueprint


def _preview_provider_config(
    store: SettingsStore, provider: str, payload: dict[str, Any]
) -> dict[str, Any]:
    if provider not in {"embedding", "llm"}:
        raise ValueError("provider must be embedding or llm")
    config = store.provider_config(provider, include_secret=True)
    configured_url = payload.get("base_url") or payload.get("api_url") or config["base_url"]
    base_url = normalize_provider_base_url(str(configured_url).strip())
    api_url = provider_api_url(base_url, provider)
    model = str(payload.get("model") or config["model"]).strip()
    api_key = payload.get("api_key")
    if api_key in (None, ""):
        api_key = config.get("api_key", "")
    if not isinstance(api_key, str) or len(api_key.strip()) < 8:
        raise ProviderError("API key is not configured")
    if not model or len(model) > 200:
        raise ValueError("model must be between 1 and 200 characters")
    config.update(
        {
            "base_url": base_url,
            "api_url": api_url,
            "models_url": provider_models_url(base_url),
            "model": model,
            "api_key": api_key.strip(),
        }
    )
    if provider == "embedding" and payload.get("dimensions") not in (None, ""):
        dimensions = int(payload["dimensions"])
        if dimensions not in {64, 128, 256, 512, 768, 1024}:
            raise ValueError("embedding dimensions are invalid")
        config["dimensions"] = dimensions
    return config

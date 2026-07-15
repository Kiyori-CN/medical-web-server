from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("FLASK_ENV", "production")

from flask import Flask, jsonify, send_from_directory

from config import get_config
from medical_app.api import create_catalog_blueprint
from medical_app.auth import (
    AuthStore,
    admin_required,
    create_auth_blueprint,
    create_user_admin_blueprint,
    install_auth,
)
from medical_app.catalog import Catalog, CatalogUnavailable, resolve_catalog_path
from medical_app.practice import PracticeStore
from medical_app.practice_api import create_practice_blueprint
from medical_app.semantic import SemanticSearchService
from medical_app.settings import SettingsStore
from medical_app.settings_api import create_settings_blueprint
from medical_app.vector_admin import VectorOperationManager, create_vector_admin_blueprint


def create_app() -> Flask:
    config = get_config()
    application = Flask(__name__)
    application.config.update(
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        JSON_AS_ASCII=False,
    )

    frontend_dir = Path(config.FRONTEND_DIR).resolve()
    catalog_path = resolve_catalog_path(config.CATALOG_DB, config.KNOWLEDGE_BASE_ROOT)
    catalog = Catalog(catalog_path)

    settings_store = SettingsStore(config.SETTINGS_DB, config.SETTINGS_KEY_PATH)
    settings_store.initialize()
    auth_store = AuthStore(config.APP_DB)
    auth_store.initialize()
    PracticeStore(config.APP_DB, catalog, profile_id="system").initialize()

    semantic_service = SemanticSearchService(
        catalog,
        settings_store,
        config.KNOWLEDGE_BASE_ROOT,
        provider_timeout=config.PROVIDER_TIMEOUT,
    )
    vector_operations = VectorOperationManager(
        catalog_database=catalog_path,
        knowledge_base_root=config.KNOWLEDGE_BASE_ROOT,
        settings_store=settings_store,
        state_root=Path(config.APP_DB).resolve().parent,
        vector_status=semantic_service.status,
    )

    install_auth(application, auth_store)
    application.register_blueprint(
        create_auth_blueprint(
            auth_store,
            registration_enabled=lambda: bool(
                settings_store.public_settings().get("allow_registration", True)
            ),
            secure_cookies=config.SECURE_COOKIES,
        )
    )
    application.register_blueprint(create_vector_admin_blueprint(vector_operations))
    application.register_blueprint(create_user_admin_blueprint(auth_store))
    application.register_blueprint(create_catalog_blueprint(catalog_path, semantic_service))
    application.register_blueprint(
        create_practice_blueprint(catalog_path, config.APP_DB)
    )
    application.register_blueprint(
        create_settings_blueprint(
            settings_store,
            vector_status=semantic_service.status,
            provider_timeout=config.PROVIDER_TIMEOUT,
        )
    )

    if config.CORS_ORIGINS:
        from flask_cors import CORS

        origins = [
            value.strip() for value in config.CORS_ORIGINS.split(",") if value.strip()
        ]
        CORS(application, origins=origins, supports_credentials=True)

    @application.get("/api/health")
    def health():
        try:
            catalog_status = catalog.health()
        except (CatalogUnavailable, OSError, ValueError) as error:
            return (
                jsonify(
                    {
                        "status": "unhealthy",
                        "catalog": {"status": "unavailable", "message": str(error)},
                        "accounts": auth_store.stats(),
                        "setup_required": not auth_store.has_users(),
                        "vector_index": semantic_service.status(),
                    }
                ),
                503,
            )
        return jsonify(
            {
                "status": "healthy",
                "catalog": catalog_status,
                "accounts": auth_store.stats(),
                "setup_required": not auth_store.has_users(),
                "vector_index": semantic_service.status(),
            }
        )

    @application.get("/api/admin/dashboard")
    @admin_required
    def admin_dashboard():
        practice_store = PracticeStore(config.APP_DB, catalog, profile_id="system")
        catalog_stats = catalog.stats()
        return jsonify(
            {
                "accounts": auth_store.stats(),
                "activity": practice_store.dashboard_stats(),
                "catalog": {
                    **catalog_stats["counts"],
                    "by_type": catalog_stats["by_type"],
                    "by_question_type": catalog_stats["by_question_type"],
                    "by_exam": catalog_stats["by_exam"],
                },
                "vector_index": semantic_service.status(),
            }
        )

    @application.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; font-src 'self'; frame-ancestors 'none'",
        )
        if response.mimetype == "text/html":
            response.headers["Cache-Control"] = "no-cache"
        return response

    @application.get("/")
    def index():
        return send_from_directory(frontend_dir, "index.html")

    @application.get("/<path:path>")
    def static_files(path: str):
        if path.startswith("api/"):
            return jsonify({"error": "API endpoint not found"}), 404
        candidate = (frontend_dir / path).resolve()
        if candidate.is_file() and frontend_dir in candidate.parents:
            return send_from_directory(frontend_dir, path)
        return send_from_directory(frontend_dir, "index.html")

    return application


app = create_app()


if __name__ == "__main__":
    runtime_config = get_config()
    app.run(
        host=runtime_config.HOST,
        port=runtime_config.PORT,
        debug=runtime_config.DEBUG,
        use_reloader=runtime_config.DEVELOPMENT,
    )

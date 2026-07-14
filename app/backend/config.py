from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = Path("/srv/projects/medical-web-server")


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    ENVIRONMENT = os.getenv("FLASK_ENV", "production").lower()
    DEVELOPMENT = ENVIRONMENT == "development"

    KNOWLEDGE_BASE_ROOT = os.getenv(
        "KNOWLEDGE_BASE_ROOT",
        str(REPO_ROOT / "knowledge-base") if DEVELOPMENT else str(SERVER_ROOT / "knowledge-base"),
    )
    CATALOG_DB = os.getenv("CATALOG_DB", "")
    APP_DB = os.getenv(
        "APP_DB",
        str(REPO_ROOT / "state" / "app.sqlite3")
        if DEVELOPMENT
        else str(SERVER_ROOT / "state" / "app.sqlite3"),
    )
    SETTINGS_DB = os.getenv(
        "SETTINGS_DB",
        str(REPO_ROOT / "state" / "settings.sqlite3")
        if DEVELOPMENT
        else str(SERVER_ROOT / "state" / "settings.sqlite3"),
    )
    SETTINGS_KEY_PATH = os.getenv(
        "SETTINGS_KEY_PATH",
        str(REPO_ROOT / "state" / "settings.key")
        if DEVELOPMENT
        else str(SERVER_ROOT / "state" / "settings.key"),
    )
    FRONTEND_DIR = os.getenv("FRONTEND_DIR", str(REPO_ROOT / "public"))

    HOST = os.getenv("FLASK_HOST", "127.0.0.1" if DEVELOPMENT else "0.0.0.0")
    PORT = int(os.getenv("FLASK_PORT", "5001" if DEVELOPMENT else "8080"))
    DEBUG = _boolean("FLASK_DEBUG", DEVELOPMENT)
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "")
    SECURE_COOKIES = _boolean("SECURE_COOKIES", not DEVELOPMENT)
    PROVIDER_TIMEOUT = max(5, min(int(os.getenv("PROVIDER_TIMEOUT", "60")), 180))


def get_config() -> Config:
    return Config()

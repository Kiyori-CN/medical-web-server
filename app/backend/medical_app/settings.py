from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .normalize import display_text
from .providers import validate_provider_url


SETTINGS_SCHEMA_REVISION = 1
DEFAULT_EMBEDDING_API_URL = "https://api.siliconflow.cn/v1/embeddings"
DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_EMBEDDING_DIMENSIONS = 1024
DEFAULT_LLM_API_URL = "https://api.siliconflow.cn/v1/chat/completions"
DEFAULT_LLM_MODEL = "Qwen/Qwen2.5-7B-Instruct"


class SettingsUnavailable(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class SettingsStore:
    def __init__(self, database_path: str | Path, key_path: str | Path | None = None):
        self.database_path = Path(database_path).resolve()
        self.key_path = (
            Path(key_path).resolve()
            if key_path
            else self.database_path.with_name("settings.key")
        )

    def initialize(self) -> dict[str, Any]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_key()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_settings (
                    provider TEXT PRIMARY KEY,
                    api_url TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dimensions INTEGER,
                    api_key_encrypted BLOB,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO settings_meta(key, value) VALUES ('schema_revision', ?)",
                (str(SETTINGS_SCHEMA_REVISION),),
            )
            defaults = {
                "semantic_enabled": False,
                "default_search_mode": "keyword",
                "search_result_limit": 10,
                "allow_registration": True,
                "site_name": "医考知库",
            }
            for key, value in defaults.items():
                connection.execute(
                    """
                    INSERT OR IGNORE INTO app_settings(key, value_json, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (key, json.dumps(value), _utc_now()),
                )
            for provider, api_url, model, dimensions in (
                (
                    "embedding",
                    DEFAULT_EMBEDDING_API_URL,
                    DEFAULT_EMBEDDING_MODEL,
                    DEFAULT_EMBEDDING_DIMENSIONS,
                ),
                ("llm", DEFAULT_LLM_API_URL, DEFAULT_LLM_MODEL, None),
            ):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO provider_settings(
                        provider, api_url, model, dimensions, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (provider, api_url, model, dimensions, _utc_now()),
                )
            connection.commit()
        finally:
            connection.close()
        self._chmod_private(self.database_path)
        return self.public_settings()

    def connect(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise SettingsUnavailable("Settings database is not initialized")
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def public_settings(self) -> dict[str, Any]:
        app = self._app_settings()
        embedding = self.provider_config("embedding", include_secret=False)
        llm = self.provider_config("llm", include_secret=False)
        return {
            "semantic_enabled": bool(app["semantic_enabled"]),
            "default_search_mode": app["default_search_mode"],
            "search_result_limit": int(app["search_result_limit"]),
            "allow_registration": bool(app["allow_registration"]),
            "site_name": str(app["site_name"]),
            "embedding": embedding,
            "llm": llm,
        }

    def admin_settings(self) -> dict[str, Any]:
        return self.public_settings()

    def provider_config(self, provider: str, *, include_secret: bool = False) -> dict[str, Any]:
        provider = display_text(provider).lower()
        if provider not in {"embedding", "llm"}:
            raise ValueError("provider must be embedding or llm")
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT * FROM provider_settings WHERE provider = ?", (provider,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise SettingsUnavailable(f"Provider is not configured: {provider}")
        encrypted = row["api_key_encrypted"]
        result = {
            "provider": provider,
            "api_url": row["api_url"],
            "model": row["model"],
            "dimensions": row["dimensions"],
            "api_key_configured": bool(encrypted),
            "api_key_masked": self._masked_key(encrypted),
            "updated_at": row["updated_at"],
        }
        if include_secret:
            result["api_key"] = self._decrypt(encrypted) if encrypted else ""
        return result

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("settings payload must be an object")
        app_payload = payload.get("app", {})
        providers_payload = payload.get("providers", {})
        if not isinstance(app_payload, dict) or not isinstance(providers_payload, dict):
            raise ValueError("app and providers must be objects")
        connection = self.connect()
        try:
            with connection:
                if "semantic_enabled" in app_payload:
                    if not isinstance(app_payload["semantic_enabled"], bool):
                        raise ValueError("semantic_enabled must be a boolean")
                    self._set_app(connection, "semantic_enabled", app_payload["semantic_enabled"])
                if "default_search_mode" in app_payload:
                    mode = display_text(app_payload["default_search_mode"]).lower()
                    if mode not in {"keyword", "semantic", "hybrid"}:
                        raise ValueError("default_search_mode is invalid")
                    self._set_app(connection, "default_search_mode", mode)
                if "search_result_limit" in app_payload:
                    limit = max(1, min(int(app_payload["search_result_limit"]), 50))
                    self._set_app(connection, "search_result_limit", limit)
                if "allow_registration" in app_payload:
                    if not isinstance(app_payload["allow_registration"], bool):
                        raise ValueError("allow_registration must be a boolean")
                    self._set_app(
                        connection, "allow_registration", app_payload["allow_registration"]
                    )
                if "site_name" in app_payload:
                    site_name = display_text(app_payload["site_name"])
                    if not 2 <= len(site_name) <= 30:
                        raise ValueError("site_name must be between 2 and 30 characters")
                    self._set_app(connection, "site_name", site_name)
                for provider in ("embedding", "llm"):
                    if provider in providers_payload:
                        value = providers_payload[provider]
                        if not isinstance(value, dict):
                            raise ValueError(f"{provider} settings must be an object")
                        self._update_provider(connection, provider, value)
        finally:
            connection.close()
        return self.admin_settings()

    def _update_provider(
        self, connection: sqlite3.Connection, provider: str, payload: dict[str, Any]
    ) -> None:
        current = connection.execute(
            "SELECT * FROM provider_settings WHERE provider = ?", (provider,)
        ).fetchone()
        if current is None:
            raise SettingsUnavailable(f"Provider is not initialized: {provider}")
        api_url = display_text(payload.get("api_url", current["api_url"]))
        model = display_text(payload.get("model", current["model"]))
        validate_provider_url(api_url)
        if not model or len(model) > 200:
            raise ValueError("model must be between 1 and 200 characters")
        dimensions = current["dimensions"]
        if provider == "embedding" and "dimensions" in payload:
            dimensions = int(payload["dimensions"])
            if dimensions not in {64, 128, 256, 512, 768, 1024}:
                raise ValueError("embedding dimensions must be supported by Qwen3 Embedding")
        encrypted = current["api_key_encrypted"]
        if payload.get("clear_api_key") is True:
            encrypted = None
        api_key = payload.get("api_key")
        if api_key not in (None, ""):
            if not isinstance(api_key, str) or len(api_key.strip()) < 8:
                raise ValueError("api_key is invalid")
            encrypted = self._encrypt(api_key.strip())
        connection.execute(
            """
            UPDATE provider_settings
            SET api_url = ?, model = ?, dimensions = ?,
                api_key_encrypted = ?, updated_at = ?
            WHERE provider = ?
            """,
            (api_url, model, dimensions, encrypted, _utc_now(), provider),
        )

    def _app_settings(self) -> dict[str, Any]:
        connection = self.connect()
        try:
            rows = connection.execute("SELECT key, value_json FROM app_settings").fetchall()
        finally:
            connection.close()
        return {row["key"]: json.loads(row["value_json"]) for row in rows}

    @staticmethod
    def _set_app(connection: sqlite3.Connection, key: str, value: Any) -> None:
        connection.execute(
            """
            INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (key, json.dumps(value, ensure_ascii=False), _utc_now()),
        )

    def _ensure_key(self) -> bytes:
        if self.key_path.is_file():
            key = self.key_path.read_bytes().strip()
            try:
                Fernet(key)
            except (ValueError, TypeError) as error:
                raise SettingsUnavailable("Settings encryption key is invalid") from error
            return key
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.key_path.with_name(f"{self.key_path.name}.tmp")
        temporary.write_bytes(Fernet.generate_key() + b"\n")
        self._chmod_private(temporary)
        os.replace(temporary, self.key_path)
        self._chmod_private(self.key_path)
        return self.key_path.read_bytes().strip()

    def _fernet(self) -> Fernet:
        return Fernet(self._ensure_key())

    def _encrypt(self, value: str) -> bytes:
        return self._fernet().encrypt(value.encode("utf-8"))

    def _decrypt(self, value: bytes) -> str:
        try:
            return self._fernet().decrypt(value).decode("utf-8")
        except InvalidToken as error:
            raise SettingsUnavailable("Stored API key cannot be decrypted") from error

    def _masked_key(self, encrypted: bytes | None) -> str:
        if not encrypted:
            return ""
        secret = self._decrypt(encrypted)
        return f"{'*' * 8}{secret[-4:]}" if len(secret) >= 4 else "********"

    @staticmethod
    def _chmod_private(path: Path) -> None:
        try:
            path.chmod(0o600)
        except OSError:
            pass

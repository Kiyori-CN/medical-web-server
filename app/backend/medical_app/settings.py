from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from PIL import Image, ImageOps, UnidentifiedImageError

from .normalize import display_text
from .providers import (
    normalize_provider_base_url,
    provider_api_url,
    provider_models_url,
)


SETTINGS_SCHEMA_REVISION = 2
DEFAULT_EMBEDDING_API_URL = "https://api.siliconflow.cn/v1/embeddings"
DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_EMBEDDING_DIMENSIONS = 1024
DEFAULT_LLM_API_URL = "https://api.siliconflow.cn/v1/chat/completions"
DEFAULT_LLM_MODEL = "Qwen/Qwen2.5-7B-Instruct"
DEFAULT_SITE_APPEARANCE = {
    "site_name": "知库",
    "site_mark": "知",
    "site_subtitle": "Question Bank Workspace",
    "site_description": "知库：可私有部署的通用题库检索、练习与复习平台。",
    "login_eyebrow": "通用题库学习工作台",
    "login_title": "把检索、练习与复习，放在同一条学习路径。",
    "login_description": "保留题库目录、来源文件和原题号，支持精确搜题、智能检索与跨设备练习。",
    "login_features": [
        "按来源文件精确刷题",
        "收藏与错题自动归档",
        "账号数据完全本地保存",
    ],
}
SITE_TEXT_LIMITS = {
    "site_name": (2, 30),
    "site_mark": (1, 4),
    "site_subtitle": (0, 60),
    "site_description": (2, 160),
    "login_eyebrow": (0, 40),
    "login_title": (2, 90),
    "login_description": (0, 240),
}
LOGIN_BACKGROUND_MAX_BYTES = 8 * 1024 * 1024
LOGIN_BACKGROUND_MAX_PIXELS = 32_000_000
LOGIN_BACKGROUND_MAX_EDGE = 2560


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
                CREATE TABLE IF NOT EXISTS site_assets (
                    asset_name TEXT PRIMARY KEY,
                    media_type TEXT NOT NULL,
                    content BLOB NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
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
                **DEFAULT_SITE_APPEARANCE,
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
        site = self._site_settings_from_app(app)
        return {
            "semantic_enabled": bool(app["semantic_enabled"]),
            "default_search_mode": app["default_search_mode"],
            "search_result_limit": int(app["search_result_limit"]),
            "allow_registration": bool(app["allow_registration"]),
            "site_name": site["site_name"],
            "site": site,
            "embedding": embedding,
            "llm": llm,
        }

    def admin_settings(self) -> dict[str, Any]:
        payload = self.public_settings()
        payload["site_defaults"] = {
            **DEFAULT_SITE_APPEARANCE,
            "login_features": list(DEFAULT_SITE_APPEARANCE["login_features"]),
        }
        return payload

    def site_settings(self) -> dict[str, Any]:
        return self._site_settings_from_app(self._app_settings())

    def public_site_settings(self) -> dict[str, Any]:
        app = self._app_settings()
        return {
            **self._site_settings_from_app(app),
            "allow_registration": bool(app["allow_registration"]),
        }

    def login_background(self, *, include_content: bool = True) -> dict[str, Any] | None:
        connection = self.connect()
        try:
            row = connection.execute(
                (
                    "SELECT * FROM site_assets WHERE asset_name = 'login_background'"
                    if include_content
                    else """
                        SELECT asset_name, media_type, width, height, sha256, updated_at
                        FROM site_assets WHERE asset_name = 'login_background'
                    """
                )
            ).fetchone()
        finally:
            connection.close()
        return dict(row) if row is not None else None

    def set_login_background(self, payload: bytes) -> dict[str, Any]:
        if not payload or len(payload) > LOGIN_BACKGROUND_MAX_BYTES:
            raise ValueError("登录背景图片必须小于 8 MB")
        try:
            with Image.open(io.BytesIO(payload)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("登录背景仅支持 JPG、PNG 或 WebP")
                if int(getattr(source, "n_frames", 1)) != 1:
                    raise ValueError("登录背景不支持动态图片")
                width, height = source.size
                if width * height > LOGIN_BACKGROUND_MAX_PIXELS:
                    raise ValueError("登录背景图片像素尺寸过大")
                if min(width, height) < 320 or max(width, height) < 640:
                    raise ValueError("登录背景图片至少需要 640×320 像素")
                source.load()
                image = ImageOps.exif_transpose(source).convert("RGBA")
        except Image.DecompressionBombError as error:
            raise ValueError("登录背景图片像素尺寸过大") from error
        except ValueError:
            raise
        except (OSError, UnidentifiedImageError) as error:
            raise ValueError("登录背景图片无法读取") from error

        image.thumbnail(
            (LOGIN_BACKGROUND_MAX_EDGE, LOGIN_BACKGROUND_MAX_EDGE),
            Image.Resampling.LANCZOS,
        )
        canvas = Image.new("RGBA", image.size, (31, 37, 48, 255))
        canvas.alpha_composite(image)
        output = io.BytesIO()
        canvas.convert("RGB").save(
            output,
            format="WEBP",
            quality=88,
            method=6,
        )
        content = output.getvalue()
        digest = hashlib.sha256(content).hexdigest()
        now = _utc_now()
        connection = self.connect()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO site_assets(
                        asset_name, media_type, content, width, height, sha256, updated_at
                    ) VALUES ('login_background', 'image/webp', ?, ?, ?, ?, ?)
                    ON CONFLICT(asset_name) DO UPDATE SET
                        media_type = excluded.media_type,
                        content = excluded.content,
                        width = excluded.width,
                        height = excluded.height,
                        sha256 = excluded.sha256,
                        updated_at = excluded.updated_at
                    """,
                    (content, canvas.width, canvas.height, digest, now),
                )
        finally:
            connection.close()
        return self.site_settings()

    def delete_login_background(self) -> bool:
        connection = self.connect()
        try:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM site_assets WHERE asset_name = 'login_background'"
                )
        finally:
            connection.close()
        return cursor.rowcount == 1

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
        base_url = normalize_provider_base_url(row["api_url"])
        api_url = provider_api_url(base_url, provider)
        result = {
            "provider": provider,
            "base_url": base_url,
            "api_url": api_url,
            "models_url": provider_models_url(base_url),
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
                    self._set_app(
                        connection,
                        "site_name",
                        self._site_text("site_name", app_payload["site_name"]),
                    )
                for field in (
                    "site_mark",
                    "site_subtitle",
                    "site_description",
                    "login_eyebrow",
                    "login_title",
                    "login_description",
                ):
                    if field in app_payload:
                        self._set_app(
                            connection, field, self._site_text(field, app_payload[field])
                        )
                if "login_features" in app_payload:
                    features = app_payload["login_features"]
                    if not isinstance(features, list) or len(features) != 3:
                        raise ValueError("login_features must contain exactly three items")
                    normalized_features = []
                    for value in features:
                        text = display_text(value)
                        if len(text) > 60:
                            raise ValueError("login feature must not exceed 60 characters")
                        normalized_features.append(text)
                    self._set_app(connection, "login_features", normalized_features)
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
        configured_url = payload.get(
            "base_url", payload.get("api_url", current["api_url"])
        )
        base_url = normalize_provider_base_url(display_text(configured_url))
        api_url = provider_api_url(base_url, provider)
        model = display_text(payload.get("model", current["model"]))
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

    def _site_settings_from_app(self, app: dict[str, Any]) -> dict[str, Any]:
        background = self.login_background(include_content=False)
        features = app.get("login_features", DEFAULT_SITE_APPEARANCE["login_features"])
        if not isinstance(features, list) or len(features) != 3:
            features = DEFAULT_SITE_APPEARANCE["login_features"]
        return {
            field: str(app.get(field, default))
            for field, default in DEFAULT_SITE_APPEARANCE.items()
            if field != "login_features"
        } | {
            "login_features": [display_text(value)[:60] for value in features],
            "login_background": {
                "configured": background is not None,
                "version": background["sha256"][:20] if background else "",
                "width": int(background["width"]) if background else 0,
                "height": int(background["height"]) if background else 0,
            },
        }

    @staticmethod
    def _site_text(field: str, value: object) -> str:
        minimum, maximum = SITE_TEXT_LIMITS[field]
        text = display_text(value)
        if not minimum <= len(text) <= maximum:
            raise ValueError(
                f"{field} must be between {minimum} and {maximum} characters"
            )
        return text

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

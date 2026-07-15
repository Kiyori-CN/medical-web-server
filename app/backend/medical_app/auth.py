from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time
import unicodedata
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, Flask, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash


SESSION_COOKIE = "medical_session"
CSRF_COOKIE = "medical_csrf"
SESSION_TTL_SECONDS = 14 * 24 * 60 * 60
_USERNAME_RE = re.compile(r"^[\w.-]{3,32}$", re.UNICODE)
_AVATAR_OPTIONS = {
    "", "医", "学", "知", "🩺", "🧬", "🧠", "🫀", "🫁", "📚", "⭐", "🌿",
}


class AuthenticationError(RuntimeError):
    pass


class AuthorizationError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_username(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def validate_username(value: str) -> str:
    username = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not _USERNAME_RE.fullmatch(username):
        raise ValueError("用户名须为 3–32 位，可使用中文、字母、数字、点、短横线或下划线")
    return username


def validate_password(value: str) -> str:
    password = str(value or "")
    if len(password) < 10 or len(password) > 128:
        raise ValueError("密码长度须为 10–128 位")
    categories = (
        any(character.islower() for character in password),
        any(character.isupper() for character in password),
        any(character.isdigit() for character in password),
        any(not character.isalnum() for character in password),
    )
    if sum(categories) < 3:
        raise ValueError("密码须包含大写字母、小写字母、数字或符号中的至少三类")
    return password


def validate_avatar(value: str) -> str:
    avatar = unicodedata.normalize("NFKC", str(value or "")).strip()
    if avatar not in _AVATAR_OPTIONS:
        raise ValueError("请选择系统提供的头像")
    return avatar


class AuthStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).resolve()

    def initialize(self) -> dict[str, Any]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    normalized_username TEXT NOT NULL UNIQUE,
                    avatar TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'disabled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    user_agent TEXT NOT NULL DEFAULT '',
                    remote_hash TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                ON auth_sessions(user_id, expires_at);
                CREATE INDEX IF NOT EXISTS idx_users_role_status
                ON users(role, status);
                """
            )
            user_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(users)")
            }
            if "avatar" not in user_columns:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN avatar TEXT NOT NULL DEFAULT ''"
                )
            connection.commit()
        finally:
            connection.close()
        try:
            self.database_path.chmod(0o600)
        except OSError:
            pass
        return self.stats()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def has_users(self) -> bool:
        with self.connect() as connection:
            return bool(connection.execute("SELECT 1 FROM users LIMIT 1").fetchone())

    def setup_admin(self, username: str, password: str) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                connection.rollback()
                raise AuthenticationError("系统已经完成初始化")
            user = self._insert_user(connection, username, password, role="admin")
            connection.commit()
        return user

    def register(self, username: str, password: str) -> dict[str, Any]:
        with self.connect() as connection:
            try:
                user = self._insert_user(connection, username, password, role="user")
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise AuthenticationError("用户名已存在") from error
        return user

    def create_user(
        self, username: str, password: str, *, role: str = "user"
    ) -> dict[str, Any]:
        if role not in {"admin", "user"}:
            raise ValueError("role must be admin or user")
        with self.connect() as connection:
            try:
                user = self._insert_user(connection, username, password, role=role)
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise AuthenticationError("用户名已存在") from error
        return user

    def _insert_user(
        self, connection: sqlite3.Connection, username: str, password: str, *, role: str
    ) -> dict[str, Any]:
        display_name = validate_username(username)
        password = validate_password(password)
        user_id = f"usr_{secrets.token_hex(16)}"
        now = _utc_now()
        connection.execute(
            """
            INSERT INTO users(
                user_id, username, normalized_username, password_hash,
                role, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                user_id,
                display_name,
                _normalized_username(display_name),
                generate_password_hash(password, method="scrypt"),
                role,
                now,
                now,
            ),
        )
        return {
            "user_id": user_id,
            "username": display_name,
            "role": role,
            "status": "active",
            "created_at": now,
            "last_login_at": None,
        }

    def authenticate(self, username: str, password: str) -> dict[str, Any]:
        normalized = _normalized_username(username)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE normalized_username = ?", (normalized,)
            ).fetchone()
            if row is None or not check_password_hash(row["password_hash"], str(password or "")):
                raise AuthenticationError("用户名或密码不正确")
            if row["status"] != "active":
                raise AuthenticationError("账号已被停用，请联系管理员")
            now = _utc_now()
            connection.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE user_id = ?",
                (now, now, row["user_id"]),
            )
            connection.commit()
            payload = self._public_user(row)
            payload["last_login_at"] = now
            return payload

    def change_password(
        self, user_id: str, current_password: str, new_password: str
    ) -> None:
        new_password = validate_password(new_password)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT password_hash FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise AuthenticationError("用户不存在")
            if not check_password_hash(
                row["password_hash"], str(current_password or "")
            ):
                connection.rollback()
                raise AuthenticationError("当前密码不正确")
            now = _utc_now()
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?",
                (generate_password_hash(new_password, method="scrypt"), now, user_id),
            )
            connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            connection.commit()

    def update_profile(self, user_id: str, username: str, avatar: str) -> dict[str, Any]:
        display_name = validate_username(username)
        avatar = validate_avatar(avatar)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
            ).fetchone() is None:
                connection.rollback()
                raise AuthenticationError("用户不存在")
            try:
                connection.execute(
                    """
                    UPDATE users
                    SET username = ?, normalized_username = ?, avatar = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (
                        display_name,
                        _normalized_username(display_name),
                        avatar,
                        _utc_now(),
                        user_id,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise AuthenticationError("用户名已存在") from error
            updated = connection.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return self._public_user(updated)

    def create_session(
        self, user_id: str, *, user_agent: str = "", remote_address: str = ""
    ) -> tuple[str, int]:
        token = secrets.token_urlsafe(40)
        now_epoch = int(time.time())
        expires_at = now_epoch + SESSION_TTL_SECONDS
        now = _utc_now()
        remote_hash = _token_hash(remote_address) if remote_address else ""
        with self.connect() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now_epoch,))
            connection.execute(
                """
                INSERT INTO auth_sessions(
                    token_hash, user_id, created_at, last_seen_at, expires_at,
                    user_agent, remote_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _token_hash(token),
                    user_id,
                    now,
                    now,
                    expires_at,
                    str(user_agent or "")[:300],
                    remote_hash,
                ),
            )
            connection.commit()
        return token, expires_at

    def resolve_session(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        now_epoch = int(time.time())
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT u.*
                FROM auth_sessions s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ? AND u.status = 'active'
                """,
                (_token_hash(token), now_epoch),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
                (_utc_now(), _token_hash(token)),
            )
            connection.commit()
        return self._public_user(row)

    def delete_session(self, token: str) -> None:
        if not token:
            return
        with self.connect() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),))
            connection.commit()

    def list_users(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id, username, avatar, role, status, created_at, updated_at, last_login_at
                FROM users
                ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, created_at, username
                LIMIT ? OFFSET ?
                """,
                (max(1, min(int(limit), 500)), max(0, int(offset))),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_user(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row is None:
                connection.rollback()
                raise LookupError("用户不存在")
            role = str(payload.get("role", row["role"])).strip().lower()
            status = str(payload.get("status", row["status"])).strip().lower()
            if role not in {"admin", "user"} or status not in {"active", "disabled"}:
                connection.rollback()
                raise ValueError("role or status is invalid")
            removing_active_admin = (
                row["role"] == "admin"
                and row["status"] == "active"
                and (role != "admin" or status != "active")
            )
            if removing_active_admin:
                active_admins = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND status = 'active'"
                ).fetchone()[0]
                if active_admins <= 1:
                    connection.rollback()
                    raise AuthorizationError("不能停用或降级最后一个可用管理员")
            password_hash = row["password_hash"]
            if payload.get("password") not in (None, ""):
                password_hash = generate_password_hash(
                    validate_password(payload["password"]), method="scrypt"
                )
            now = _utc_now()
            connection.execute(
                """
                UPDATE users
                SET role = ?, status = ?, password_hash = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (role, status, password_hash, now, user_id),
            )
            if status != "active" or payload.get("password") not in (None, ""):
                connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            connection.commit()
            updated = connection.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return self._public_user(updated)

    def stats(self) -> dict[str, int]:
        if not self.database_path.is_file():
            return {"users": 0, "admins": 0, "active_sessions": 0}
        with self.connect() as connection:
            users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            admins = connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND status = 'active'"
            ).fetchone()[0]
            sessions = connection.execute(
                "SELECT COUNT(*) FROM auth_sessions WHERE expires_at > ?", (int(time.time()),)
            ).fetchone()[0]
        return {"users": users, "admins": admins, "active_sessions": sessions}

    @staticmethod
    def _public_user(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "user_id": row["user_id"],
            "username": row["username"],
            "avatar": row["avatar"],
            "role": row["role"],
            "status": row["status"],
            "created_at": row["created_at"],
            "last_login_at": row["last_login_at"],
        }


def install_auth(app: Flask, store: AuthStore) -> None:
    @app.before_request
    def load_current_user() -> None:
        g.current_user = store.resolve_session(request.cookies.get(SESSION_COOKIE, ""))


def login_required(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "current_user", None):
            return jsonify({"error": "请先登录", "code": "authentication_required"}), 401
        return view(*args, **kwargs)

    return wrapped


def admin_required(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = getattr(g, "current_user", None)
        if not user:
            return jsonify({"error": "请先登录", "code": "authentication_required"}), 401
        if user.get("role") != "admin":
            return jsonify({"error": "需要管理员权限", "code": "admin_required"}), 403
        return view(*args, **kwargs)

    return wrapped


def csrf_required(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        cookie_token = request.cookies.get(CSRF_COOKIE, "")
        header_token = request.headers.get("X-CSRF-Token", "")
        if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
            return jsonify({"error": "请求校验失败，请刷新页面后重试", "code": "csrf_failed"}), 403
        return view(*args, **kwargs)

    return wrapped


def create_auth_blueprint(
    store: AuthStore,
    *,
    registration_enabled: Callable[[], bool],
    secure_cookies: bool,
) -> Blueprint:
    blueprint = Blueprint("auth", __name__, url_prefix="/api/auth")

    @blueprint.errorhandler(AuthenticationError)
    def handle_authentication_error(error):
        return jsonify({"error": str(error)}), 409

    @blueprint.errorhandler(AuthorizationError)
    def handle_authorization_error(error):
        return jsonify({"error": str(error)}), 409

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    def authenticated_response(user: dict[str, Any], status: int = 200):
        token, expires_at = store.create_session(
            user["user_id"],
            user_agent=request.headers.get("User-Agent", ""),
            remote_address=request.remote_addr or "",
        )
        csrf_token = secrets.token_urlsafe(24)
        response = jsonify({"user": user, "expires_at": expires_at})
        response.status_code = status
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            secure=secure_cookies,
            samesite="Lax",
            path="/",
        )
        response.set_cookie(
            CSRF_COOKIE,
            csrf_token,
            max_age=SESSION_TTL_SECONDS,
            httponly=False,
            secure=secure_cookies,
            samesite="Lax",
            path="/",
        )
        return response

    @blueprint.get("/setup")
    def setup_status():
        return jsonify({"setup_required": not store.has_users()})

    @blueprint.post("/setup")
    def setup_admin():
        data = _json_object()
        user = store.setup_admin(data.get("username", ""), data.get("password", ""))
        return authenticated_response(user, 201)

    @blueprint.post("/register")
    def register():
        if not store.has_users():
            raise AuthenticationError("请先创建管理员账号")
        if not registration_enabled():
            raise AuthenticationError("当前未开放注册")
        data = _json_object()
        user = store.register(data.get("username", ""), data.get("password", ""))
        return authenticated_response(user, 201)

    @blueprint.post("/login")
    def login():
        data = _json_object()
        user = store.authenticate(data.get("username", ""), data.get("password", ""))
        return authenticated_response(user)

    @blueprint.get("/me")
    def me():
        return jsonify(
            {
                "authenticated": bool(getattr(g, "current_user", None)),
                "user": getattr(g, "current_user", None),
            }
        )

    @blueprint.post("/logout")
    @login_required
    @csrf_required
    def logout():
        store.delete_session(request.cookies.get(SESSION_COOKIE, ""))
        response = jsonify({"status": "ok"})
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    @blueprint.post("/password")
    @login_required
    @csrf_required
    def change_password():
        data = _json_object()
        store.change_password(
            g.current_user["user_id"],
            data.get("current_password", ""),
            data.get("new_password", ""),
        )
        response = jsonify({"status": "ok", "reauthenticate": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    @blueprint.put("/profile")
    @login_required
    @csrf_required
    def update_profile():
        data = _json_object()
        user = store.update_profile(
            g.current_user["user_id"],
            data.get("username", g.current_user["username"]),
            data.get("avatar", g.current_user.get("avatar", "")),
        )
        return jsonify({"user": user})

    return blueprint


def create_user_admin_blueprint(store: AuthStore) -> Blueprint:
    blueprint = Blueprint("user_admin", __name__, url_prefix="/api/admin/users")

    @blueprint.errorhandler(AuthenticationError)
    @blueprint.errorhandler(AuthorizationError)
    def handle_conflict(error):
        return jsonify({"error": str(error)}), 409

    @blueprint.errorhandler(LookupError)
    def handle_not_found(error):
        return jsonify({"error": str(error)}), 404

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.get("")
    @admin_required
    def users():
        rows = store.list_users(
            limit=request.args.get("limit", 100), offset=request.args.get("offset", 0)
        )
        return jsonify({"count": len(rows), "users": rows})

    @blueprint.post("")
    @admin_required
    @csrf_required
    def create_user():
        data = _json_object()
        user = store.create_user(
            data.get("username", ""), data.get("password", ""), role=data.get("role", "user")
        )
        return jsonify(user), 201

    @blueprint.patch("/<user_id>")
    @admin_required
    @csrf_required
    def update_user(user_id: str):
        return jsonify(store.update_user(user_id, _json_object()))

    return blueprint


def _json_object() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return data

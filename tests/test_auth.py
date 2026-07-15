import pytest
from io import BytesIO

from PIL import Image

from medical_app.auth import AuthStore, AuthorizationError, AuthenticationError


def test_local_accounts_hash_passwords_sessions_and_last_admin_guard(tmp_path):
    database = tmp_path / "state" / "app.sqlite3"
    store = AuthStore(database)
    assert store.initialize() == {"users": 0, "admins": 0, "active_sessions": 0}
    admin = store.setup_admin("Kiyori", "StrongPass1!")
    assert admin["role"] == "admin"
    assert "StrongPass1!" not in database.read_bytes().decode("latin-1")
    with pytest.raises(AuthenticationError, match="已经完成初始化"):
        store.setup_admin("another", "StrongPass1!")

    user = store.register("Student", "StudentPass1!")
    authenticated = store.authenticate("student", "StudentPass1!")
    assert authenticated["user_id"] == user["user_id"]
    token, _ = store.create_session(user["user_id"])
    assert store.resolve_session(token)["username"] == "Student"

    with pytest.raises(AuthenticationError, match="当前密码不正确"):
        store.change_password(user["user_id"], "WrongPass1!", "ChangedPass2!")
    profile = store.update_profile(user["user_id"], "StudentNew", "🩺")
    assert profile["username"] == "StudentNew"
    assert profile["avatar"] == "🩺"
    assert profile["privacy"]["followers"] is True
    assert profile["notifications"]["direct"] is True
    preferences = store.update_preferences(
        user["user_id"],
        privacy={"school": True, "followers": False},
        notifications={"direct": False},
    )
    assert preferences["privacy"]["school"] is True
    assert preferences["privacy"]["followers"] is False
    assert preferences["notifications"]["direct"] is False
    preserved = store.update_profile(user["user_id"], "StudentNew", "🩺")
    assert preserved["privacy"]["followers"] is False
    assert preserved["notifications"]["direct"] is False
    assert store.authenticate("studentnew", "StudentPass1!")["avatar"] == "🩺"
    store.change_password(user["user_id"], "StudentPass1!", "ChangedPass2!")
    assert store.resolve_session(token) is None
    assert store.authenticate("studentnew", "ChangedPass2!")["username"] == "StudentNew"

    with pytest.raises(AuthorizationError, match="最后一个"):
        store.update_user(admin["user_id"], {"status": "disabled"})
    store.update_user(user["user_id"], {"status": "disabled"})
    assert store.resolve_session(token) is None


def test_admin_can_change_role_and_reset_password(tmp_path):
    store = AuthStore(tmp_path / "state" / "app.sqlite3")
    store.initialize()
    store.setup_admin("Kiyori", "StrongPass1!")
    user = store.create_user("Student", "StudentPass1!", role="user")

    updated = store.update_user(
        user["user_id"], {"role": "admin", "password": "RenewedPass2!"}
    )
    assert updated["role"] == "admin"
    with pytest.raises(AuthenticationError, match="用户名或密码不正确"):
        store.authenticate("Student", "StudentPass1!")
    assert store.authenticate("Student", "RenewedPass2!")["role"] == "admin"


def test_avatar_upload_is_reencoded_and_profile_exposes_cacheable_url(tmp_path):
    store = AuthStore(tmp_path / "state" / "app.sqlite3")
    store.initialize()
    user = store.setup_admin("Kiyori", "StrongPass1!")
    source = BytesIO()
    Image.new("RGB", (900, 600), (232, 93, 82)).save(source, format="PNG")

    updated = store.save_avatar_image(user["user_id"], source.getvalue())
    assert updated["avatar"] == "custom"
    assert updated["avatar_url"].startswith("/api/auth/avatars/")
    image = store.avatar_image(user["user_id"])
    assert image["mime_type"] == "image/webp"
    with Image.open(BytesIO(image["image_data"])) as decoded:
        assert decoded.size == (512, 512)
        assert decoded.format == "WEBP"

    profile = store.update_profile(user["user_id"], "Kiyori", "preset-03")
    assert profile["avatar"] == "preset-03"
    assert profile["avatar_url"].endswith("/avatars/preset-03.svg")

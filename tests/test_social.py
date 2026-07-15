from pathlib import Path

from medical_app.auth import AuthStore
from medical_app.builder import build_catalog
from medical_app.catalog import Catalog
from medical_app.practice import PracticeStore


def _social_services(sample_source_tree: Path, tmp_path: Path):
    manifest = build_catalog(sample_source_tree, tmp_path / "builds")
    catalog = Catalog(tmp_path / "builds" / manifest["build_id"] / "catalog.sqlite3")
    database = tmp_path / "state" / "app.sqlite3"
    auth = AuthStore(database)
    auth.initialize()
    alice = auth.setup_admin("Alice", "StrongPass1!")
    bob = auth.register("BobUser", "StrongPass2!")
    alice_store = PracticeStore(database, catalog, alice["user_id"])
    alice_store.initialize()
    bob_store = PracticeStore(database, catalog, bob["user_id"])
    return auth, catalog, alice, bob, alice_store, bob_store


def test_public_profile_follow_messages_and_comment_interactions(
    sample_source_tree: Path, tmp_path: Path
):
    auth, catalog, alice, bob, alice_store, bob_store = _social_services(
        sample_source_tree, tmp_path
    )
    profile = auth.update_profile(
        alice["user_id"],
        "Alice",
        "preset-02",
        {
            "birthday": "2000-01-02",
            "gender": "女",
            "school": "示例医学院",
            "major": "临床医学",
            "privacy": {"school": True, "major": True},
        },
    )
    assert profile["school"] == "示例医学院"
    assert profile["privacy"]["school"] is True
    assert profile["privacy"]["birthday"] is False

    followed = alice_store.follow_user(bob["user_id"])
    assert followed["followers"] == 1
    assert followed["is_following"] is True
    public = bob_store.public_profile(alice["user_id"])
    assert public["school"] == "示例医学院"
    assert public["birthday"] == ""
    assert public["is_self"] is False

    collection = catalog.list_collections()[0]
    occurrence = catalog.collection_outline(collection["collection_id"])[0]
    comment = alice_store.create_comment(
        occurrence["occurrence_id"], author_name="Alice", body="欢迎讨论。"
    )
    reaction = bob_store.react_comment(comment["comment_id"], "agree")
    assert reaction["agree_count"] == 1
    bob_store.create_comment(
        occurrence["occurrence_id"],
        author_name="BobUser",
        body="我也这样认为。",
        parent_comment_id=comment["comment_id"],
    )
    decorated = alice_store.list_comments(occurrence["occurrence_id"])["items"]
    parent = next(item for item in decorated if item["comment_id"] == comment["comment_id"])
    assert parent["agree_count"] == 1
    assert parent["reply_count"] == 1
    assert bob_store.my_likes()["total"] == 1

    message = bob_store.send_message(alice["user_id"], "一起复习这道题。")
    assert message["receiver_id"] == alice["user_id"]
    inbox = alice_store.list_messages()
    assert inbox["messages"][0]["body"] == "一起复习这道题。"
    assert {item["kind"] for item in inbox["notifications"]} >= {"agree", "reply"}

    unfollowed = alice_store.unfollow_user(bob["user_id"])
    assert unfollowed["followers"] == 0

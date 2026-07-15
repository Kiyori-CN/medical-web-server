from pathlib import Path
import sqlite3

import pytest

from medical_app.auth import AuthStore
from medical_app.forum import ForumForbidden, ForumNotFound, ForumStore


def _forum_services(tmp_path: Path):
    database = tmp_path / "state" / "app.sqlite3"
    auth = AuthStore(database)
    auth.initialize()
    alice = auth.setup_admin("Alice", "StrongPass1!")
    bob = auth.register("BobUser", "StrongPass2!")
    alice_store = ForumStore(database, alice["user_id"])
    alice_store.initialize()
    bob_store = ForumStore(database, bob["user_id"])
    return alice, bob, alice_store, bob_store


def test_forum_topic_reply_search_and_permissions(tmp_path: Path):
    alice, bob, alice_store, bob_store = _forum_services(tmp_path)
    sections = alice_store.sections()
    assert [item["section_id"] for item in sections] == ["study", "questions"]

    topic = alice_store.create_topic(
        section_id="questions",
        title="细胞膜运输方式怎么区分",
        body="易化扩散和主动转运的关键区别是什么？",
        author_name="Alice",
    )
    assert {item["section_id"]: item["topic_count"] for item in alice_store.sections()} == {
        "study": 0,
        "questions": 1,
    }
    with sqlite3.connect(alice_store.database_path) as connection:
        connection.execute(
            """
            CREATE TABLE notifications (
                notification_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                actor_id TEXT NOT NULL DEFAULT '',
                comment_id TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                read_at TEXT
            )
            """
        )
    listing = bob_store.list_topics(section_id="questions", query="主动转运")
    assert listing["total"] == 1
    assert listing["items"][0]["topic_id"] == topic["topic_id"]
    assert listing["items"][0]["can_manage"] is False

    reply = bob_store.create_reply(
        topic["topic_id"], body="关键是是否消耗能量以及转运方向。", author_name="BobUser"
    )
    detail = alice_store.get_topic(topic["topic_id"], viewer_is_admin=True)
    assert detail["topic"]["reply_count"] == 1
    assert detail["replies"][0]["reply_id"] == reply["reply_id"]
    assert detail["replies"][0]["can_manage"] is True
    with sqlite3.connect(alice_store.database_path) as connection:
        notification = connection.execute(
            "SELECT profile_id, kind, actor_id, comment_id, body FROM notifications"
        ).fetchone()
    assert notification[:4] == (
        alice["user_id"],
        "reply",
        bob["user_id"],
        reply["reply_id"],
    )
    assert notification[4].startswith("论坛回复：细胞膜运输方式")

    with pytest.raises(ValueError, match="自己的论坛内容"):
        alice_store.set_reaction("topic", topic["topic_id"], "agree")

    topic_reaction = bob_store.set_reaction("topic", topic["topic_id"], "agree")
    assert topic_reaction["my_reaction"] == "agree"
    assert topic_reaction["agree_count"] == 1
    bob_store.set_reaction("topic", topic["topic_id"], "agree")
    with sqlite3.connect(alice_store.database_path) as connection:
        agree_notifications = connection.execute(
            "SELECT COUNT(*) FROM notifications WHERE profile_id = ? AND kind = 'agree'",
            (alice["user_id"],),
        ).fetchone()[0]
    assert agree_notifications == 1
    switched = bob_store.set_reaction("topic", topic["topic_id"], "disagree")
    assert switched["agree_count"] == 0
    assert switched["disagree_count"] == 1
    cleared = bob_store.clear_reaction("topic", topic["topic_id"])
    assert cleared["my_reaction"] == ""
    assert cleared["disagree_count"] == 0

    reply_reaction = alice_store.set_reaction("reply", reply["reply_id"], "agree")
    assert reply_reaction["agree_count"] == 1
    with sqlite3.connect(alice_store.database_path) as connection:
        reply_agree = connection.execute(
            "SELECT body FROM notifications WHERE profile_id = ? AND kind = 'agree'",
            (bob["user_id"],),
        ).fetchone()[0]
    assert reply_agree.startswith("赞同了你的论坛回复")
    reacted_detail = alice_store.get_topic(topic["topic_id"], viewer_is_admin=True)
    assert reacted_detail["replies"][0]["my_reaction"] == "agree"
    assert reacted_detail["replies"][0]["agree_count"] == 1

    updated_topic = alice_store.update_topic(
        topic["topic_id"],
        title="细胞膜运输方式如何区分",
        body="请结合能量消耗和转运方向说明关键区别。",
    )
    assert updated_topic["title"] == "细胞膜运输方式如何区分"
    updated_reply = bob_store.update_reply(
        reply["reply_id"], body="关键是能量消耗、载体参与和转运方向。"
    )
    assert updated_reply["body"].startswith("关键是能量消耗")

    with pytest.raises(ForumForbidden):
        bob_store.delete_topic(topic["topic_id"])
    assert alice_store.delete_reply(reply["reply_id"], viewer_is_admin=True) is True
    with pytest.raises(ForumForbidden, match="不可互动"):
        alice_store.set_reaction("reply", reply["reply_id"], "disagree")
    with pytest.raises(ForumForbidden, match="不能编辑"):
        bob_store.update_reply(reply["reply_id"], body="不能更新已删除回复")
    assert alice_store.delete_topic(topic["topic_id"], viewer_is_admin=True) is True
    assert {item["section_id"]: item["topic_count"] for item in alice_store.sections()} == {
        "study": 0,
        "questions": 0,
    }
    with pytest.raises(ForumForbidden, match="不能编辑"):
        alice_store.update_topic(
            topic["topic_id"], title="不能更新已删除主题", body="删除后不允许编辑。"
        )
    with pytest.raises(ForumNotFound):
        bob_store.get_topic(topic["topic_id"])


def test_forum_validates_lengths_and_parent_topic(tmp_path: Path):
    _, _, alice_store, _ = _forum_services(tmp_path)
    with pytest.raises(ValueError, match="标题长度"):
        alice_store.create_topic(
            section_id="study", title="短", body="正文足够长", author_name="Alice"
        )
    topic = alice_store.create_topic(
        section_id="study",
        title="制定每日复习计划",
        body="如何把题库练习拆分为稳定的每日目标？",
        author_name="Alice",
    )
    with pytest.raises(ForumNotFound, match="回复目标"):
        alice_store.create_reply(
            topic["topic_id"],
            body="尝试按章节拆分。",
            author_name="Alice",
            parent_reply_id="missing",
        )

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


def _initialize_notifications(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
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
    _initialize_notifications(alice_store.database_path)
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


def test_forum_nested_replies_and_mentions_are_deduplicated(tmp_path: Path):
    alice, bob, alice_store, bob_store = _forum_services(tmp_path)
    charlie = AuthStore(alice_store.database_path).register(
        "CharlieUser", "StrongPass3!"
    )
    charlie_store = ForumStore(alice_store.database_path, charlie["user_id"])
    _initialize_notifications(alice_store.database_path)
    topic = alice_store.create_topic(
        section_id="study",
        title="如何安排错题复习节奏",
        body="想把错题回顾安排进每日复习计划。",
        author_name="Alice",
    )
    parent = alice_store.create_reply(
        topic["topic_id"],
        body="可以先按知识点分组。",
        author_name="Alice",
    )
    child = bob_store.create_reply(
        topic["topic_id"],
        body="请@CharlieUser @CharlieUser 补充；@Alice 谢谢。@MissingUser @BobUser",
        author_name="BobUser",
        parent_reply_id=parent["reply_id"],
    )

    assert child["parent_reply_id"] == parent["reply_id"]
    detail = alice_store.get_topic(topic["topic_id"], viewer_is_admin=True)
    nested = next(item for item in detail["replies"] if item["reply_id"] == child["reply_id"])
    assert nested["parent_reply_id"] == parent["reply_id"]
    assert nested["parent_author_name"] == "Alice"

    with sqlite3.connect(alice_store.database_path) as connection:
        notifications = connection.execute(
            """
            SELECT profile_id, kind, actor_id, comment_id, body
            FROM notifications
            WHERE comment_id = ?
            ORDER BY profile_id, kind
            """,
            (child["reply_id"],),
        ).fetchall()
    assert len(notifications) == 2
    assert {
        (row[0], row[1], row[2], row[3]) for row in notifications
    } == {
        (alice["user_id"], "reply", bob["user_id"], child["reply_id"]),
        (charlie["user_id"], "mention", bob["user_id"], child["reply_id"]),
    }
    assert all(row[4].startswith("论坛") for row in notifications)

    assert alice_store.delete_reply(parent["reply_id"]) is True
    with pytest.raises(ForumNotFound, match="回复目标"):
        charlie_store.create_reply(
            topic["topic_id"],
            body="不能回复已经删除的楼层。",
            author_name="CharlieUser",
            parent_reply_id=parent["reply_id"],
        )


def test_forum_admin_moderation_filters_and_audit(tmp_path: Path):
    alice, bob, alice_store, bob_store = _forum_services(tmp_path)
    charlie = AuthStore(alice_store.database_path).register(
        "CharlieUser", "StrongPass3!"
    )
    charlie_store = ForumStore(alice_store.database_path, charlie["user_id"])
    topic = bob_store.create_topic(
        section_id="questions",
        title="需要管理员审核的讨论",
        body="这是一条用于验证隐藏、恢复和审计记录的论坛主题。",
        author_name="BobUser",
    )
    reply = bob_store.create_reply(
        topic["topic_id"],
        body="这是一条需要审核的回复。",
        author_name="BobUser",
    )

    content = alice_store.admin_content(
        query="管理员审核", target_type="topic", limit=10
    )
    assert content["total"] == 1
    assert content["items"][0]["target_type"] == "topic"
    assert content["stats"] == {
        "total": 2,
        "topics": 1,
        "replies": 1,
        "visible": 2,
        "hidden": 0,
        "deleted": 0,
    }
    with pytest.raises(ValueError, match="审核原因"):
        alice_store.moderate_content(
            "topic",
            topic["topic_id"],
            action="hide",
            reason="短",
            moderator_name="Alice",
        )

    hidden_topic = alice_store.moderate_content(
        "topic",
        topic["topic_id"],
        action="hide",
        reason="等待管理员复核主题内容",
        moderator_name="Alice",
    )
    assert hidden_topic["new_status"] == "hidden"
    assert bob_store.list_topics()["total"] == 0
    assert bob_store.get_topic(topic["topic_id"])["topic"]["status"] == "hidden"
    with pytest.raises(ForumNotFound):
        charlie_store.get_topic(topic["topic_id"])
    with pytest.raises(ForumForbidden, match="不可回复"):
        bob_store.create_reply(
            topic["topic_id"], body="隐藏后不能回复。", author_name="BobUser"
        )

    alice_store.moderate_content(
        "reply",
        reply["reply_id"],
        action="hide",
        reason="随主题一起复核回复",
        moderator_name="Alice",
    )
    with pytest.raises(ForumForbidden, match="先恢复所属主题"):
        alice_store.moderate_content(
            "reply",
            reply["reply_id"],
            action="restore",
            reason="尝试提前恢复回复",
            moderator_name="Alice",
        )
    alice_store.moderate_content(
        "topic",
        topic["topic_id"],
        action="restore",
        reason="主题复核通过",
        moderator_name="Alice",
    )
    alice_store.moderate_content(
        "reply",
        reply["reply_id"],
        action="restore",
        reason="回复复核通过",
        moderator_name="Alice",
    )
    updated = alice_store.update_reply(
        reply["reply_id"],
        body="管理员修正了这条回复。",
        viewer_is_admin=True,
        moderator_name="Alice",
        moderation_reason="移除不合适的表述",
    )
    assert updated["body"] == "管理员修正了这条回复。"
    assert alice_store.delete_reply(
        reply["reply_id"], viewer_is_admin=True, moderator_name="Alice"
    ) is True
    with pytest.raises(ForumForbidden, match="已经删除"):
        alice_store.moderate_content(
            "reply",
            reply["reply_id"],
            action="delete",
            reason="不能重复删除",
            moderator_name="Alice",
        )

    deleted = alice_store.admin_content(
        target_type="reply", status="deleted", limit=10
    )
    assert deleted["total"] == 1
    assert deleted["items"][0]["target_id"] == reply["reply_id"]
    assert deleted["items"][0]["raw_status"] == "deleted"
    actions = [item["action"] for item in deleted["audit"]]
    assert actions[:6] == ["delete", "edit", "restore", "restore", "hide", "hide"]
    assert deleted["audit"][0]["moderator_id"] == alice["user_id"]
    assert deleted["audit"][0]["moderator_name"] == "Alice"

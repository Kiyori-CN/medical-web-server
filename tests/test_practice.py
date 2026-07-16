from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import UTC, datetime, time, timedelta, timezone

import pytest
from flask import Flask, g

from medical_app.builder import build_catalog
from medical_app.catalog import Catalog
from medical_app.practice import PracticeConflict, PracticeForbidden, PracticeStore
from medical_app.practice_api import create_practice_blueprint


def _services(sample_source_tree: Path, tmp_path: Path, profile_id: str = "usr_test"):
    manifest = build_catalog(sample_source_tree, tmp_path / "builds")
    catalog_path = tmp_path / "builds" / manifest["build_id"] / "catalog.sqlite3"
    catalog = Catalog(catalog_path)
    store = PracticeStore(tmp_path / "state" / "app.sqlite3", catalog, profile_id)
    store.initialize()
    return catalog, store


def _file_collection(catalog: Catalog, suffix: str) -> dict:
    return next(
        item for item in catalog.list_collections() if item["relative_path"].endswith(suffix)
    )


def test_practice_session_hides_answers_tracks_wrong_and_reveals_after_finish(
    sample_source_tree: Path, tmp_path: Path
):
    _, store = _services(sample_source_tree, tmp_path)
    session = store.create_session(count=1, filters={"question_type": "单选题"})

    item = session["items"][0]
    assert "answer" not in item["question"]
    assert "explanation" not in item["question"]

    result = store.submit_answer(
        session["session_id"], position=item["position"], answer="B", elapsed_ms=1200
    )
    assert result["is_correct"] is False
    assert result["correct_answer"] == "A"

    completed = store.get_session(session["session_id"])
    assert completed["status"] == "complete"
    assert completed["summary"]["wrong"] == 1
    assert completed["solution_visible"] is True
    assert completed["items"][0]["question"]["answer"] == "A"

    wrong_library = store.saved_library("wrong")
    assert wrong_library["total_saved"] == 1
    assert wrong_library["directories"][0]["name"] == "执业医师"

    with pytest.raises(PracticeConflict):
        store.submit_answer(session["session_id"], position=1, answer="A")


def test_practice_rejects_invalid_choice_and_serializes_concurrent_submissions(
    sample_source_tree: Path, tmp_path: Path
):
    _, store = _services(sample_source_tree, tmp_path)
    session = store.create_session(count=1, filters={"question_type": "单选题"})
    session_id = session["session_id"]

    with pytest.raises(ValueError):
        store.submit_answer(session_id, position=1, answer="Z")

    def submit(answer: str):
        try:
            return store.submit_answer(session_id, position=1, answer=answer)
        except PracticeConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ("A", "B")))

    assert results.count("conflict") == 1
    assert sum(isinstance(result, dict) for result in results) == 1
    assert store.health()["attempts"] == 1


def test_practice_blueprint_requires_login_and_csrf(
    sample_source_tree: Path, tmp_path: Path
):
    catalog, store = _services(sample_source_tree, tmp_path)
    app = Flask(__name__)

    @app.before_request
    def authenticate():
        g.current_user = (
            {"user_id": "usr_test", "role": "user"}
            if app.config.get("AUTHENTICATED")
            else None
        )

    app.register_blueprint(create_practice_blueprint(catalog.database_path, store.database_path))
    client = app.test_client()
    assert client.get("/api/practice/health").status_code == 401
    assert client.get("/api/study/goals").status_code == 401
    assert client.get("/api/study/sessions").status_code == 401

    app.config["AUTHENTICATED"] = True
    client.set_cookie("medical_csrf", "csrf-test")
    headers = {"X-CSRF-Token": "csrf-test"}
    assert client.get("/api/practice/health").status_code == 200
    assert client.get("/api/study/goals").get_json()["daily_question_target"] == 20
    assert client.put(
        "/api/study/goals",
        json={"daily_question_target": 40, "daily_duration_target_minutes": 60},
    ).status_code == 403
    updated_goal = client.put(
        "/api/study/goals",
        headers=headers,
        json={"daily_question_target": 40, "daily_duration_target_minutes": 60},
    )
    assert updated_goal.status_code == 200
    assert updated_goal.get_json()["daily_duration_target_minutes"] == 60
    assert client.post("/api/practice/sessions", json={"count": 1}).status_code == 403

    created = client.post(
        "/api/practice/sessions",
        headers=headers,
        json={"count": 1, "filters": {"question_type": "单选题"}},
    )
    assert created.status_code == 201
    session = created.get_json()
    submitted = client.post(
        f"/api/practice/sessions/{session['session_id']}/answers",
        headers=headers,
        json={"position": 1, "answer": "B"},
    )
    assert submitted.status_code == 200
    assert submitted.get_json()["is_correct"] is False
    history = client.get("/api/study/sessions?status=complete")
    assert history.status_code == 200
    assert history.get_json()["stats"]["complete"] == 1
    assert client.get("/api/study/sessions?status=deleted").status_code == 400
    assert client.get("/api/study/sessions?limit=invalid").status_code == 400


def test_study_goals_use_shanghai_day_boundary_and_track_streak(
    sample_source_tree: Path, tmp_path: Path
):
    _, store = _services(sample_source_tree, tmp_path)
    assert store.study_goal()["daily_question_target"] == 20
    goal = store.update_study_goal(2, 20)
    assert goal["daily_question_target"] == 2
    assert goal["time_zone"] == "Asia/Shanghai"
    other_store = PracticeStore(store.database_path, store.catalog, "other-user")
    other_store.initialize()
    assert other_store.study_goal()["daily_question_target"] == 20
    with pytest.raises(ValueError):
        store.update_study_goal(0, 20)
    with pytest.raises(ValueError):
        store.update_study_goal(20, 1441)

    sessions = []
    for _ in range(2):
        session = store.create_session(count=1, filters={"question_type": "单选题"})
        store.submit_answer(
            session["session_id"], position=1, answer="A", elapsed_ms=600_000
        )
        sessions.append(session["session_id"])

    shanghai = timezone(timedelta(hours=8), "Asia/Shanghai")
    today = datetime.now(shanghai).date()
    today_at_0030 = datetime.combine(today, time(0, 30), tzinfo=shanghai).astimezone(UTC)
    yesterday_at_noon = datetime.combine(
        today - timedelta(days=1), time(12, 0), tzinfo=shanghai
    ).astimezone(UTC)
    connection = store.connect()
    try:
        with connection:
            connection.execute(
                "UPDATE practice_session_questions SET answered_at = ? WHERE session_id = ?",
                (today_at_0030.isoformat(), sessions[0]),
            )
            connection.execute(
                "UPDATE practice_session_questions SET answered_at = ? WHERE session_id = ?",
                (yesterday_at_noon.isoformat(), sessions[1]),
            )
    finally:
        connection.close()

    summary = store.today_summary()
    assert summary["date"] == today.isoformat()
    assert summary["questions"] == 1
    assert summary["duration_minutes"] == 10
    assert summary["streak_days"] == 2
    assert summary["goal"]["question_progress"] == 50
    assert summary["goal"]["duration_progress"] == 50
    assert summary["goal"]["completed"] is False
    assert [item["questions"] for item in summary["weekly_trend"][-2:]] == [1, 1]


def test_session_history_is_paginated_account_scoped_and_test_safe(
    sample_source_tree: Path, tmp_path: Path
):
    catalog, store = _services(sample_source_tree, tmp_path)
    active_test = store.create_session(
        count=2, filters={"question_type": "单选题"}, mode="test"
    )
    store.submit_answer(active_test["session_id"], position=1, answer="B")
    completed = store.create_session(
        count=1, filters={"question_type": "单选题"}, mode="practice"
    )
    store.submit_answer(completed["session_id"], position=1, answer="B")
    other = PracticeStore(store.database_path, catalog, "other-user")
    other.initialize()
    other.create_session(count=1, filters={"question_type": "单选题"})

    history = store.session_history(limit=1)
    assert history["total"] == 2
    assert history["has_more"] is True
    assert history["stats"] == {"total": 2, "active": 1, "complete": 1}
    second_page = store.session_history(limit=1, offset=1)
    assert len(second_page["items"]) == 1
    active = store.session_history(status="active")
    assert active["total"] == 1
    assert active["items"][0]["session_id"] == active_test["session_id"]
    assert active["items"][0]["summary"]["answered"] == 1
    assert active["items"][0]["summary"]["correct"] is None
    assert active["items"][0]["summary"]["accuracy"] is None
    complete = store.session_history(status="complete")
    assert complete["total"] == 1
    assert complete["items"][0]["session_id"] == completed["session_id"]
    assert complete["items"][0]["summary"]["wrong"] == 1
    assert other.session_history()["total"] == 1
    with pytest.raises(ValueError):
        store.session_history(status="deleted")


def test_collection_order_memorize_mode_and_early_finish(
    sample_source_tree: Path, tmp_path: Path
):
    catalog, store = _services(sample_source_tree, tmp_path)
    collection = _file_collection(catalog, "第一章.xlsx")
    outline = store.collection_outline(collection["collection_id"])
    assert [item["collection_position"] for item in outline["items"]] == [1, 2, 3, 4]
    assert outline["items"][0]["type_label"] == "A1型题"

    memorize = store.create_session(
        collection_id=collection["collection_id"],
        count=None,
        mode="memorize",
        start_position=2,
    )
    assert [item["collection_position"] for item in memorize["items"]] == [2, 3, 4]
    assert memorize["solution_visible"] is True
    assert all("answer" in item["question"] for item in memorize["items"])

    practice = store.create_session(
        collection_id=collection["collection_id"], count=None, mode="practice"
    )
    finished = store.finish_session(practice["session_id"])
    assert finished["status"] == "complete"
    assert finished["summary"]["answered"] == 0
    assert finished["summary"]["total"] == 4
    assert finished["duration_ms"] >= 0
    assert all("answer" in item["question"] for item in finished["items"])


def test_speed_and_test_modes_have_distinct_feedback_contract(
    sample_source_tree: Path, tmp_path: Path
):
    catalog, store = _services(sample_source_tree, tmp_path)
    collection = _file_collection(catalog, "第一章.xlsx")

    speed = store.create_session(
        collection_id=collection["collection_id"], count=1, mode="speed"
    )
    speed_result = store.submit_answer(speed["session_id"], position=1, answer="B")
    assert speed_result["is_correct"] is False
    assert speed_result["correct_answer"] == "A"

    test = store.create_session(
        collection_id=collection["collection_id"], count=2, mode="test"
    )
    assert test["mode"] == "test"
    assert "answer" not in test["items"][0]["question"]
    test_result = store.submit_answer(test["session_id"], position=1, answer="A")
    assert "is_correct" not in test_result
    active = store.get_session(test["session_id"])
    assert active["summary"]["correct"] is None
    assert active["items"][0]["submission"]["answer"] == "A"
    assert active["items"][0]["submission"]["correct_answer"] == ""

    finished = store.finish_session(test["session_id"])
    assert finished["summary"]["correct"] == 1
    assert finished["items"][0]["question"]["answer"] == "A"


def test_favorites_and_wrong_items_are_account_scoped_and_path_preserving(
    sample_source_tree: Path, tmp_path: Path
):
    catalog, alice = _services(sample_source_tree, tmp_path, "usr_alice")
    bob = PracticeStore(alice.database_path, catalog, "usr_bob")
    collection = _file_collection(catalog, "第一章.xlsx")
    occurrence = catalog.collection_outline(collection["collection_id"])[0]

    favorite = alice.add_favorite(occurrence_id=occurrence["occurrence_id"])
    assert favorite["relative_path"].endswith("第一章.xlsx")
    assert alice.saved_library("favorite")["total_saved"] == 1
    assert bob.saved_library("favorite")["total_saved"] == 0
    assert alice.favorite_occurrence_ids([favorite["occurrence_id"]]) == [
        favorite["occurrence_id"]
    ]

    toggled_off = alice.toggle_favorite(occurrence_id=favorite["occurrence_id"])
    assert toggled_off["favorited"] is False
    assert alice.saved_library("favorite")["total_saved"] == 0
    toggled_on = alice.toggle_favorite(occurrence_id=favorite["occurrence_id"])
    assert toggled_on["favorited"] is True

    saved_outline = alice.saved_outline("favorite", collection["collection_id"])
    assert [item["occurrence_id"] for item in saved_outline["items"]] == [
        occurrence["occurrence_id"]
    ]
    session = alice.create_session(
        collection_id=collection["collection_id"], saved_kind="favorite", count=None
    )
    assert len(session["items"]) == 1
    assert session["saved_kind"] == "favorite"
    assert alice.remove_saved_item("favorite", favorite["item_key"]) is True
    assert alice.saved_library("favorite")["total_saved"] == 0


def test_file_practice_uses_exact_occurrence_answer_and_scoped_progress(
    sample_source_tree: Path, tmp_path: Path
):
    catalog, store = _services(sample_source_tree, tmp_path)
    conflict = _file_collection(catalog, "重复题.xlsx")
    canonical = _file_collection(catalog, "第一章.xlsx")

    session = store.create_session(collection_id=conflict["collection_id"], count=None)
    question = session["items"][0]["question"]
    assert question["sources"][0]["relative_path"].endswith("重复题.xlsx")
    result = store.submit_answer(session["session_id"], position=1, answer="B")
    assert result["is_correct"] is True
    assert result["correct_answer"] == "B"
    assert store.collection_outline(conflict["collection_id"])["items"][0]["status"] == "correct"
    assert store.collection_outline(canonical["collection_id"])["items"][0]["status"] == "unanswered"


def test_collection_progress_reset_is_account_and_collection_scoped_without_deleting_records(
    sample_source_tree: Path, tmp_path: Path
):
    catalog, alice = _services(sample_source_tree, tmp_path, "usr_alice")
    bob = PracticeStore(alice.database_path, catalog, "usr_bob")
    collection = _file_collection(catalog, "第一章.xlsx")
    occurrence = catalog.collection_outline(collection["collection_id"])[0]

    alice_session = alice.create_session(
        collection_id=collection["collection_id"], count=1
    )
    alice.submit_answer(alice_session["session_id"], position=1, answer="B")
    alice.add_favorite(occurrence_id=occurrence["occurrence_id"])
    alice.create_comment(
        occurrence["occurrence_id"], author_name="Alice", body="这道题需要注意被动转运。"
    )

    bob_session = bob.create_session(collection_id=collection["collection_id"], count=1)
    bob.submit_answer(bob_session["session_id"], position=1, answer="A")

    reset = alice.reset_collection_progress(collection["collection_id"])
    assert reset["reset_count"] == 1
    assert reset["summary"]["answered"] == 0
    assert alice.collection_outline(collection["collection_id"])["items"][0]["status"] == "unanswered"
    assert bob.collection_outline(collection["collection_id"])["items"][0]["status"] == "correct"

    history = alice.get_session(alice_session["session_id"])
    assert history["items"][0]["submission"]["is_correct"] is False
    assert alice.saved_library("favorite")["total_saved"] == 1
    assert alice.saved_library("wrong")["total_saved"] == 1
    assert alice.list_comments(occurrence["occurrence_id"])["count"] == 1


def test_question_comments_are_occurrence_bound_validated_and_permission_scoped(
    sample_source_tree: Path, tmp_path: Path
):
    catalog, alice = _services(sample_source_tree, tmp_path, "usr_alice")
    bob = PracticeStore(alice.database_path, catalog, "usr_bob")
    collection = _file_collection(catalog, "第一章.xlsx")
    occurrence = catalog.collection_outline(collection["collection_id"])[0]

    created = alice.create_comment(
        occurrence["occurrence_id"], author_name="Alice", body="  第一行\r\n第二行  "
    )
    assert created["question_id"] == occurrence["question_id"]
    assert created["body"] == "第一行\n第二行"
    assert alice.list_comments(occurrence["occurrence_id"])["items"][0]["can_delete"] is True
    assert bob.list_comments(occurrence["occurrence_id"])["items"][0]["can_delete"] is False

    with pytest.raises(ValueError):
        alice.create_comment(occurrence["occurrence_id"], author_name="Alice", body="   ")
    with pytest.raises(ValueError):
        alice.create_comment(occurrence["occurrence_id"], author_name="Alice", body={})
    with pytest.raises(ValueError):
        alice.create_comment(occurrence["occurrence_id"], author_name="Alice", body="x" * 2001)
    with pytest.raises(PracticeForbidden):
        bob.delete_comment(created["comment_id"])

    assert bob.delete_comment(created["comment_id"], is_admin=True) is True
    assert alice.list_comments(occurrence["occurrence_id"])["count"] == 0


def test_private_notes_and_comment_feeds_are_account_scoped(
    sample_source_tree: Path, tmp_path: Path
):
    catalog, alice = _services(sample_source_tree, tmp_path, "usr_alice")
    bob = PracticeStore(alice.database_path, catalog, "usr_bob")
    collection = _file_collection(catalog, "第一章.xlsx")
    occurrence = catalog.collection_outline(collection["collection_id"])[0]

    note = alice.save_note(occurrence["occurrence_id"], "记住这是易化扩散。")
    assert note["exists"] is True
    assert alice.get_note(occurrence["occurrence_id"])["body"] == "记住这是易化扩散。"
    assert bob.get_note(occurrence["occurrence_id"])["exists"] is False
    assert alice.my_notes()["items"][0]["question"]["collection_id"] == collection["collection_id"]

    comment = alice.create_comment(
        occurrence["occurrence_id"], author_name="Alice", body="先看题干中的方向。"
    )
    updated = alice.update_comment(comment["comment_id"], "先判断物质和浓度梯度。")
    assert updated["edited"] is True
    assert alice.my_comments()["items"][0]["body"] == "先判断物质和浓度梯度。"
    with pytest.raises(PracticeForbidden):
        bob.update_comment(comment["comment_id"], "越权修改")
    assert alice.delete_note(occurrence["occurrence_id"]) is True


def test_progress_reset_and_comment_routes_require_login_csrf_and_enforce_permissions(
    sample_source_tree: Path, tmp_path: Path
):
    catalog, store = _services(sample_source_tree, tmp_path)
    collection = _file_collection(catalog, "第一章.xlsx")
    occurrence = catalog.collection_outline(collection["collection_id"])[0]
    app = Flask(__name__)

    @app.before_request
    def authenticate():
        g.current_user = app.config.get("CURRENT_USER")

    app.register_blueprint(create_practice_blueprint(catalog.database_path, store.database_path))
    client = app.test_client()
    reset_path = f"/api/practice/collections/{collection['collection_id']}/progress/reset"
    assert client.post(reset_path).status_code == 401

    app.config["CURRENT_USER"] = {
        "user_id": "usr_test",
        "username": "Test User",
        "role": "user",
    }
    client.set_cookie("medical_csrf", "csrf-test")
    headers = {"X-CSRF-Token": "csrf-test"}
    assert client.post(reset_path).status_code == 403
    assert client.post(reset_path, headers=headers).status_code == 200
    assert client.post(
        "/api/study/comments",
        headers=headers,
        json={"occurrence_id": occurrence["occurrence_id"], "body": "接口评论"},
    ).status_code == 201
    listed = client.get(
        f"/api/study/comments?occurrence_id={occurrence['occurrence_id']}"
    ).get_json()
    assert listed["count"] == 1
    comment_id = listed["items"][0]["comment_id"]

    app.config["CURRENT_USER"] = {
        "user_id": "usr_other",
        "username": "Other User",
        "role": "user",
    }
    assert client.delete(
        f"/api/study/comments/{comment_id}", headers=headers
    ).status_code == 403
    app.config["CURRENT_USER"]["role"] = "admin"
    assert client.delete(
        f"/api/study/comments/{comment_id}", headers=headers
    ).status_code == 200

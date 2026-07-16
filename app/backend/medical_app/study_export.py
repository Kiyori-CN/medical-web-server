from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, g, jsonify, request

from .auth import login_required
from .normalize import display_text


EXPORT_COLUMNS = (
    "record_type",
    "kind",
    "item_key",
    "session_id",
    "position",
    "collection_id",
    "relative_path",
    "occurrence_id",
    "question_id",
    "wrong_count",
    "attempts",
    "correct_count",
    "wrong_count",
    "ungraded_count",
    "streak",
    "last_answer",
    "last_result",
    "last_seen_at",
    "body",
    "created_at",
    "updated_at",
    "mode",
    "filters_json",
    "total_questions",
    "status",
    "archived_at",
    "completed_at",
    "duration_ms",
    "saved_kind",
    "collection_position",
    "submitted_answer",
    "normalized_submitted_answer",
    "is_correct",
    "answered_at",
    "elapsed_ms",
    "daily_question_target",
    "daily_duration_target_minutes",
)


def create_study_export_blueprint(database_path: str | Path) -> Blueprint:
    blueprint = Blueprint("study_export", __name__, url_prefix="/api/study")
    resolved_database = Path(database_path).resolve()

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.get("/export")
    @login_required
    def export_study_data():
        export_format = display_text(request.args.get("format", "jsonl")).lower()
        if export_format not in {"jsonl", "csv"}:
            raise ValueError("学习数据导出格式仅支持 JSONL 或 CSV")
        suffix = export_format
        response = Response(
            _stream_export(resolved_database, g.current_user["user_id"], export_format),
            content_type=(
                "application/x-ndjson; charset=utf-8"
                if export_format == "jsonl"
                else "text/csv; charset=utf-8"
            ),
        )
        response.headers["Content-Disposition"] = (
            f'attachment; filename="medical-study-{suffix}.{suffix}"'
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    return blueprint


def _connect_read_only(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro", uri=True, timeout=10
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _stream_export(
    database_path: Path, profile_id: str, export_format: str
) -> Iterator[str]:
    with _connect_read_only(database_path) as connection:
        records = _iter_records(connection, profile_id)
        if export_format == "csv":
            yield from _stream_csv(records)
        else:
            yield from _stream_jsonl(records)


def _iter_records(
    connection: sqlite3.Connection, profile_id: str
) -> Iterator[dict[str, Any]]:
    queries = (
        (
            "study_goal",
            """
            SELECT daily_question_target, daily_duration_target_minutes, updated_at
            FROM study_goals
            WHERE profile_id = ?
            """,
        ),
        (
            "study_item",
            """
            SELECT kind, item_key, occurrence_id, question_id, collection_id,
                   relative_path, wrong_count, created_at, updated_at
            FROM study_items
            WHERE profile_id = ?
            ORDER BY updated_at DESC, kind, item_key
            """,
        ),
        (
            "note",
            """
            SELECT occurrence_id, question_id, body, created_at, updated_at
            FROM question_notes
            WHERE profile_id = ?
            ORDER BY updated_at DESC, occurrence_id
            """,
        ),
        (
            "progress",
            """
            SELECT question_id, attempts, correct_count, wrong_count,
                   ungraded_count, streak, last_answer, last_result, last_seen_at
            FROM question_progress
            WHERE profile_id = ?
            ORDER BY last_seen_at DESC, question_id
            """,
        ),
        (
            "collection_progress",
            """
            SELECT collection_id, occurrence_id, attempts, correct_count,
                   wrong_count, ungraded_count, streak,
                   last_answer, last_result, last_seen_at
            FROM collection_question_progress
            WHERE profile_id = ?
            ORDER BY last_seen_at DESC, collection_id, occurrence_id
            """,
        ),
        (
            "session",
            """
            SELECT session_id, collection_id, mode, filters_json, total_questions,
                   status, created_at, archived_at, completed_at, duration_ms, saved_kind
            FROM practice_sessions
            WHERE profile_id = ?
            ORDER BY created_at DESC, session_id
            """,
        ),
        (
            "session_question",
            """
            SELECT q.session_id, q.position, s.collection_id, q.question_id,
                   q.occurrence_id, q.collection_position, q.submitted_answer,
                   q.normalized_submitted_answer, q.is_correct, q.answered_at,
                   q.elapsed_ms
            FROM practice_session_questions q
            JOIN practice_sessions s ON s.session_id = q.session_id
            WHERE s.profile_id = ?
            ORDER BY q.session_id, q.position
            """,
        ),
    )
    for record_type, query in queries:
        for row in connection.execute(query, (profile_id,)):
            payload = {"record_type": record_type, **dict(row)}
            yield payload


def _stream_jsonl(records: Iterator[dict[str, Any]]) -> Iterator[str]:
    for record in records:
        yield json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"


def _stream_csv(records: Iterator[dict[str, Any]]) -> Iterator[str]:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    buffer.write("\ufeff")
    writer.writeheader()
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    for record in records:
        writer.writerow(
            {
                column: _csv_safe(record.get(column, ""))
                for column in EXPORT_COLUMNS
            }
        )
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value

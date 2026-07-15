from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from .auth import admin_required
from .normalize import display_text


EXPORT_COLUMNS = (
    "occurrence_id",
    "question_id",
    "collection_id",
    "relative_path",
    "extension",
    "exam",
    "collection_kind",
    "subject",
    "title",
    "year",
    "sheet_name",
    "row_number",
    "source_position",
    "canonical_type",
    "raw_type",
    "source_code",
    "stem",
    "option_a",
    "option_b",
    "option_c",
    "option_d",
    "option_e",
    "option_f",
    "option_g",
    "option_h",
    "answer",
    "point",
    "explanation",
)


def create_catalog_export_blueprint(database_path: str | Path) -> Blueprint:
    blueprint = Blueprint(
        "catalog_export", __name__, url_prefix="/api/admin/catalog"
    )
    resolved_database = Path(database_path).resolve()

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.get("/export")
    @admin_required
    def export_catalog():
        export_format = display_text(request.args.get("format", "jsonl")).lower()
        if export_format not in {"jsonl", "csv"}:
            raise ValueError("导出格式仅支持 JSONL 或 CSV")
        collection_id = display_text(request.args.get("collection_id", ""))
        if len(collection_id) > 80:
            raise ValueError("题库文件标识无效")
        if collection_id and not _collection_exists(resolved_database, collection_id):
            raise ValueError("题库文件不存在")

        suffix = export_format
        scope = "collection" if collection_id else "all"
        response = Response(
            _stream_export(resolved_database, export_format, collection_id),
            content_type=(
                "application/x-ndjson; charset=utf-8"
                if export_format == "jsonl"
                else "text/csv; charset=utf-8"
            ),
        )
        response.headers["Content-Disposition"] = (
            f'attachment; filename="medical-catalog-{scope}.{suffix}"'
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


def _collection_exists(database_path: Path, collection_id: str) -> bool:
    with _connect_read_only(database_path) as connection:
        return (
            connection.execute(
                "SELECT 1 FROM collections WHERE collection_id = ?", (collection_id,)
            ).fetchone()
            is not None
        )


def _stream_export(
    database_path: Path, export_format: str, collection_id: str
) -> Iterator[str]:
    with _connect_read_only(database_path) as connection:
        cursor = connection.execute(
            _export_query(bool(collection_id)),
            (collection_id,) if collection_id else (),
        )
        if export_format == "csv":
            yield from _stream_csv(cursor)
        else:
            yield from _stream_jsonl(cursor)


def _export_query(filtered: bool) -> str:
    option_columns = ",\n".join(
        f"COALESCE(json_extract(qv.options_json, '$[{position - 1}]'), '') AS option_{label.lower()}"
        for position, label in enumerate("ABCDEFGH", start=1)
    )
    where = "WHERE c.collection_id = ?" if filtered else ""
    return f"""
        SELECT qo.occurrence_id, q.question_id, c.collection_id,
               c.relative_path, c.extension, c.exam, c.collection_kind,
               c.subject, c.title, c.year, qo.sheet_name, qo.row_number,
               cq.position AS source_position, q.canonical_type, qv.raw_type,
               qv.source_code, qv.stem,
               {option_columns},
               qv.answer, qv.point, qv.explanation
        FROM collection_questions cq
        JOIN collections c ON c.collection_id = cq.collection_id
        JOIN question_occurrences qo ON qo.occurrence_id = cq.occurrence_id
        JOIN questions q ON q.question_id = cq.question_id
        JOIN question_versions qv ON qv.version_id = qo.version_id
        {where}
        ORDER BY c.relative_path, cq.position, qo.occurrence_id
    """


def _stream_csv(cursor: sqlite3.Cursor) -> Iterator[str]:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    buffer.write("\ufeff")
    writer.writeheader()
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    for row in cursor:
        writer.writerow({column: _csv_safe(row[column]) for column in EXPORT_COLUMNS})
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


def _stream_jsonl(cursor: sqlite3.Cursor) -> Iterator[str]:
    for row in cursor:
        payload = {column: row[column] for column in EXPORT_COLUMNS}
        payload["options"] = [
            {"label": label, "text": payload.pop(f"option_{label.lower()}")}
            for label in "ABCDEFGH"
            if payload[f"option_{label.lower()}"]
        ]
        yield json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value

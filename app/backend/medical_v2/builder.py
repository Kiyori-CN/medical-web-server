from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .models import BuildStats, NormalizedQuestion, QuestionGroupMembership, SourceFile
from .normalize import infer_question_groups, normalize_question
from .readers import discover_sources, read_questions
from .schema import connect_build_database, create_schema, rebuild_fts, validate_database


BUILD_SCHEMA_VERSION = 3


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _stable_id(prefix: str, payload: object, length: int = 24) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:length]}"


def _snapshot_id(sources: list[SourceFile]) -> str:
    payload = [
        {
            "relative_path": source.relative_path,
            "size_bytes": source.size_bytes,
            "sha256": source.sha256,
        }
        for source in sources
    ]
    return _stable_id("snap", payload, length=32)


def _collection_id(source: SourceFile) -> str:
    return _stable_id("col", source.relative_path.rsplit(".", 1)[0], length=24)


def _write_json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _source_manifest(sources: list[SourceFile]) -> list[dict]:
    return [
        {
            "relative_path": source.relative_path,
            "extension": source.extension,
            "size_bytes": source.size_bytes,
            "sha256": source.sha256,
            "exam": source.exam,
            "collection_kind": source.collection_kind,
            "subject": source.subject,
            "title": source.title,
            "year": source.year,
        }
        for source in sources
    ]


def _source_signature(sources: list[SourceFile]) -> list[tuple[str, int, str]]:
    return [(source.relative_path, source.size_bytes, source.sha256) for source in sources]


def _insert_source(connection, run_id: str, source: SourceFile) -> tuple[int, str]:
    cursor = connection.execute(
        """
        INSERT INTO source_files(
            run_id, relative_path, extension, sha256, size_bytes,
            exam, collection_kind, subject, title, year
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            source.relative_path,
            source.extension,
            source.sha256,
            source.size_bytes,
            source.exam,
            source.collection_kind,
            source.subject,
            source.title,
            source.year,
        ),
    )
    collection_id = _collection_id(source)
    connection.execute(
        """
        INSERT OR IGNORE INTO collections(
            collection_id, relative_path, exam, collection_kind, subject, title, year
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            collection_id,
            source.relative_path.rsplit(".", 1)[0],
            source.exam,
            source.collection_kind,
            source.subject,
            source.title,
            source.year,
        ),
    )
    return int(cursor.lastrowid), collection_id


def _insert_question_version(connection, question: NormalizedQuestion) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO question_versions(
            version_id, question_id, raw_type, source_code, stem, options_json,
            answer, normalized_answer, point, explanation, quality_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            question.version_id,
            question.question_id,
            question.raw_type,
            question.source_code,
            question.stem,
            json.dumps(question.options, ensure_ascii=False),
            question.answer,
            question.normalized_answer,
            question.point,
            question.explanation,
            question.quality_score,
        ),
    )


def _insert_question_group(
    connection, membership: QuestionGroupMembership | None, question_id: str
) -> None:
    if membership is None:
        return
    connection.execute(
        """
        INSERT OR IGNORE INTO question_groups(
            group_id, shared_stem, normalized_shared_stem
        ) VALUES (?, ?, ?)
        """,
        (
            membership.group_id,
            membership.shared_stem,
            membership.normalized_shared_stem,
        ),
    )
    connection.execute(
        """
        INSERT INTO question_group_members(
            group_id, question_id, position, sub_stem, normalized_sub_stem
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(group_id, question_id) DO UPDATE SET
            position = MIN(position, excluded.position),
            sub_stem = CASE
                WHEN length(excluded.sub_stem) < length(sub_stem)
                THEN excluded.sub_stem ELSE sub_stem END,
            normalized_sub_stem = CASE
                WHEN length(excluded.normalized_sub_stem) < length(normalized_sub_stem)
                THEN excluded.normalized_sub_stem ELSE normalized_sub_stem END
        """,
        (
            membership.group_id,
            question_id,
            membership.position,
            membership.sub_stem,
            membership.normalized_sub_stem,
        ),
    )


def _insert_or_update_question(connection, question: NormalizedQuestion) -> tuple[str, bool]:
    existing = connection.execute(
        """
        SELECT question_id, canonical_version_id, normalized_answer, answer, quality_score
        FROM questions WHERE fingerprint = ?
        """,
        (question.fingerprint,),
    ).fetchone()

    if existing is None:
        connection.execute(
            """
            INSERT INTO questions(
                question_id, fingerprint, canonical_type, raw_type, source_code,
                stem, normalized_stem, answer, normalized_answer, point,
                explanation, canonical_version_id, quality_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question.question_id,
                question.fingerprint,
                question.canonical_type,
                question.raw_type,
                question.source_code,
                question.stem,
                question.normalized_stem,
                question.answer,
                question.normalized_answer,
                question.point,
                question.explanation,
                question.version_id,
                question.quality_score,
            ),
        )
        for position, (text, normalized_text) in enumerate(
            zip(question.options, question.normalized_options, strict=True), start=1
        ):
            connection.execute(
                """
                INSERT INTO question_options(question_id, position, label, text, normalized_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (question.question_id, position, chr(64 + position), text, normalized_text),
            )
        _insert_question_version(connection, question)
        return question.question_id, True

    question_id = existing["question_id"]
    if question_id != question.question_id:
        raise ValueError(
            f"Fingerprint collision: {question.fingerprint} maps to {question_id} and {question.question_id}"
        )
    _insert_question_version(connection, question)

    canonical_answer = existing["normalized_answer"]
    answer_conflict = bool(
        canonical_answer
        and question.normalized_answer
        and canonical_answer != question.normalized_answer
    )
    if answer_conflict and existing["canonical_version_id"] != question.version_id:
        canonical_version_id = existing["canonical_version_id"]
        conflict_id = _stable_id(
            "conf",
            {
                "question_id": question_id,
                "canonical_version_id": canonical_version_id,
                "observed_version_id": question.version_id,
                "type": "answer_mismatch",
            },
            length=32,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO data_conflicts(
                conflict_id, question_id, canonical_version_id,
                observed_version_id, conflict_type, details_json
            ) VALUES (?, ?, ?, ?, 'answer_mismatch', ?)
            """,
            (
                conflict_id,
                question_id,
                canonical_version_id,
                question.version_id,
                json.dumps(
                    {
                        "canonical_answer": existing["answer"],
                        "observed_answer": question.answer,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )

    if not answer_conflict and question.quality_score > existing["quality_score"]:
        connection.execute(
            """
            UPDATE questions SET
                raw_type = ?, source_code = ?, stem = ?, answer = ?,
                normalized_answer = ?, point = ?, explanation = ?,
                canonical_version_id = ?, quality_score = ?
            WHERE question_id = ?
            """,
            (
                question.raw_type,
                question.source_code,
                question.stem,
                question.answer,
                question.normalized_answer,
                question.point,
                question.explanation,
                question.version_id,
                question.quality_score,
                question_id,
            ),
        )
    return question_id, False


def build_catalog(source_root: str | Path, builds_root: str | Path, force: bool = False) -> dict:
    source_root = Path(source_root).resolve()
    builds_root = Path(builds_root).resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"Source directory does not exist: {source_root}")

    started_at = _utc_now()
    sources = discover_sources(source_root)
    if not sources:
        raise ValueError(f"No supported source files found under {source_root}")
    snapshot_id = _snapshot_id(sources)
    build_id = f"v{BUILD_SCHEMA_VERSION}_{snapshot_id.removeprefix('snap_')[:20]}"
    build_dir = builds_root / build_id
    database_path = build_dir / "catalog.sqlite3"
    manifest_path = build_dir / "manifest.json"
    report_path = build_dir / "import-report.json"
    conflicts_path = build_dir / "conflict-report.json"

    if database_path.exists() and manifest_path.exists() and not force:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("snapshot_id") != snapshot_id:
            raise ValueError(f"Existing build has a different snapshot: {build_dir}")
        manifest["reused"] = True
        return manifest

    build_dir.mkdir(parents=True, exist_ok=True)
    temporary_database = build_dir / "catalog.sqlite3.tmp"
    if temporary_database.exists():
        temporary_database.unlink()

    run_id = f"run_{snapshot_id.removeprefix('snap_')}"
    stats = BuildStats(source_files=len(sources))
    type_counts: Counter[str] = Counter()
    exam_counts: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    missing_counts: Counter[str] = Counter()

    connection = connect_build_database(str(temporary_database))
    try:
        create_schema(connection, BUILD_SCHEMA_VERSION)
        connection.execute(
            """
            INSERT INTO import_runs(
                run_id, snapshot_id, schema_version, source_root,
                started_at, status, stats_json
            ) VALUES (?, ?, ?, ?, ?, 'building', '{}')
            """,
            (run_id, snapshot_id, BUILD_SCHEMA_VERSION, str(source_root), started_at),
        )

        with connection:
            for source in sources:
                extension_counts[source.extension] += 1
                exam_counts[source.exam or "未分类"] += 1
                source_file_id, collection_id = _insert_source(connection, run_id, source)
                raw_questions = list(read_questions(source))
                normalized_questions = [normalize_question(raw) for raw in raw_questions]
                group_memberships = infer_question_groups(normalized_questions)
                for raw_question, question, group_membership in zip(
                    raw_questions,
                    normalized_questions,
                    group_memberships,
                    strict=True,
                ):
                    if not question.normalized_stem:
                        raise ValueError(
                            f"Empty normalized stem: {source.relative_path} "
                            f"{raw_question.sheet_name}!{raw_question.row_number}"
                        )
                    question_id, _ = _insert_or_update_question(connection, question)
                    _insert_question_group(connection, group_membership, question_id)
                    occurrence_cursor = connection.execute(
                        """
                        INSERT INTO question_occurrences(
                            question_id, version_id, source_file_id,
                            sheet_name, row_number, position
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            question_id,
                            question.version_id,
                            source_file_id,
                            raw_question.sheet_name,
                            raw_question.row_number,
                            raw_question.position,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO collection_questions(
                            collection_id, occurrence_id, question_id, position
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            collection_id,
                            int(occurrence_cursor.lastrowid),
                            question_id,
                            raw_question.position,
                        ),
                    )
                    stats.question_occurrences += 1
                    type_counts[question.canonical_type] += 1
                    if not question.normalized_answer:
                        missing_counts["answer"] += 1
                    if not question.options:
                        missing_counts["options"] += 1
                    if not question.point:
                        missing_counts["point"] += 1
                    if not question.explanation:
                        missing_counts["explanation"] += 1

            rebuild_fts(connection)
            validation = validate_database(connection)
            stats.unique_questions = validation["counts"]["questions"]
            stats.question_versions = validation["counts"]["question_versions"]
            stats.conflicts = validation["counts"]["data_conflicts"]
            stats.collections = validation["counts"]["collections"]
            stats.question_groups = validation["counts"]["question_groups"]
            if validation["integrity_check"] != "ok" or validation["foreign_key_errors"]:
                raise ValueError(f"Database validation failed: {validation}")
            completed_at = _utc_now()
            connection.execute(
                """
                UPDATE import_runs
                SET completed_at = ?, status = 'complete', stats_json = ?
                WHERE run_id = ?
                """,
                (completed_at, json.dumps(stats.as_dict(), sort_keys=True), run_id),
            )
        connection.execute("PRAGMA optimize")
        connection.commit()
    except Exception:
        connection.close()
        if temporary_database.exists():
            temporary_database.unlink()
        raise
    else:
        connection.close()

    sources_after = discover_sources(source_root)
    if _source_signature(sources_after) != _source_signature(sources):
        if temporary_database.exists():
            temporary_database.unlink()
        raise RuntimeError("Source files changed while the catalog was being built")

    os.replace(temporary_database, database_path)
    conflict_rows = []
    report_connection = connect_build_database(str(database_path))
    try:
        conflict_rows = [
            dict(row)
            for row in report_connection.execute(
                """
                SELECT conflict_id, question_id, canonical_version_id,
                       observed_version_id, conflict_type, details_json
                FROM data_conflicts ORDER BY conflict_id
                """
            )
        ]
        final_validation = validate_database(report_connection)
    finally:
        report_connection.close()

    completed_at = _utc_now()
    report = {
        "build_id": build_id,
        "snapshot_id": snapshot_id,
        "schema_version": BUILD_SCHEMA_VERSION,
        "started_at": started_at,
        "completed_at": completed_at,
        "stats": stats.as_dict(),
        "type_counts": dict(sorted(type_counts.items())),
        "exam_counts": dict(sorted(exam_counts.items())),
        "extension_counts": dict(sorted(extension_counts.items())),
        "missing_counts": dict(sorted(missing_counts.items())),
        "validation": final_validation,
    }
    manifest = {
        "build_id": build_id,
        "snapshot_id": snapshot_id,
        "schema_version": BUILD_SCHEMA_VERSION,
        "created_at": completed_at,
        "source_root": str(source_root),
        "database": "catalog.sqlite3",
        "sources": _source_manifest(sources),
        "stats": stats.as_dict(),
        "reused": False,
    }
    _write_json_atomic(report_path, report)
    _write_json_atomic(conflicts_path, {"count": len(conflict_rows), "conflicts": conflict_rows})
    _write_json_atomic(manifest_path, manifest)
    return manifest


def activate_build(knowledge_base_root: str | Path, build_id: str) -> dict:
    root = Path(knowledge_base_root).resolve()
    if not build_id or Path(build_id).name != build_id:
        raise ValueError(f"Invalid build_id: {build_id!r}")
    build_dir = root / "builds" / build_id
    manifest_path = build_dir / "manifest.json"
    database_path = build_dir / "catalog.sqlite3"
    if not manifest_path.is_file() or not database_path.is_file():
        raise FileNotFoundError(f"Incomplete build: {build_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("build_id") != build_id:
        raise ValueError(f"Build manifest mismatch: {manifest_path}")

    connection = connect_build_database(str(database_path))
    try:
        validation = validate_database(connection)
    finally:
        connection.close()
    if validation["integrity_check"] != "ok" or validation["foreign_key_errors"]:
        raise ValueError(f"Cannot activate invalid build: {validation}")

    pointer = {
        "build_id": build_id,
        "snapshot_id": manifest["snapshot_id"],
        "schema_version": manifest["schema_version"],
        "activated_at": _utc_now(),
    }
    root.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(root / "current.json", pointer)
    return pointer

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from medical_v2.builder import activate_build, build_catalog
from medical_v2.catalog import resolve_catalog_path


def _hash_tree(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def test_build_catalog_is_structured_idempotent_and_read_only(sample_source_tree: Path, tmp_path: Path):
    before = _hash_tree(sample_source_tree)
    builds_root = tmp_path / "builds"

    first = build_catalog(sample_source_tree, builds_root)
    second = build_catalog(sample_source_tree, builds_root)

    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["build_id"] == second["build_id"]
    assert first["reused"] is False
    assert second["reused"] is True
    assert before == _hash_tree(sample_source_tree)

    build_dir = builds_root / first["build_id"]
    database_path = build_dir / "catalog.sqlite3"
    report = json.loads((build_dir / "import-report.json").read_text(encoding="utf-8"))
    conflicts = json.loads((build_dir / "conflict-report.json").read_text(encoding="utf-8"))
    pointer = activate_build(builds_root.parent, first["build_id"])
    assert pointer["build_id"] == first["build_id"]
    assert resolve_catalog_path("", builds_root.parent) == database_path.resolve()

    assert report["stats"] == {
        "collections": 3,
        "conflicts": 1,
        "question_groups": 1,
        "question_occurrences": 6,
        "question_versions": 6,
        "source_files": 3,
        "unique_questions": 5,
        "warning_count": 0,
    }
    assert conflicts["count"] == 1

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        question = connection.execute(
            "SELECT stem, answer FROM questions WHERE normalized_stem = ?",
            ("细胞膜运输方式属于",),
        ).fetchone()
        assert dict(question) == {"stem": "细胞膜运输方式属于", "answer": "A"}
        assert connection.execute("SELECT COUNT(*) FROM question_occurrences").fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM question_versions").fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM question_groups").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM question_group_members").fetchone()[0] == 2
        search_rows = connection.execute(
            """
            SELECT question_id, stem
            FROM question_fts
            WHERE question_fts MATCH ?
            ORDER BY bm25(question_fts)
            """,
            ("细胞膜",),
        ).fetchall()
        assert search_rows and search_rows[0]["stem"] == "细胞膜运输方式属于"
    finally:
        connection.close()

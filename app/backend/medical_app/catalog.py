from __future__ import annotations

import math
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from urllib.parse import quote

from .normalize import display_text, extract_source_code, key_text
from .ordering import question_type_label, sort_library_entries


_SEGMENT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+|[^\W_]+", re.UNICODE)
SEARCH_FIELDS = ("stem", "answer", "point", "explanation")


class CatalogUnavailable(RuntimeError):
    pass


def resolve_catalog_path(
    direct_path: str | Path | None,
    knowledge_base_root: str | Path,
) -> Path:
    if direct_path:
        return Path(direct_path).resolve()
    root = Path(knowledge_base_root).resolve()
    pointer = root / "current.json"
    if not pointer.is_file():
        return root / "builds" / "unavailable" / "catalog.sqlite3"
    data = json.loads(pointer.read_text(encoding="utf-8"))
    build_id = display_text(data.get("build_id"))
    if not re.fullmatch(r"catalog_[a-f0-9]{24,64}", build_id):
        raise CatalogUnavailable(f"Invalid build_id in {pointer}: {build_id!r}")
    return root / "builds" / build_id / "catalog.sqlite3"


class Catalog:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).resolve()
        self._library_cache: list[dict[str, Any]] | None = None

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if not self.database_path.is_file():
            raise CatalogUnavailable(f"Catalog database not found: {self.database_path}")
        uri = f"file:{quote(self.database_path.as_posix(), safe='/:')}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def health(self) -> dict[str, Any]:
        with self.connect() as connection:
            schema_revision = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_revision'"
            ).fetchone()
            total_questions = connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        return {
            "status": "healthy",
            "engine": "sqlite-fts5",
            "schema_revision": int(schema_revision[0]) if schema_revision else None,
            "total_questions": total_questions,
            "integrity_validation": "build-time",
        }

    def identity(self) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_id, schema_revision
                FROM import_runs
                WHERE status = 'complete'
                ORDER BY completed_at DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            raise CatalogUnavailable("Catalog does not contain a completed import run")
        return {
            "catalog_build_id": self.database_path.parent.name,
            "snapshot_id": row["snapshot_id"],
            "catalog_schema_revision": row["schema_revision"],
        }

    def stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "questions",
                    "question_occurrences",
                    "question_versions",
                    "question_groups",
                    "question_group_members",
                    "collections",
                    "source_files",
                    "data_conflicts",
                )
            }
            by_type = {
                row["canonical_type"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT canonical_type, COUNT(*) AS count
                    FROM questions GROUP BY canonical_type ORDER BY canonical_type
                    """
                )
            }
            by_question_type: dict[str, int] = {}
            for row in connection.execute(
                """
                SELECT raw_type, canonical_type, COUNT(*) AS count
                FROM questions
                GROUP BY raw_type, canonical_type
                ORDER BY raw_type, canonical_type
                """
            ):
                label = question_type_label(row["raw_type"], row["canonical_type"])
                by_question_type[label] = by_question_type.get(label, 0) + row["count"]
            by_exam = {
                row["exam"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT exam, COUNT(DISTINCT question_id) AS count
                    FROM question_occurrences qo
                    JOIN source_files sf ON sf.source_file_id = qo.source_file_id
                    GROUP BY exam ORDER BY exam
                    """
                )
            }
        return {
            "counts": counts,
            "by_type": by_type,
            "by_question_type": by_question_type,
            "by_exam": by_exam,
        }

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        canonical_type: str = "",
        canonical_types: list[str] | tuple[str, ...] | None = None,
        question_types: list[str] | tuple[str, ...] | None = None,
        exam: str = "",
        collection_kind: str = "",
        subject: str = "",
        year: int | None = None,
        include_solution: bool = True,
        search_fields: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        query = display_text(query)
        if not query:
            return []
        if len(query) > 500:
            raise ValueError("query is too long; maximum length is 500 characters")
        limit = max(1, min(int(limit), 50))
        fields = normalize_search_fields(search_fields)
        filters = {
            "canonical_type": display_text(canonical_type),
            "canonical_types": [
                display_text(value) for value in (canonical_types or []) if display_text(value)
            ],
            "question_types": [
                display_text(value) for value in (question_types or []) if display_text(value)
            ],
            "exam": display_text(exam),
            "collection_kind": display_text(collection_kind),
            "subject": display_text(subject),
            "year": year,
        }
        query_source_code, _, query_without_code = extract_source_code(query)
        normalized_query = key_text(query_without_code)

        with self.connect() as connection:
            candidates: list[tuple[str, float, str]] = []
            if "stem" in fields:
                exact = self._find_exact(connection, normalized_query, filters, limit)
                candidates.extend((question_id, 1.0, "exact") for question_id in exact)
            if query_source_code and "stem" in fields:
                source_code_rows = self._find_source_code(
                    connection, query_source_code, filters, limit
                )
                candidates.extend(
                    (question_id, 1.0, "source_code") for question_id in source_code_rows
                )

            remaining = limit - len({candidate[0] for candidate in candidates})
            if remaining > 0 and normalized_query:
                if len(normalized_query) >= 3:
                    fts_fields = [
                        field for field in fields if field in {"stem", "point", "explanation"}
                    ]
                    if fts_fields:
                        expression = _scoped_fts_expression(
                            _fts_expression(query_without_code), fts_fields
                        )
                        fts_rows = self._find_fts(
                            connection, expression, filters, max(limit * 4, 40)
                        )
                        for row in fts_rows:
                            score = _rank_score(row["rank"])
                            candidates.append((row["question_id"], score, "fts"))
                    if "answer" in fields:
                        answer_rows = self._find_contains(
                            connection,
                            query_without_code,
                            normalized_query,
                            filters,
                            ["answer"],
                            max(limit * 4, 40),
                        )
                        candidates.extend(
                            (question_id, 0.55, "answer") for question_id in answer_rows
                        )
                else:
                    short_rows = self._find_contains(
                        connection,
                        query_without_code,
                        normalized_query,
                        filters,
                        fields,
                        max(limit * 4, 40),
                    )
                    candidates.extend((question_id, 0.4, "short") for question_id in short_rows)

            deduplicated: list[tuple[str, float, str]] = []
            seen: set[str] = set()
            for candidate in candidates:
                if candidate[0] in seen:
                    continue
                seen.add(candidate[0])
                deduplicated.append(candidate)
                if len(deduplicated) >= limit:
                    break
            return self._load_questions(
                connection,
                deduplicated,
                include_solution=include_solution,
                question_types=filters["question_types"],
            )

    def get_question(self, question_id: str, *, include_solution: bool = True) -> dict[str, Any] | None:
        with self.connect() as connection:
            rows = self._load_questions(
                connection,
                [(display_text(question_id), 1.0, "id")],
                include_solution=include_solution,
            )
            return rows[0] if rows else None

    def get_questions(
        self, question_ids: list[str], *, include_solution: bool = False
    ) -> list[dict[str, Any]]:
        candidates = [
            (display_text(question_id), 1.0, "id")
            for question_id in question_ids
            if display_text(question_id)
        ]
        with self.connect() as connection:
            return self._load_questions(
                connection, candidates, include_solution=include_solution
            )

    def get_occurrence_questions(
        self, occurrence_ids: list[int], *, include_solution: bool = False
    ) -> list[dict[str, Any]]:
        """Load the exact source version for each occurrence in caller order."""
        normalized_ids = [int(value) for value in occurrence_ids]
        if not normalized_ids:
            return []
        by_occurrence: dict[int, dict[str, Any]] = {}
        with self.connect() as connection:
            for start in range(0, len(normalized_ids), 500):
                chunk = normalized_ids[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT qo.occurrence_id, qo.question_id, qo.version_id,
                           qo.sheet_name, qo.row_number, qo.position AS source_position,
                           q.canonical_type AS question_type,
                           qv.raw_type, qv.source_code, qv.stem, qv.options_json,
                           qv.answer, qv.point, qv.explanation,
                           sf.relative_path, sf.exam, sf.collection_kind,
                           sf.subject, sf.title, sf.year,
                           cq.collection_id, cq.position AS collection_position,
                           (SELECT COUNT(*) FROM question_occurrences all_qo
                            WHERE all_qo.question_id = qo.question_id) AS source_count
                    FROM question_occurrences qo
                    JOIN questions q ON q.question_id = qo.question_id
                    JOIN question_versions qv ON qv.version_id = qo.version_id
                    JOIN source_files sf ON sf.source_file_id = qo.source_file_id
                    LEFT JOIN collection_questions cq ON cq.occurrence_id = qo.occurrence_id
                    WHERE qo.occurrence_id IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
                question_ids = list(dict.fromkeys(row["question_id"] for row in rows))
                groups: dict[str, list[dict[str, Any]]] = {
                    question_id: [] for question_id in question_ids
                }
                for question_start in range(0, len(question_ids), 500):
                    question_chunk = question_ids[question_start : question_start + 500]
                    question_placeholders = ",".join("?" for _ in question_chunk)
                    for group_row in connection.execute(
                        f"""
                        SELECT qgm.question_id, qgm.group_id, qgm.position,
                               qg.shared_stem, qgm.sub_stem
                        FROM question_group_members qgm
                        JOIN question_groups qg ON qg.group_id = qgm.group_id
                        WHERE qgm.question_id IN ({question_placeholders})
                        ORDER BY qgm.question_id, qgm.group_id, qgm.position
                        """,
                        question_chunk,
                    ):
                        groups[group_row["question_id"]].append(
                            {
                                "group_id": group_row["group_id"],
                                "position": group_row["position"],
                                "shared_stem": group_row["shared_stem"],
                                "sub_stem": group_row["sub_stem"],
                            }
                        )
                for row in rows:
                    try:
                        option_values = json.loads(row["options_json"])
                    except (TypeError, json.JSONDecodeError) as error:
                        raise CatalogUnavailable(
                            f"Invalid options for occurrence {row['occurrence_id']}"
                        ) from error
                    if not isinstance(option_values, list):
                        raise CatalogUnavailable(
                            f"Invalid options for occurrence {row['occurrence_id']}"
                        )
                    item = {
                        "question_id": row["question_id"],
                        "occurrence_id": row["occurrence_id"],
                        "version_id": row["version_id"],
                        "question_type": row["question_type"],
                        "raw_type": row["raw_type"],
                        "type_label": question_type_label(
                            row["raw_type"], row["question_type"]
                        ),
                        "source_code": row["source_code"],
                        "stem": row["stem"],
                        "options": [
                            {
                                "position": position,
                                "label": chr(64 + position),
                                "text": str(text),
                            }
                            for position, text in enumerate(option_values, start=1)
                        ],
                        "source_count": row["source_count"],
                        "sources": [
                            {
                                "relative_path": row["relative_path"],
                                "exam": row["exam"],
                                "collection_kind": row["collection_kind"],
                                "subject": row["subject"],
                                "title": row["title"],
                                "year": row["year"],
                                "sheet_name": row["sheet_name"],
                                "row_number": row["row_number"],
                                "position": row["source_position"],
                                "collection_position": row["collection_position"],
                                "collection_id": row["collection_id"],
                            }
                        ],
                        "groups": groups.get(row["question_id"], []),
                        "score": 1.0,
                        "match_kind": "occurrence",
                    }
                    if include_solution:
                        item["answer"] = row["answer"]
                        item["point"] = row["point"]
                        item["explanation"] = row["explanation"]
                    by_occurrence[row["occurrence_id"]] = item
        return [
            by_occurrence[occurrence_id]
            for occurrence_id in normalized_ids
            if occurrence_id in by_occurrence
        ]

    def sample_questions(
        self,
        *,
        count: int = 10,
        collection_id: str = "",
        canonical_type: str = "",
        exam: str = "",
        collection_kind: str = "",
        subject: str = "",
        year: int | None = None,
    ) -> list[dict[str, Any]]:
        count = max(1, min(int(count), 100))
        filters = {
            "canonical_type": display_text(canonical_type),
            "exam": display_text(exam),
            "collection_kind": display_text(collection_kind),
            "subject": display_text(subject),
            "year": year,
        }
        filter_sql, params = _question_filters(filters)
        with self.connect() as connection:
            if collection_id:
                rows = connection.execute(
                    f"""
                    SELECT DISTINCT q.question_id
                    FROM questions q
                    JOIN collection_questions cq ON cq.question_id = q.question_id
                    WHERE cq.collection_id = ? {filter_sql}
                    ORDER BY random()
                    LIMIT ?
                    """,
                    [display_text(collection_id), *params, count],
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    SELECT q.question_id
                    FROM questions q
                    WHERE 1 = 1 {filter_sql}
                    ORDER BY random()
                    LIMIT ?
                    """,
                    [*params, count],
                ).fetchall()
            candidates = [(row["question_id"], 1.0, "sample") for row in rows]
            return self._load_questions(connection, candidates, include_solution=False)

    def list_collections(
        self,
        *,
        exam: str = "",
        collection_kind: str = "",
        subject: str = "",
        year: int | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where, params = _collection_filters(exam, collection_kind, subject, year)
        params.extend((max(1, min(int(limit), 500)), max(0, int(offset))))
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT c.collection_id, c.relative_path, c.extension,
                           c.exam, c.collection_kind, c.subject, c.title, c.year,
                           COUNT(cq.occurrence_id) AS question_count,
                           COUNT(DISTINCT cq.question_id) AS unique_question_count
                    FROM collections c
                    LEFT JOIN collection_questions cq ON cq.collection_id = c.collection_id
                    {where}
                    GROUP BY c.collection_id
                    ORDER BY c.exam, c.collection_kind, c.subject, c.title
                    LIMIT ? OFFSET ?
                    """,
                    params,
                )
            ]

    def get_collection(self, collection_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT c.collection_id, c.relative_path, c.extension,
                       c.exam, c.collection_kind, c.subject, c.title, c.year,
                       COUNT(cq.occurrence_id) AS question_count,
                       COUNT(DISTINCT cq.question_id) AS unique_question_count
                FROM collections c
                LEFT JOIN collection_questions cq ON cq.collection_id = c.collection_id
                WHERE c.collection_id = ?
                GROUP BY c.collection_id
                """,
                (display_text(collection_id),),
            ).fetchone()
        return dict(row) if row else None

    def browse_library(
        self,
        parent: str = "",
        *,
        collection_ids: set[str] | None = None,
        collection_counts: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        parent_path = _safe_library_path(parent)
        prefix = f"{parent_path}/" if parent_path else ""
        directories: dict[str, dict[str, Any]] = {}
        collections: list[dict[str, Any]] = []
        for row in self._library_rows():
            if collection_ids is not None and row["collection_id"] not in collection_ids:
                continue
            row = dict(row)
            if collection_counts is not None:
                saved_count = int(collection_counts.get(row["collection_id"], 0))
                row["question_count"] = saved_count
                row["unique_question_count"] = saved_count
                row["saved_count"] = saved_count
            relative_path = row["relative_path"]
            if not relative_path.startswith(prefix):
                continue
            remainder = relative_path[len(prefix) :]
            if "/" in remainder:
                directory_name = remainder.split("/", 1)[0]
                directory_path = f"{prefix}{directory_name}".strip("/")
                entry = directories.setdefault(
                    directory_name,
                    {
                        "kind": "directory",
                        "name": directory_name,
                        "path": directory_path,
                        "collection_count": 0,
                        "question_count": 0,
                    },
                )
                entry["collection_count"] += 1
                entry["question_count"] += row["question_count"]
                continue
            collections.append(
                {
                    "kind": "collection",
                    "name": PurePosixPath(relative_path).name,
                    **row,
                }
            )
        breadcrumbs = [{"name": "全部题库", "path": ""}]
        current = ""
        for part in PurePosixPath(parent_path).parts if parent_path else ():
            current = f"{current}/{part}".strip("/")
            breadcrumbs.append({"name": part, "path": current})
        return {
            "parent": parent_path,
            "breadcrumbs": breadcrumbs,
            "directories": sort_library_entries(list(directories.values()), parent_path),
            "collections": sort_library_entries(collections, parent_path),
        }

    def browse_library_tree(self, parent: str = "") -> dict[str, Any]:
        parent_path = _safe_library_path(parent)
        prefix = f"{parent_path}/" if parent_path else ""
        root: dict[str, Any] = {
            "kind": "directory",
            "name": PurePosixPath(parent_path).name if parent_path else "全部题库",
            "path": parent_path,
            "collection_count": 0,
            "question_count": 0,
            "directories": {},
            "collections": [],
        }
        for source_row in self._library_rows():
            relative_path = str(source_row["relative_path"] or "")
            if not relative_path.startswith(prefix):
                continue
            remainder = relative_path[len(prefix) :]
            if not remainder:
                continue
            parts = remainder.split("/")
            node = root
            node["collection_count"] += 1
            node["question_count"] += int(source_row["question_count"] or 0)
            current_path = parent_path
            for directory_name in parts[:-1]:
                current_path = f"{current_path}/{directory_name}".strip("/")
                child = node["directories"].setdefault(
                    directory_name,
                    {
                        "kind": "directory",
                        "name": directory_name,
                        "path": current_path,
                        "collection_count": 0,
                        "question_count": 0,
                        "directories": {},
                        "collections": [],
                    },
                )
                child["collection_count"] += 1
                child["question_count"] += int(source_row["question_count"] or 0)
                node = child
            row = dict(source_row)
            node["collections"].append(
                {"kind": "collection", "name": parts[-1], **row}
            )

        def finalize(node: dict[str, Any]) -> dict[str, Any]:
            directory_rows = [finalize(value) for value in node["directories"].values()]
            return {
                **{key: value for key, value in node.items() if key != "directories"},
                "directories": sort_library_entries(directory_rows, node["path"]),
                "collections": sort_library_entries(node["collections"], node["path"]),
            }

        return finalize(root)

    def collection_outline(self, collection_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT cq.occurrence_id, cq.position AS collection_position,
                        q.question_id, q.canonical_type AS question_type,
                        qv.raw_type, qv.source_code, qv.stem
                FROM collection_questions cq
                JOIN question_occurrences qo ON qo.occurrence_id = cq.occurrence_id
                JOIN questions q ON q.question_id = cq.question_id
                JOIN question_versions qv ON qv.version_id = qo.version_id
                WHERE cq.collection_id = ?
                ORDER BY cq.position, cq.occurrence_id
                """,
                (display_text(collection_id),),
            ).fetchall()
        return [
            {
                **dict(row),
                "type_label": question_type_label(row["raw_type"], row["question_type"]),
            }
            for row in rows
        ]

    def select_practice_items(
        self,
        *,
        collection_id: str,
        count: int | None = None,
        start_position: int = 1,
        order_mode: str = "sequential",
        canonical_type: str = "",
    ) -> list[dict[str, Any]]:
        collection_id = display_text(collection_id)
        if not collection_id:
            raise ValueError("collection_id is required")
        start_position = max(1, int(start_position))
        order_mode = display_text(order_mode) or "sequential"
        if order_mode not in {"sequential", "random"}:
            raise ValueError("order_mode must be sequential or random")
        limit = 5000 if count in (None, 0, "") else max(1, min(int(count), 5000))
        type_clause = " AND q.canonical_type = ?" if display_text(canonical_type) else ""
        params: list[Any] = [collection_id, start_position]
        if type_clause:
            params.append(display_text(canonical_type))
        params.append(limit)
        ordering = "random()" if order_mode == "random" else "cq.position, cq.occurrence_id"
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT cq.occurrence_id, cq.position AS collection_position, cq.question_id
                FROM collection_questions cq
                JOIN questions q ON q.question_id = cq.question_id
                WHERE cq.collection_id = ? AND cq.position >= ? {type_clause}
                ORDER BY {ordering}
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_ranked_questions(
        self,
        candidates: list[tuple[str, float, str]],
        *,
        include_solution: bool = True,
        canonical_type: str = "",
        canonical_types: list[str] | tuple[str, ...] | None = None,
        question_types: list[str] | tuple[str, ...] | None = None,
        exam: str = "",
        collection_kind: str = "",
        subject: str = "",
        year: int | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        filters = {
            "canonical_type": display_text(canonical_type),
            "canonical_types": [
                display_text(value) for value in (canonical_types or []) if display_text(value)
            ],
            "question_types": [
                display_text(value) for value in (question_types or []) if display_text(value)
            ],
            "exam": display_text(exam),
            "collection_kind": display_text(collection_kind),
            "subject": display_text(subject),
            "year": year,
        }
        with self.connect() as connection:
            filtered = self._filter_candidates(connection, candidates, filters, limit)
            return self._load_questions(
                connection,
                filtered,
                include_solution=include_solution,
                question_types=filters["question_types"],
            )

    def _filter_candidates(
        self,
        connection: sqlite3.Connection,
        candidates: list[tuple[str, float, str]],
        filters: dict[str, Any],
        limit: int,
    ) -> list[tuple[str, float, str]]:
        question_ids = list(dict.fromkeys(candidate[0] for candidate in candidates))
        if not question_ids:
            return []
        filter_sql, params = _question_filters(filters)
        allowed: set[str] = set()
        for start in range(0, len(question_ids), 500):
            chunk = question_ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            allowed.update(
                row["question_id"]
                for row in connection.execute(
                    f"SELECT q.question_id FROM questions q "
                    f"WHERE q.question_id IN ({placeholders}) {filter_sql}",
                    [*chunk, *params],
                )
            )
        result = []
        for candidate in candidates:
            if candidate[0] in allowed:
                result.append(candidate)
                if len(result) >= max(1, min(int(limit), 50)):
                    break
        return result

    def _library_rows(self) -> list[dict[str, Any]]:
        if self._library_cache is not None:
            return self._library_cache
        with self.connect() as connection:
            self._library_cache = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT c.collection_id, c.relative_path, c.extension,
                           c.exam, c.collection_kind, c.subject, c.title, c.year,
                           COUNT(cq.occurrence_id) AS question_count,
                           COUNT(DISTINCT cq.question_id) AS unique_question_count
                    FROM collections c
                    LEFT JOIN collection_questions cq ON cq.collection_id = c.collection_id
                    GROUP BY c.collection_id
                    ORDER BY c.relative_path
                    """
                )
            ]
        return self._library_cache

    def list_collection_questions(
        self,
        collection_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        include_solution: bool = False,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 2000))
        offset = max(0, int(offset))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT cq.occurrence_id, cq.question_id, cq.position
                FROM collection_questions cq
                WHERE cq.collection_id = ?
                ORDER BY cq.position, cq.occurrence_id
                LIMIT ? OFFSET ?
                """,
                (display_text(collection_id), limit, offset),
            ).fetchall()
        questions = self.get_occurrence_questions(
            [row["occurrence_id"] for row in rows],
            include_solution=include_solution,
        )
        for row, question in zip(rows, questions, strict=True):
            question["position"] = row["position"]
            question["match_kind"] = "collection"
        return questions

    def _find_exact(self, connection, normalized_query: str, filters: dict, limit: int) -> list[str]:
        if not normalized_query:
            return []
        filter_sql, params = _question_filters(filters)
        rows = connection.execute(
            f"""
            SELECT q.question_id
            FROM questions q
            WHERE q.normalized_stem = ? {filter_sql}
            ORDER BY q.question_id
            LIMIT ?
            """,
            [normalized_query, *params, limit],
        ).fetchall()
        return [row["question_id"] for row in rows]

    def _find_source_code(
        self, connection, source_code: str, filters: dict, limit: int
    ) -> list[str]:
        filter_sql, params = _question_filters(filters)
        rows = connection.execute(
            f"""
            SELECT q.question_id
            FROM questions q
            WHERE q.source_code = ? {filter_sql}
            ORDER BY q.question_id
            LIMIT ?
            """,
            [display_text(source_code).upper(), *params, limit],
        ).fetchall()
        return [row["question_id"] for row in rows]

    def _find_fts(self, connection, expression: str, filters: dict, limit: int):
        filter_sql, params = _question_filters(filters)
        return connection.execute(
            f"""
            SELECT q.question_id, bm25(question_fts, 0.0, 8.0, 4.0, 2.0, 1.0) AS rank
            FROM question_fts
            JOIN questions q ON q.question_id = question_fts.question_id
            WHERE question_fts MATCH ? {filter_sql}
            ORDER BY rank, q.question_id
            LIMIT ?
            """,
            [expression, *params, limit],
        ).fetchall()

    def _find_contains(
        self,
        connection,
        raw_query: str,
        normalized_query: str,
        filters: dict,
        fields: list[str] | tuple[str, ...],
        limit: int,
    ) -> list[str]:
        filter_sql, params = _question_filters(filters)
        scope_clauses: list[str] = []
        scope_params: list[str] = []
        if "stem" in fields and normalized_query:
            scope_clauses.append("instr(q.normalized_stem, ?) > 0")
            scope_params.append(normalized_query)
        for field, column in (
            ("answer", "answer"),
            ("point", "point"),
            ("explanation", "explanation"),
        ):
            if field in fields:
                scope_clauses.append(f"instr(lower(q.{column}), lower(?)) > 0")
                scope_params.append(display_text(raw_query))
        if not scope_clauses:
            return []
        rows = connection.execute(
            f"""
            SELECT q.question_id
            FROM questions q
            WHERE ({' OR '.join(scope_clauses)}) {filter_sql}
            ORDER BY length(q.normalized_stem), q.question_id
            LIMIT ?
            """,
            [*scope_params, *params, limit],
        ).fetchall()
        return [row["question_id"] for row in rows]

    def _load_questions(
        self,
        connection: sqlite3.Connection,
        candidates: list[tuple[str, float, str]],
        *,
        include_solution: bool,
        question_types: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        question_ids = [candidate[0] for candidate in candidates]
        placeholders = ",".join("?" for _ in question_ids)
        rows = {
            row["question_id"]: row
            for row in connection.execute(
                f"""
                SELECT question_id, canonical_type, raw_type, source_code,
                       stem, answer, point, explanation
                FROM questions WHERE question_id IN ({placeholders})
                """,
                question_ids,
            )
        }
        options: dict[str, list[dict[str, Any]]] = {question_id: [] for question_id in question_ids}
        for row in connection.execute(
            f"""
            SELECT question_id, position, label, text
            FROM question_options
            WHERE question_id IN ({placeholders})
            ORDER BY question_id, position
            """,
            question_ids,
        ):
            options[row["question_id"]].append(
                {"position": row["position"], "label": row["label"], "text": row["text"]}
            )
        source_counts = {
            row["question_id"]: row["source_count"]
            for row in connection.execute(
                f"""
                SELECT question_id, COUNT(*) AS source_count
                FROM question_occurrences
                WHERE question_id IN ({placeholders})
                GROUP BY question_id
                """,
                question_ids,
            )
        }
        matched_raw_types: dict[str, list[str]] = {question_id: [] for question_id in question_ids}
        requested_type_keys = {
            _question_type_key(value)
            for value in (question_types or [])
            if _question_type_key(value)
        }
        if requested_type_keys:
            for row in connection.execute(
                f"""
                SELECT qo.question_id, qv.raw_type
                FROM question_occurrences qo
                JOIN question_versions qv ON qv.version_id = qo.version_id
                WHERE qo.question_id IN ({placeholders})
                ORDER BY qo.question_id, qo.occurrence_id
                """,
                question_ids,
            ):
                raw_type = str(row["raw_type"] or "")
                if _question_type_key(raw_type) in requested_type_keys:
                    matched_raw_types[row["question_id"]].append(raw_type)
        sources: dict[str, list[dict[str, Any]]] = {question_id: [] for question_id in question_ids}
        for row in connection.execute(
            f"""
            SELECT qo.question_id, qo.occurrence_id, sf.relative_path, sf.exam, sf.collection_kind,
                   sf.subject, sf.title, sf.year, qo.sheet_name, qo.row_number,
                   qo.position, cq.collection_id
            FROM question_occurrences qo
            JOIN source_files sf ON sf.source_file_id = qo.source_file_id
            LEFT JOIN collection_questions cq ON cq.occurrence_id = qo.occurrence_id
            WHERE qo.question_id IN ({placeholders})
            ORDER BY qo.question_id, sf.relative_path, qo.sheet_name, qo.row_number
            """,
            question_ids,
        ):
            sources[row["question_id"]].append(
                {
                    "occurrence_id": row["occurrence_id"],
                    "relative_path": row["relative_path"],
                    "exam": row["exam"],
                    "collection_kind": row["collection_kind"],
                    "subject": row["subject"],
                    "title": row["title"],
                    "year": row["year"],
                    "sheet_name": row["sheet_name"],
                    "row_number": row["row_number"],
                    "position": row["position"],
                    "collection_id": row["collection_id"],
                }
            )
        groups: dict[str, list[dict[str, Any]]] = {question_id: [] for question_id in question_ids}
        for row in connection.execute(
            f"""
            SELECT qgm.question_id, qgm.group_id, qgm.position,
                   qg.shared_stem, qgm.sub_stem
            FROM question_group_members qgm
            JOIN question_groups qg ON qg.group_id = qgm.group_id
            WHERE qgm.question_id IN ({placeholders})
            ORDER BY qgm.question_id, qgm.group_id, qgm.position
            """,
            question_ids,
        ):
            groups[row["question_id"]].append(
                {
                    "group_id": row["group_id"],
                    "position": row["position"],
                    "shared_stem": row["shared_stem"],
                    "sub_stem": row["sub_stem"],
                }
            )
        result = []
        for question_id, score, match_kind in candidates:
            row = rows.get(question_id)
            if row is None:
                continue
            raw_type = row["raw_type"]
            if matched_raw_types.get(question_id):
                raw_type = matched_raw_types[question_id][0]
            item = {
                "question_id": question_id,
                "question_type": row["canonical_type"],
                "raw_type": raw_type,
                "type_label": question_type_label(raw_type, row["canonical_type"]),
                "source_code": row["source_code"],
                "stem": row["stem"],
                "options": options.get(question_id, []),
                "source_count": source_counts.get(question_id, 0),
                "sources": sources.get(question_id, []),
                "groups": groups.get(question_id, []),
                "score": round(float(score), 6),
                "match_kind": match_kind,
            }
            if include_solution:
                item["answer"] = row["answer"]
                item["point"] = row["point"]
                item["explanation"] = row["explanation"]
            result.append(item)
        return result


def normalize_search_fields(value: object) -> list[str]:
    if value is None:
        return list(SEARCH_FIELDS)
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("search_fields must be an array")
    fields = list(dict.fromkeys(display_text(field).lower() for field in value))
    invalid = [field for field in fields if field not in SEARCH_FIELDS]
    if invalid:
        raise ValueError("search_fields contains an unsupported field")
    if not fields:
        raise ValueError("search_fields must contain at least one field")
    return fields


def _rank_score(rank: float) -> float:
    rank = abs(float(rank))
    return 1.0 - math.exp(-rank) if rank > 0 else 0.25


def _fts_expression(query: str) -> str:
    normalized = display_text(query)
    terms: list[str] = []
    for segment in _SEGMENT_RE.findall(normalized):
        compact = key_text(segment)
        if len(compact) < 3:
            continue
        if not any("\u3400" <= character <= "\u9fff" for character in compact):
            # Keep Latin, Greek and numeric medical terms intact. Expanding a
            # nonexistent identifier into numeric trigrams can otherwise
            # recall unrelated years, dosages and question numbers.
            terms.append(compact)
        elif len(compact) == 3:
            terms.append(compact)
        else:
            grams = [compact[index : index + 3] for index in range(len(compact) - 2)]
            if len(grams) > 16:
                step = (len(grams) - 1) / 15
                grams = [grams[round(index * step)] for index in range(16)]
            terms.extend(grams)
    terms = list(dict.fromkeys(terms))
    if not terms:
        raise ValueError("query does not contain a searchable term of at least 3 characters")
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def _scoped_fts_expression(expression: str, fields: list[str]) -> str:
    columns = [field for field in fields if field in {"stem", "point", "explanation"}]
    if not columns:
        raise ValueError("search_fields does not contain an FTS field")
    return "{" + " ".join(columns) + "} : (" + expression + ")"


def _question_filters(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses = []
    params: list[Any] = []
    question_types = list(dict.fromkeys(filters.get("question_types") or []))
    canonical_types = list(dict.fromkeys(filters.get("canonical_types") or []))
    if question_types:
        type_keys = []
        include_unknown = False
        for value in question_types:
            key = _question_type_key(value)
            if key == _question_type_key("未知题型"):
                include_unknown = True
            elif key:
                type_keys.append(key)
        type_clauses = []
        if type_keys:
            placeholders = ",".join("?" for _ in type_keys)
            type_clauses.append(
                "EXISTS (SELECT 1 FROM question_occurrences qo "
                "JOIN question_versions qv ON qv.version_id = qo.version_id "
                "WHERE qo.question_id = q.question_id AND "
                "UPPER(REPLACE(REPLACE(COALESCE(qv.raw_type, ''), ' ', ''), '型题', '')) "
                f"IN ({placeholders}))"
            )
            params.extend(type_keys)
        if include_unknown:
            type_clauses.append(
                "EXISTS (SELECT 1 FROM question_occurrences qo "
                "JOIN question_versions qv ON qv.version_id = qo.version_id "
                "WHERE qo.question_id = q.question_id AND "
                "COALESCE(qv.raw_type, '') = '' AND q.canonical_type = '未知题型')"
            )
        if type_clauses:
            clauses.append("(" + " OR ".join(type_clauses) + ")")
    elif canonical_types:
        placeholders = ",".join("?" for _ in canonical_types)
        clauses.append(f"q.canonical_type IN ({placeholders})")
        params.extend(canonical_types)
    elif filters.get("canonical_type"):
        clauses.append("q.canonical_type = ?")
        params.append(filters["canonical_type"])

    source_clauses = []
    for field in ("exam", "collection_kind", "subject"):
        value = filters.get(field)
        if value:
            source_clauses.append(f"sf.{field} = ?")
            params.append(value)
    if filters.get("year") is not None:
        source_clauses.append("sf.year = ?")
        params.append(int(filters["year"]))
    if source_clauses:
        clauses.append(
            "EXISTS (SELECT 1 FROM question_occurrences qo "
            "JOIN source_files sf ON sf.source_file_id = qo.source_file_id "
            "WHERE qo.question_id = q.question_id AND "
            + " AND ".join(source_clauses)
            + ")"
        )
    return (" AND " + " AND ".join(clauses) if clauses else "", params)


def _question_type_key(value: object) -> str:
    """Return the stable key shared by raw type labels and search chips."""
    return display_text(value).replace(" ", "").upper().replace("型题", "")


def _collection_filters(
    exam: str, collection_kind: str, subject: str, year: int | None
) -> tuple[str, list[Any]]:
    clauses = []
    params: list[Any] = []
    for field, value in (
        ("exam", display_text(exam)),
        ("collection_kind", display_text(collection_kind)),
        ("subject", display_text(subject)),
    ):
        if value:
            clauses.append(f"c.{field} = ?")
            params.append(value)
    if year is not None:
        clauses.append("c.year = ?")
        params.append(int(year))
    return ("WHERE " + " AND ".join(clauses) if clauses else "", params)


def _safe_library_path(value: str) -> str:
    path = str(value or "").replace("\\", "/").strip("/")
    if not path:
        return ""
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("invalid library path")
    return "/".join(parts)

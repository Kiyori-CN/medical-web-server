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


_SEGMENT_RE = re.compile(r"[\w\u3400-\u4dbf\u4e00-\u9fff]+", re.UNICODE)


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
    if not re.fullmatch(r"v\d+_[a-f0-9]{20,64}", build_id):
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
            schema_version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            total_questions = connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        return {
            "status": "healthy",
            "engine": "sqlite-fts5",
            "schema_version": int(schema_version[0]) if schema_version else None,
            "total_questions": total_questions,
            "integrity_validation": "build-time",
        }

    def identity(self) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_id, schema_version
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
            "catalog_schema_version": row["schema_version"],
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
        return {"counts": counts, "by_type": by_type, "by_exam": by_exam}

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        canonical_type: str = "",
        exam: str = "",
        collection_kind: str = "",
        subject: str = "",
        year: int | None = None,
        include_solution: bool = True,
    ) -> list[dict[str, Any]]:
        query = display_text(query)
        if not query:
            return []
        if len(query) > 500:
            raise ValueError("query is too long; maximum length is 500 characters")
        limit = max(1, min(int(limit), 50))
        filters = {
            "canonical_type": display_text(canonical_type),
            "exam": display_text(exam),
            "collection_kind": display_text(collection_kind),
            "subject": display_text(subject),
            "year": year,
        }
        query_source_code, _, query_without_code = extract_source_code(query)
        normalized_query = key_text(query_without_code)

        with self.connect() as connection:
            candidates: list[tuple[str, float, str]] = []
            exact = self._find_exact(connection, normalized_query, filters, limit)
            candidates.extend((question_id, 1.0, "exact") for question_id in exact)
            if query_source_code:
                source_code_rows = self._find_source_code(
                    connection, query_source_code, filters, limit
                )
                candidates.extend(
                    (question_id, 1.0, "source_code") for question_id in source_code_rows
                )

            remaining = limit - len({candidate[0] for candidate in candidates})
            if remaining > 0 and normalized_query:
                if len(normalized_query) >= 3:
                    expression = _fts_expression(query_without_code)
                    fts_rows = self._find_fts(
                        connection, expression, filters, max(limit * 4, 40)
                    )
                    for row in fts_rows:
                        score = _rank_score(row["rank"])
                        candidates.append((row["question_id"], score, "fts"))
                else:
                    short_rows = self._find_short(
                        connection, normalized_query, filters, max(limit * 4, 40)
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
            return self._load_questions(connection, deduplicated, include_solution=include_solution)

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

    def browse_library(self, parent: str = "") -> dict[str, Any]:
        parent_path = _safe_library_path(parent)
        prefix = f"{parent_path}/" if parent_path else ""
        directories: dict[str, dict[str, Any]] = {}
        collections: list[dict[str, Any]] = []
        for row in self._library_rows():
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
            "directories": sorted(directories.values(), key=lambda item: item["name"].casefold()),
            "collections": sorted(collections, key=lambda item: item["name"].casefold()),
        }

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
        return [dict(row) for row in rows]

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
            "exam": display_text(exam),
            "collection_kind": display_text(collection_kind),
            "subject": display_text(subject),
            "year": year,
        }
        with self.connect() as connection:
            filtered = self._filter_candidates(connection, candidates, filters, limit)
            return self._load_questions(connection, filtered, include_solution=include_solution)

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

    def _find_short(self, connection, query: str, filters: dict, limit: int) -> list[str]:
        filter_sql, params = _question_filters(filters)
        rows = connection.execute(
            f"""
            SELECT q.question_id
            FROM questions q
            WHERE instr(q.normalized_stem, ?) > 0 {filter_sql}
            ORDER BY length(q.normalized_stem), q.question_id
            LIMIT ?
            """,
            [query, *params, limit],
        ).fetchall()
        return [row["question_id"] for row in rows]

    def _load_questions(
        self,
        connection: sqlite3.Connection,
        candidates: list[tuple[str, float, str]],
        *,
        include_solution: bool,
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
        sources: dict[str, list[dict[str, Any]]] = {question_id: [] for question_id in question_ids}
        for row in connection.execute(
            f"""
            SELECT qo.question_id, sf.relative_path, sf.exam, sf.collection_kind,
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
            item = {
                "question_id": question_id,
                "question_type": row["canonical_type"],
                "raw_type": row["raw_type"],
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
        if len(compact) == 3:
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


def _question_filters(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses = []
    params: list[Any] = []
    if filters.get("canonical_type"):
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

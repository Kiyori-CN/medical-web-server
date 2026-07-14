from __future__ import annotations

import math
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
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
                    SELECT c.collection_id, c.relative_path, c.exam, c.collection_kind,
                           c.subject, c.title, c.year,
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

    def list_collection_questions(
        self,
        collection_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        include_solution: bool = False,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT cq.question_id, MIN(cq.position) AS position
                FROM collection_questions cq
                WHERE cq.collection_id = ?
                GROUP BY cq.question_id
                ORDER BY position, cq.question_id
                LIMIT ? OFFSET ?
                """,
                (display_text(collection_id), limit, offset),
            ).fetchall()
            candidates = [(row["question_id"], 1.0, "collection") for row in rows]
            questions = self._load_questions(
                connection, candidates, include_solution=include_solution
            )
            positions = {row["question_id"]: row["position"] for row in rows}
            for question in questions:
                question["position"] = positions[question["question_id"]]
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

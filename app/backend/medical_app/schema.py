from __future__ import annotations

import sqlite3


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE import_runs (
    run_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    schema_revision INTEGER NOT NULL,
    source_root TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    stats_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE source_files (
    source_file_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES import_runs(run_id),
    relative_path TEXT NOT NULL,
    extension TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    exam TEXT NOT NULL DEFAULT '',
    collection_kind TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    year INTEGER,
    UNIQUE (run_id, relative_path)
);

CREATE TABLE collections (
    collection_id TEXT PRIMARY KEY,
    source_file_id INTEGER NOT NULL UNIQUE REFERENCES source_files(source_file_id),
    relative_path TEXT NOT NULL UNIQUE,
    extension TEXT NOT NULL,
    exam TEXT NOT NULL DEFAULT '',
    collection_kind TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    year INTEGER
);

CREATE TABLE questions (
    question_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    canonical_type TEXT NOT NULL,
    raw_type TEXT NOT NULL DEFAULT '',
    source_code TEXT NOT NULL DEFAULT '',
    stem TEXT NOT NULL,
    normalized_stem TEXT NOT NULL,
    answer TEXT NOT NULL DEFAULT '',
    normalized_answer TEXT NOT NULL DEFAULT '',
    point TEXT NOT NULL DEFAULT '',
    explanation TEXT NOT NULL DEFAULT '',
    canonical_version_id TEXT,
    quality_score INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE question_options (
    question_id TEXT NOT NULL REFERENCES questions(question_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    PRIMARY KEY (question_id, position)
);

CREATE TABLE question_versions (
    version_id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(question_id) ON DELETE CASCADE,
    raw_type TEXT NOT NULL DEFAULT '',
    source_code TEXT NOT NULL DEFAULT '',
    stem TEXT NOT NULL,
    options_json TEXT NOT NULL,
    answer TEXT NOT NULL DEFAULT '',
    normalized_answer TEXT NOT NULL DEFAULT '',
    point TEXT NOT NULL DEFAULT '',
    explanation TEXT NOT NULL DEFAULT '',
    quality_score INTEGER NOT NULL
);

CREATE TABLE question_groups (
    group_id TEXT PRIMARY KEY,
    shared_stem TEXT NOT NULL,
    normalized_shared_stem TEXT NOT NULL UNIQUE
);

CREATE TABLE question_group_members (
    group_id TEXT NOT NULL REFERENCES question_groups(group_id) ON DELETE CASCADE,
    question_id TEXT NOT NULL REFERENCES questions(question_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    sub_stem TEXT NOT NULL,
    normalized_sub_stem TEXT NOT NULL,
    PRIMARY KEY (group_id, question_id)
);

CREATE TABLE question_occurrences (
    occurrence_id INTEGER PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(question_id) ON DELETE CASCADE,
    version_id TEXT NOT NULL REFERENCES question_versions(version_id),
    source_file_id INTEGER NOT NULL REFERENCES source_files(source_file_id),
    sheet_name TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    position INTEGER NOT NULL,
    UNIQUE (source_file_id, sheet_name, row_number)
);

CREATE TABLE collection_questions (
    collection_id TEXT NOT NULL REFERENCES collections(collection_id) ON DELETE CASCADE,
    occurrence_id INTEGER NOT NULL REFERENCES question_occurrences(occurrence_id) ON DELETE CASCADE,
    question_id TEXT NOT NULL REFERENCES questions(question_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY (collection_id, occurrence_id)
);

CREATE TABLE data_conflicts (
    conflict_id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(question_id) ON DELETE CASCADE,
    canonical_version_id TEXT NOT NULL REFERENCES question_versions(version_id),
    observed_version_id TEXT NOT NULL REFERENCES question_versions(version_id),
    conflict_type TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE INDEX idx_source_files_path ON source_files(relative_path);
CREATE INDEX idx_questions_type ON questions(canonical_type);
CREATE INDEX idx_questions_stem ON questions(normalized_stem);
CREATE INDEX idx_questions_source_code ON questions(source_code);
CREATE INDEX idx_versions_question ON question_versions(question_id);
CREATE INDEX idx_group_members_question ON question_group_members(question_id);
CREATE INDEX idx_group_members_position ON question_group_members(group_id, position);
CREATE INDEX idx_occurrences_question ON question_occurrences(question_id);
CREATE INDEX idx_occurrences_source ON question_occurrences(source_file_id, position);
CREATE INDEX idx_collection_questions_question ON collection_questions(question_id);
CREATE INDEX idx_collections_relative_path ON collections(relative_path);

CREATE VIRTUAL TABLE question_fts USING fts5(
    question_id UNINDEXED,
    stem,
    options,
    point,
    explanation,
    tokenize='trigram'
);
"""


def connect_build_database(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = OFF")
    connection.execute("PRAGMA temp_store = MEMORY")
    return connection


def create_schema(connection: sqlite3.Connection, schema_revision: int) -> None:
    connection.executescript(SCHEMA_SQL)
    connection.execute(
        "INSERT INTO schema_meta(key, value) VALUES ('schema_revision', ?)",
        (str(schema_revision),),
    )


def rebuild_fts(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM question_fts")
    connection.execute(
        """
        INSERT INTO question_fts(question_id, stem, options, point, explanation)
        SELECT
            q.question_id,
            q.stem,
            COALESCE((
                SELECT group_concat(o.label || '. ' || o.text, char(10))
                FROM question_options AS o
                WHERE o.question_id = q.question_id
                ORDER BY o.position
            ), ''),
            q.point,
            q.explanation
        FROM questions AS q
        ORDER BY q.question_id
        """
    )


def validate_database(connection: sqlite3.Connection) -> dict:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_key_errors = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "source_files",
            "collections",
            "questions",
            "question_options",
            "question_versions",
            "question_groups",
            "question_group_members",
            "question_occurrences",
            "collection_questions",
            "data_conflicts",
            "question_fts",
        )
    }
    return {
        "integrity_check": integrity,
        "foreign_key_errors": foreign_key_errors,
        "counts": counts,
    }

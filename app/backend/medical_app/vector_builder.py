from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .providers import EmbeddingClient, ProviderError, validate_provider_url


VECTOR_TEXT_REVISION = 1


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _stable_id(prefix: str, payload: object, length: int = 32) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:length]}"


def _write_json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _catalog_items(database_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    import sqlite3

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        run = connection.execute(
            """
            SELECT snapshot_id, schema_revision FROM import_runs
            WHERE status = 'complete' ORDER BY completed_at DESC LIMIT 1
            """
        ).fetchone()
        if run is None:
            raise ValueError("Catalog does not contain a completed import run")
        questions = [
            dict(row)
            for row in connection.execute(
                """
                SELECT question_id, canonical_type, stem
                FROM questions ORDER BY question_id
                """
            )
        ]
        options: dict[str, list[str]] = {}
        for row in connection.execute(
            """
            SELECT question_id, label, text FROM question_options
            ORDER BY question_id, position
            """
        ):
            options.setdefault(row["question_id"], []).append(f"{row['label']}. {row['text']}")
    finally:
        connection.close()
    for question in questions:
        option_lines = options.get(question["question_id"], [])
        parts = [f"题型：{question['canonical_type']}", f"题干：{question['stem']}"]
        if option_lines:
            parts.append("选项：\n" + "\n".join(option_lines))
        question["embedding_text"] = "\n".join(parts)
    return {
        "snapshot_id": run["snapshot_id"],
        "catalog_schema_revision": run["schema_revision"],
        "catalog_build_id": database_path.parent.name,
    }, questions


def build_vector_index(
    catalog_database: str | Path,
    knowledge_base_root: str | Path,
    *,
    api_url: str,
    api_key: str,
    model: str,
    dimensions: int = 1024,
    batch_size: int = 32,
    timeout: int = 60,
    max_retries: int = 5,
    force: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    catalog_database = Path(catalog_database).resolve()
    root = Path(knowledge_base_root).resolve()
    if not catalog_database.is_file():
        raise FileNotFoundError(f"Catalog database does not exist: {catalog_database}")
    if not api_key:
        raise ValueError("Embedding API key is required")
    validate_provider_url(api_url)
    dimensions = int(dimensions)
    if dimensions <= 0:
        raise ValueError("dimensions must be positive")
    batch_size = max(1, min(int(batch_size), 256))
    metadata, items = _catalog_items(catalog_database)
    question_ids = [item["question_id"] for item in items]
    ids_hash = hashlib.sha256("\n".join(question_ids).encode("utf-8")).hexdigest()
    vector_id = _stable_id(
        "vec",
        {
            **metadata,
            "api_url": api_url,
            "model": model,
            "dimensions": dimensions,
            "text_revision": VECTOR_TEXT_REVISION,
            "question_ids_sha256": ids_hash,
        },
        length=32,
    )
    build_dir = root / "vector-indexes" / vector_id
    build_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = build_dir / "manifest.json"
    matrix_path = build_dir / "embeddings.npy"
    ids_path = build_dir / "question_ids.json"
    state_path = build_dir / "build-state.json"

    if manifest_path.is_file() and matrix_path.is_file() and ids_path.is_file() and not force:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("build_id") != vector_id:
            raise ValueError("Existing vector manifest build ID does not match its directory")
        validate_vector_build(build_dir)
        manifest["reused"] = True
        return manifest
    if force:
        for path in (manifest_path, matrix_path, ids_path, state_path):
            if path.exists():
                path.unlink()

    expected_state = {
        "build_id": vector_id,
        **metadata,
        "api_url": api_url,
        "model": model,
        "dimensions": dimensions,
        "count": len(items),
        "question_ids_sha256": ids_hash,
        "text_revision": VECTOR_TEXT_REVISION,
    }
    if state_path.is_file() and matrix_path.is_file() and ids_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        for key, value in expected_state.items():
            if state.get(key) != value:
                raise ValueError(f"Partial vector build does not match current input: {key}")
        stored_ids = json.loads(ids_path.read_text(encoding="utf-8"))
        if stored_ids != question_ids:
            raise ValueError("Partial vector build question IDs do not match catalog")
        matrix = np.load(matrix_path, mmap_mode="r+")
        next_index = int(state.get("next_index", 0))
        if matrix.shape != (len(items), dimensions) or matrix.dtype != np.float32:
            raise ValueError("Partial vector matrix does not match current input")
        if next_index < 0 or next_index > len(items):
            raise ValueError("Partial vector build progress is invalid")
    else:
        _write_json_atomic(ids_path, question_ids)
        matrix = np.lib.format.open_memmap(
            matrix_path,
            mode="w+",
            dtype=np.float32,
            shape=(len(items), dimensions),
        )
        next_index = 0
        state = {**expected_state, "next_index": 0, "started_at": _utc_now()}
        _write_json_atomic(state_path, state)

    client = EmbeddingClient(
        api_url=api_url,
        api_key=api_key,
        model=model,
        dimensions=dimensions,
        timeout=timeout,
    )
    started = time.monotonic()
    for start in range(next_index, len(items), batch_size):
        end = min(start + batch_size, len(items))
        texts = [item["embedding_text"] for item in items[start:end]]
        last_error: Exception | None = None
        for attempt in range(max(1, int(max_retries))):
            try:
                vectors = client.embed(texts)
                last_error = None
                break
            except ProviderError as error:
                last_error = error
                if attempt + 1 >= max_retries:
                    break
                time.sleep(min(30, 2**attempt))
        if last_error is not None:
            raise last_error
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if np.any(norms <= 0) or not np.isfinite(norms).all():
            raise ValueError("Embedding batch contains invalid vector norms")
        matrix[start:end] = vectors / norms
        matrix.flush()
        state = {
            **expected_state,
            "next_index": end,
            "started_at": state.get("started_at", _utc_now()),
            "updated_at": _utc_now(),
        }
        _write_json_atomic(state_path, state)
        if progress:
            progress(
                {
                    "build_id": vector_id,
                    "completed": end,
                    "total": len(items),
                    "elapsed_seconds": round(time.monotonic() - started, 1),
                }
            )

    validation = validate_vector_build(build_dir)
    manifest = {
        "build_id": vector_id,
        **metadata,
        "created_at": _utc_now(),
        "model": model,
        "api_url": api_url,
        "dimensions": dimensions,
        "dtype": "float32",
        "normalized": True,
        "count": len(items),
        "text_revision": VECTOR_TEXT_REVISION,
        "question_ids_sha256": ids_hash,
        "matrix": "embeddings.npy",
        "question_ids": "question_ids.json",
        "validation": validation,
        "reused": False,
    }
    _write_json_atomic(manifest_path, manifest)
    return manifest


def validate_vector_build(build_dir: str | Path) -> dict[str, Any]:
    build_dir = Path(build_dir).resolve()
    ids = json.loads((build_dir / "question_ids.json").read_text(encoding="utf-8"))
    matrix = np.load(build_dir / "embeddings.npy", mmap_mode="r")
    if matrix.ndim != 2 or matrix.shape[0] != len(ids):
        raise ValueError("Vector matrix and question ID count do not match")
    minimum_norm = float("inf")
    maximum_norm = 0.0
    for start in range(0, matrix.shape[0], 4096):
        chunk = np.asarray(matrix[start : start + 4096], dtype=np.float32)
        if not np.isfinite(chunk).all():
            raise ValueError("Vector matrix contains non-finite values")
        norms = np.linalg.norm(chunk, axis=1)
        minimum_norm = min(minimum_norm, float(norms.min()))
        maximum_norm = max(maximum_norm, float(norms.max()))
    if matrix.shape[0] and (minimum_norm < 0.999 or maximum_norm > 1.001):
        raise ValueError("Vector matrix rows are not normalized")
    return {
        "shape": [int(matrix.shape[0]), int(matrix.shape[1])],
        "minimum_norm": round(minimum_norm, 6) if matrix.shape[0] else None,
        "maximum_norm": round(maximum_norm, 6) if matrix.shape[0] else None,
        "file_size_bytes": (build_dir / "embeddings.npy").stat().st_size,
    }


def activate_vector_index(knowledge_base_root: str | Path, build_id: str) -> dict[str, Any]:
    root = Path(knowledge_base_root).resolve()
    if Path(build_id).name != build_id or not build_id.startswith("vec_"):
        raise ValueError("Invalid vector build ID")
    build_dir = root / "vector-indexes" / build_id
    manifest_path = build_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Vector manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("build_id") != build_id:
        raise ValueError("Vector manifest build ID mismatch")
    validate_vector_build(build_dir)
    pointer = {
        "build_id": build_id,
        "catalog_build_id": manifest["catalog_build_id"],
        "model": manifest["model"],
        "dimensions": manifest["dimensions"],
        "activated_at": _utc_now(),
    }
    root.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(root / "vector-current.json", pointer)
    return pointer

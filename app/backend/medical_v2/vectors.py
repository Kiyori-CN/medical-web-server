from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path
from typing import Any

import numpy as np


_VECTOR_ID_RE = re.compile(r"vec_[a-f0-9]{24,64}")


class VectorIndexUnavailable(RuntimeError):
    pass


class VectorIndexManager:
    def __init__(self, knowledge_base_root: str | Path):
        self.root = Path(knowledge_base_root).resolve()
        self._lock = threading.Lock()
        self._loaded_build_id = ""
        self._manifest: dict[str, Any] | None = None
        self._question_ids: list[str] = []
        self._matrix: np.ndarray | None = None

    def status(self) -> dict[str, Any]:
        try:
            manifest = self._read_current_manifest()
            matrix_path = self.root / "vector-indexes" / manifest["build_id"] / "embeddings.npy"
            ids_path = self.root / "vector-indexes" / manifest["build_id"] / "question_ids.json"
            if not matrix_path.is_file() or not ids_path.is_file():
                raise VectorIndexUnavailable("Vector index files are incomplete")
            return {
                "status": "ready",
                "build_id": manifest["build_id"],
                "catalog_build_id": manifest["catalog_build_id"],
                "api_url": manifest.get("api_url", ""),
                "model": manifest["model"],
                "dimensions": manifest["dimensions"],
                "count": manifest["count"],
                "dtype": manifest["dtype"],
            }
        except (FileNotFoundError, KeyError, ValueError, VectorIndexUnavailable) as error:
            return {"status": "unavailable", "message": str(error)}

    def search(self, query_vector: np.ndarray, *, top_k: int = 200) -> list[tuple[str, float]]:
        self._ensure_loaded()
        if self._matrix is None or self._manifest is None:
            raise VectorIndexUnavailable("Vector index is not loaded")
        vector = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        expected_dimension = int(self._manifest["dimensions"])
        if vector.shape[0] != expected_dimension:
            raise VectorIndexUnavailable(
                f"Query vector dimension {vector.shape[0]} does not match index {expected_dimension}"
            )
        norm = float(np.linalg.norm(vector))
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError("Query embedding has an invalid norm")
        vector = vector / norm
        scores = np.asarray(self._matrix @ vector, dtype=np.float32)
        count = max(1, min(int(top_k), scores.shape[0]))
        if count == scores.shape[0]:
            indexes = np.argsort(scores)[::-1]
        else:
            partition = np.argpartition(scores, -count)[-count:]
            indexes = partition[np.argsort(scores[partition])[::-1]]
        return [
            (self._question_ids[int(index)], float(scores[int(index)])) for index in indexes[:count]
        ]

    def manifest(self) -> dict[str, Any]:
        self._ensure_loaded()
        if self._manifest is None:
            raise VectorIndexUnavailable("Vector index is not loaded")
        return dict(self._manifest)

    def _ensure_loaded(self) -> None:
        manifest = self._read_current_manifest()
        build_id = manifest["build_id"]
        if self._loaded_build_id == build_id and self._matrix is not None:
            return
        with self._lock:
            if self._loaded_build_id == build_id and self._matrix is not None:
                return
            build_dir = self.root / "vector-indexes" / build_id
            ids = json.loads((build_dir / "question_ids.json").read_text(encoding="utf-8"))
            matrix = np.load(build_dir / "embeddings.npy", mmap_mode="r")
            if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
                raise VectorIndexUnavailable("Vector question ID file is invalid")
            if matrix.ndim != 2 or matrix.shape != (
                int(manifest["count"]),
                int(manifest["dimensions"]),
            ):
                raise VectorIndexUnavailable("Vector matrix shape does not match manifest")
            if len(ids) != matrix.shape[0]:
                raise VectorIndexUnavailable("Vector question IDs do not match matrix rows")
            self._loaded_build_id = build_id
            self._manifest = manifest
            self._question_ids = ids
            self._matrix = matrix

    def _read_current_manifest(self) -> dict[str, Any]:
        pointer_path = self.root / "vector-current.json"
        if not pointer_path.is_file():
            raise VectorIndexUnavailable("Vector index has not been activated")
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        build_id = str(pointer.get("build_id", ""))
        if not _VECTOR_ID_RE.fullmatch(build_id):
            raise VectorIndexUnavailable("Vector pointer contains an invalid build ID")
        manifest_path = self.root / "vector-indexes" / build_id / "manifest.json"
        if not manifest_path.is_file():
            raise VectorIndexUnavailable("Vector manifest is missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("build_id") != build_id:
            raise VectorIndexUnavailable("Vector manifest does not match pointer")
        for field in ("catalog_build_id", "model", "dimensions"):
            if field in pointer and pointer[field] != manifest.get(field):
                raise VectorIndexUnavailable(
                    f"Vector pointer {field} does not match manifest"
                )
        return manifest

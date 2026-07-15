from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from medical_app.builder import build_catalog
from medical_app.catalog import Catalog
from medical_app.providers import EmbeddingClient
from medical_app.semantic import SemanticSearchService, SemanticSearchUnavailable
from medical_app.settings import SettingsStore
from medical_app.vector_builder import activate_vector_index, build_vector_index
from medical_app.vectors import VectorIndexManager


def _fake_embed(self, texts: list[str]) -> np.ndarray:
    dimensions = int(self.dimensions or 64)
    rows = []
    for text in texts:
        vector = np.zeros(dimensions, dtype=np.float32)
        for character in text:
            vector[ord(character) % dimensions] += 1.0
        if not vector.any():
            vector[0] = 1.0
        rows.append(vector)
    return np.asarray(rows, dtype=np.float32)


def test_vector_build_resume_contract_and_semantic_search(
    sample_source_tree: Path, tmp_path: Path, monkeypatch
):
    manifest = build_catalog(sample_source_tree, tmp_path / "knowledge" / "builds")
    catalog_path = tmp_path / "knowledge" / "builds" / manifest["build_id"] / "catalog.sqlite3"
    knowledge_root = tmp_path / "knowledge"
    monkeypatch.setattr(EmbeddingClient, "embed", _fake_embed)

    built = build_vector_index(
        catalog_path,
        knowledge_root,
        api_url="https://example.invalid/v1/embeddings",
        api_key="sk-vector-test-secret",
        model="Qwen/Qwen3-Embedding-0.6B",
        dimensions=64,
        batch_size=2,
    )
    pointer = activate_vector_index(knowledge_root, built["build_id"])
    reused = build_vector_index(
        catalog_path,
        knowledge_root,
        api_url="https://example.invalid/v1/embeddings",
        api_key="sk-vector-test-secret",
        model="Qwen/Qwen3-Embedding-0.6B",
        dimensions=64,
    )
    assert pointer["build_id"] == built["build_id"]
    assert reused["reused"] is True
    assert built["count"] == 5
    assert built["validation"]["shape"] == [5, 64]
    assert len(built["validation"]["matrix_sha256"]) == 64
    assert len(built["validation"]["question_ids_file_sha256"]) == 64
    assert "sk-vector-test-secret" not in json.dumps(built)

    settings = SettingsStore(tmp_path / "state" / "settings.sqlite3")
    settings.initialize()
    settings.update(
        {
            "app": {"semantic_enabled": True},
            "providers": {
                "embedding": {
                    "api_url": "https://example.invalid/v1/embeddings",
                    "model": "Qwen/Qwen3-Embedding-0.6B",
                    "dimensions": 64,
                    "api_key": "sk-vector-test-secret",
                }
            },
        }
    )
    service = SemanticSearchService(Catalog(catalog_path), settings, knowledge_root)
    results, metadata = service.search(
        "细胞膜运输",
        mode="semantic",
        limit=3,
        include_solution=False,
        filters={},
    )
    assert results
    assert all(result["match_kind"] == "semantic" for result in results)
    assert all("answer" not in result for result in results)
    assert metadata["vector_index"]["build_id"] == built["build_id"]
    assert service.status()["status"] == "ready"
    assert VectorIndexManager(knowledge_root).status()["status"] == "ready"

    hybrid_results, hybrid_metadata = service.search(
        "细胞膜运输",
        mode="hybrid",
        limit=3,
        include_solution=False,
        filters={},
    )
    assert hybrid_results
    assert hybrid_metadata["mode"] == "hybrid"
    assert hybrid_metadata["vector_index"]["build_id"] == built["build_id"]
    assert all("answer" not in result for result in hybrid_results)

    # API-provided JSON numbers can arrive as strings; the service must clamp
    # them before sizing candidate sets.
    string_limit_results, _ = service.search(
        "细胞膜运输",
        mode="semantic",
        limit="3",
        include_solution=False,
        filters={},
    )
    assert len(string_limit_results) <= 3

    settings.update(
        {
            "providers": {
                "embedding": {"api_url": "https://other.invalid/v1/embeddings"}
            }
        }
    )
    assert service.status()["status"] == "incompatible"
    with pytest.raises(SemanticSearchUnavailable, match="API URL"):
        service.search(
            "细胞膜运输",
            mode="semantic",
            limit=3,
            include_solution=False,
            filters={},
        )


def test_incremental_vector_build_reuses_unchanged_question_rows(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.sqlite3"
    catalog.write_bytes(b"placeholder")
    knowledge = tmp_path / "knowledge"
    dataset = {
        "metadata": {
            "snapshot_id": "snapshot-a",
            "catalog_schema_revision": 1,
            "catalog_build_id": "catalog-a",
        },
        "items": [
            {"question_id": "q1", "embedding_text": "题目一"},
            {"question_id": "q2", "embedding_text": "题目二"},
        ],
    }
    calls: list[list[str]] = []

    def catalog_items(_):
        return dataset["metadata"], dataset["items"]

    def embed(self, texts):
        calls.append(list(texts))
        return _fake_embed(self, texts)

    monkeypatch.setattr("medical_app.vector_builder._catalog_items", catalog_items)
    monkeypatch.setattr(EmbeddingClient, "embed", embed)
    first = build_vector_index(
        catalog,
        knowledge,
        api_url="https://example.invalid/v1/embeddings",
        api_key="sk-vector-test-secret",
        model="Qwen/Qwen3-Embedding-0.6B",
        dimensions=64,
    )
    assert first["incremental"]["reused_count"] == 0

    dataset["metadata"] = {
        "snapshot_id": "snapshot-b",
        "catalog_schema_revision": 1,
        "catalog_build_id": "catalog-b",
    }
    dataset["items"] = [
        {"question_id": "q1", "embedding_text": "题目一"},
        {"question_id": "q2", "embedding_text": "题目二"},
        {"question_id": "q3", "embedding_text": "新增题目"},
    ]
    calls.clear()
    second = build_vector_index(
        catalog,
        knowledge,
        api_url="https://example.invalid/v1/embeddings",
        api_key="sk-vector-test-secret",
        model="Qwen/Qwen3-Embedding-0.6B",
        dimensions=64,
    )
    assert second["incremental"]["source_build_id"] == first["build_id"]
    assert second["incremental"]["reused_count"] == 2
    assert second["incremental"]["embedded_count"] == 1
    assert calls == [["新增题目"]]

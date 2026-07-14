from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np

from .catalog import Catalog
from .normalize import display_text
from .providers import EmbeddingClient, ProviderError
from .settings import SettingsStore
from .vectors import VectorIndexManager, VectorIndexUnavailable


class SemanticSearchUnavailable(RuntimeError):
    pass


class SemanticSearchService:
    def __init__(
        self,
        catalog: Catalog,
        settings: SettingsStore,
        knowledge_base_root: str | Path,
        *,
        provider_timeout: int = 60,
    ):
        self.catalog = catalog
        self.settings = settings
        self.vectors = VectorIndexManager(knowledge_base_root)
        self.provider_timeout = provider_timeout
        self._cache: OrderedDict[tuple[str, str, int, str], np.ndarray] = OrderedDict()
        self._cache_lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        status = self.vectors.status()
        public = self.settings.public_settings()
        status["semantic_enabled"] = public["semantic_enabled"]
        status["provider_configured"] = public["embedding"]["api_key_configured"]
        if status.get("status") != "ready":
            return status
        catalog_identity = self.catalog.identity()
        compatibility_error = _compatibility_error(
            status,
            public["embedding"],
            catalog_identity,
        )
        if compatibility_error:
            return {**status, "status": "incompatible", "message": compatibility_error}
        return status

    def search(
        self,
        query: str,
        *,
        mode: str,
        limit: int,
        include_solution: bool,
        filters: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        mode = display_text(mode).lower() or "keyword"
        limit = max(1, min(int(limit), 50))
        if mode == "keyword":
            results = self.catalog.search(
                query,
                limit=limit,
                include_solution=include_solution,
                **_catalog_filters(filters),
            )
            return results, {"mode": "keyword"}
        if mode not in {"semantic", "hybrid"}:
            raise ValueError("mode must be keyword, semantic, or hybrid")

        semantic_candidates, vector_meta = self._semantic_candidates(
            query, top_k=max(200, limit * 20)
        )
        if mode == "semantic":
            candidates = [
                (question_id, score, "semantic")
                for question_id, score in semantic_candidates
            ]
        else:
            keyword_results = self.catalog.search(
                query,
                limit=max(40, limit * 4),
                include_solution=False,
                **_catalog_filters(filters),
            )
            candidates = _fuse_hybrid(keyword_results, semantic_candidates)
        results = self.catalog.get_ranked_questions(
            candidates,
            include_solution=include_solution,
            limit=limit,
            **_catalog_filters(filters),
        )
        return results, {"mode": mode, "vector_index": vector_meta}

    def _semantic_candidates(self, query: str, *, top_k: int) -> tuple[list, dict]:
        public = self.settings.public_settings()
        if not public["semantic_enabled"]:
            raise SemanticSearchUnavailable("Semantic search is disabled in settings")
        config = self.settings.provider_config("embedding", include_secret=True)
        if not config["api_key"]:
            raise SemanticSearchUnavailable("Embedding API key is not configured")
        try:
            manifest = self.vectors.manifest()
        except VectorIndexUnavailable as error:
            raise SemanticSearchUnavailable(str(error)) from error
        compatibility_error = _compatibility_error(
            manifest,
            config,
            self.catalog.identity(),
        )
        if compatibility_error:
            raise SemanticSearchUnavailable(compatibility_error)
        vector = self._query_embedding(query, config)
        try:
            candidates = self.vectors.search(vector, top_k=top_k)
        except VectorIndexUnavailable as error:
            raise SemanticSearchUnavailable(str(error)) from error
        return candidates, {
            "build_id": manifest["build_id"],
            "model": manifest["model"],
            "dimensions": manifest["dimensions"],
        }

    def _query_embedding(self, query: str, config: dict[str, Any]) -> np.ndarray:
        cache_key = (
            config["api_url"],
            config["model"],
            int(config["dimensions"]),
            display_text(query),
        )
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                return cached.copy()
        try:
            vector = EmbeddingClient(
                api_url=config["api_url"],
                api_key=config["api_key"],
                model=config["model"],
                dimensions=config["dimensions"],
                timeout=self.provider_timeout,
            ).embed([display_text(query)])[0]
        except ProviderError as error:
            raise SemanticSearchUnavailable(str(error)) from error
        with self._cache_lock:
            self._cache[cache_key] = vector.copy()
            self._cache.move_to_end(cache_key)
            while len(self._cache) > 256:
                self._cache.popitem(last=False)
        return vector


def _catalog_filters(filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "canonical_type": filters.get("question_type", ""),
        "exam": filters.get("exam", ""),
        "collection_kind": filters.get("collection_kind", ""),
        "subject": filters.get("subject", ""),
        "year": filters.get("year"),
    }


def _compatibility_error(
    vector_metadata: dict[str, Any],
    embedding_config: dict[str, Any],
    catalog_identity: dict[str, Any],
) -> str:
    if vector_metadata.get("catalog_build_id") != catalog_identity["catalog_build_id"]:
        return "Active vector index does not match the active catalog build"
    if vector_metadata.get("model") != embedding_config.get("model"):
        return "Configured embedding model does not match the active vector index"
    if int(vector_metadata.get("dimensions") or 0) != int(
        embedding_config.get("dimensions") or 0
    ):
        return "Configured embedding dimensions do not match the active vector index"
    manifest_url = vector_metadata.get("api_url")
    if manifest_url and manifest_url != embedding_config.get("api_url"):
        return "Configured embedding API URL does not match the active vector index"
    return ""


def _fuse_hybrid(
    keyword_results: list[dict[str, Any]],
    semantic_candidates: list[tuple[str, float]],
) -> list[tuple[str, float, str]]:
    scores: dict[str, float] = {}
    match_kinds: dict[str, str] = {}
    for rank, item in enumerate(keyword_results, start=1):
        question_id = item["question_id"]
        scores[question_id] = scores.get(question_id, 0.0) + 1.0 / (60 + rank)
        match_kind = item.get("match_kind", "fts")
        match_kinds[question_id] = match_kind if match_kind in {"exact", "source_code"} else "hybrid"
        if match_kind in {"exact", "source_code"}:
            scores[question_id] += 1.0
    for rank, (question_id, _) in enumerate(semantic_candidates, start=1):
        scores[question_id] = scores.get(question_id, 0.0) + 1.0 / (60 + rank)
        match_kinds.setdefault(question_id, "hybrid")
    ordered = sorted(scores, key=lambda question_id: (-scores[question_id], question_id))
    return [
        (question_id, scores[question_id], match_kinds[question_id]) for question_id in ordered
    ]

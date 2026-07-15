from __future__ import annotations

from typing import Any
from urllib.parse import urlparse, urlunparse

import numpy as np
import requests


class ProviderError(RuntimeError):
    pass


_EMBEDDING_MODEL_MARKERS = (
    "embedding",
    "embed-",
    "bge-",
    "/bge",
    "gte-",
    "/gte",
    "e5-",
    "/e5",
    "text2vec",
    "m3e-",
)


def validate_provider_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("api_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "api_url must not contain credentials, query parameters, or a fragment"
        )


def provider_models_url(api_url: str) -> str:
    validate_provider_url(api_url)
    parsed = urlparse(api_url)
    parts = [part for part in parsed.path.split("/") if part]
    if "v1" in parts:
        parts = parts[: len(parts) - parts[::-1].index("v1")]
    elif parts[-2:] == ["chat", "completions"]:
        parts = parts[:-2]
    elif parts and parts[-1] in {"embeddings", "completions", "responses"}:
        parts = parts[:-1]
    parts.append("models")
    return urlunparse(parsed._replace(path="/" + "/".join(parts)))


def list_provider_models(
    *, api_url: str, api_key: str, provider: str, timeout: int = 30
) -> list[dict[str, str]]:
    provider = str(provider or "").strip().lower()
    if provider not in {"embedding", "llm"}:
        raise ValueError("provider must be embedding or llm")
    if not isinstance(api_key, str) or len(api_key.strip()) < 8:
        raise ProviderError("API key is not configured")
    try:
        response = requests.get(
            provider_models_url(api_url),
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            timeout=max(5, min(int(timeout), 120)),
        )
    except requests.RequestException as error:
        raise ProviderError(
            f"Provider model discovery failed: {type(error).__name__}"
        ) from error
    if response.status_code >= 400:
        raise ProviderError(f"Provider model discovery returned HTTP {response.status_code}")
    try:
        payload = response.json()
        rows = payload.get("data", payload.get("models", []))
    except (TypeError, ValueError) as error:
        raise ProviderError("Provider returned an invalid model list") from error
    if not isinstance(rows, list):
        raise ProviderError("Provider returned an invalid model list")
    models: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, str):
            model_id = row.strip()
            metadata = ""
            owner = ""
        elif isinstance(row, dict):
            model_id = str(row.get("id") or row.get("name") or "").strip()
            metadata = " ".join(
                str(row.get(key) or "")
                for key in ("type", "task", "category", "capabilities")
            ).lower()
            owner = str(row.get("owned_by") or row.get("provider") or "").strip()
        else:
            continue
        if not model_id or len(model_id) > 200:
            continue
        searchable = f"{model_id} {metadata}".lower()
        is_embedding = any(marker in searchable for marker in _EMBEDDING_MODEL_MARKERS)
        is_reranker = "rerank" in searchable
        if provider == "embedding" and (not is_embedding or is_reranker):
            continue
        if provider == "llm" and (is_embedding or is_reranker):
            continue
        models.append({"id": model_id, "owned_by": owner})
    deduplicated = {item["id"]: item for item in models}
    return sorted(
        deduplicated.values(), key=lambda item: (item["id"].casefold(), item["id"])
    )[:500]


class EmbeddingClient:
    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        model: str,
        dimensions: int | None = None,
        timeout: int = 60,
    ):
        validate_provider_url(api_url)
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self.timeout = max(5, min(int(timeout), 300))

    def embed(self, texts: list[str]) -> np.ndarray:
        if not self.api_key:
            raise ProviderError("Embedding API key is not configured")
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding input must contain non-empty strings")
        payload: dict[str, Any] = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
        }
        if self.dimensions:
            payload["dimensions"] = int(self.dimensions)
        try:
            response = requests.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise ProviderError(f"Embedding provider request failed: {type(error).__name__}") from error
        if response.status_code >= 400:
            raise ProviderError(f"Embedding provider returned HTTP {response.status_code}")
        try:
            body = response.json()
            rows = sorted(body["data"], key=lambda item: int(item.get("index", 0)))
            vectors = np.asarray([row["embedding"] for row in rows], dtype=np.float32)
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderError("Embedding provider returned an invalid response") from error
        if vectors.ndim != 2 or vectors.shape[0] != len(texts):
            raise ProviderError("Embedding provider returned an unexpected vector count")
        if self.dimensions and vectors.shape[1] != self.dimensions:
            raise ProviderError("Embedding provider returned an unexpected vector dimension")
        if not np.isfinite(vectors).all():
            raise ProviderError("Embedding provider returned non-finite values")
        return vectors


def test_chat_provider(config: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    api_key = config.get("api_key", "")
    if not api_key:
        raise ProviderError("Large-model API key is not configured")
    try:
        response = requests.post(
            config["api_url"],
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": config["model"],
                "messages": [{"role": "user", "content": "仅回复 OK"}],
                "max_tokens": 8,
                "stream": False,
            },
            timeout=max(5, min(int(timeout), 120)),
        )
    except requests.RequestException as error:
        raise ProviderError(f"Large-model provider request failed: {type(error).__name__}") from error
    if response.status_code >= 400:
        raise ProviderError(f"Large-model provider returned HTTP {response.status_code}")
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ProviderError("Large-model provider returned an invalid response") from error
    return {"status": "ok", "model": config["model"], "response_received": bool(content)}

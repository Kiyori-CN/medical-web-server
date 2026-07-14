from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import numpy as np
import requests


class ProviderError(RuntimeError):
    pass


def validate_provider_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("api_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "api_url must not contain credentials, query parameters, or a fragment"
        )


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

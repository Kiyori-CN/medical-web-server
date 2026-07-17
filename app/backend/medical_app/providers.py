from __future__ import annotations

import json
from typing import Any, Iterator
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


def normalize_provider_base_url(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    validate_provider_url(value)
    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    if parts[-2:] == ["chat", "completions"]:
        parts = parts[:-2]
    elif parts and parts[-1] in {"embeddings", "models", "completions", "responses"}:
        parts = parts[:-1]
    if not parts:
        parts = ["v1"]
    return urlunparse(parsed._replace(path="/" + "/".join(parts)))


def provider_api_url(base_url: str, provider: str) -> str:
    provider_value = str(provider or "").strip().lower()
    if provider_value not in {"embedding", "llm"}:
        raise ValueError("provider must be embedding or llm")
    base = normalize_provider_base_url(base_url)
    suffix = "embeddings" if provider_value == "embedding" else "chat/completions"
    return f"{base}/{suffix}"


def provider_models_url(base_url: str) -> str:
    return f"{normalize_provider_base_url(base_url)}/models"


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


def complete_chat_provider(
    config: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    timeout: int = 90,
    max_tokens: int = 1200,
) -> str:
    """Run one bounded, non-streaming chat completion.

    The caller supplies a decrypted configuration from ``SettingsStore``.  This
    helper deliberately returns only model content and replaces provider errors
    with safe, non-secret diagnostics so credentials never reach the API layer.
    """
    api_url, headers, payload = _chat_request(
        config, messages, max_tokens=max_tokens, stream=False
    )
    try:
        response = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=max(10, min(int(timeout), 180)),
        )
    except requests.RequestException as error:
        raise ProviderError(f"Large-model provider request failed: {type(error).__name__}") from error
    if response.status_code >= 400:
        raise ProviderError(f"Large-model provider returned HTTP {response.status_code}")
    try:
        body = response.json()
        content = _chat_content(body["choices"][0]["message"]["content"], strip=True)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ProviderError("Large-model provider returned an invalid response") from error
    if not content:
        raise ProviderError("Large-model provider returned an empty response")
    return content[:30000]


def stream_chat_provider(
    config: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    timeout: int = 90,
    max_tokens: int = 1200,
) -> Iterator[str]:
    """Yield text deltas from an OpenAI-compatible chat completion stream.

    Provider bodies and credentials never escape this helper. A provider that
    ignores ``stream=true`` and returns a normal JSON completion is accepted as
    a single delta so compatible gateways continue to work.
    """

    api_url, headers, payload = _chat_request(
        config, messages, max_tokens=max_tokens, stream=True
    )
    try:
        response = requests.post(
            api_url,
            headers=headers,
            json=payload,
            stream=True,
            timeout=(10, max(10, min(int(timeout), 180))),
        )
    except requests.RequestException as error:
        raise ProviderError(
            f"Large-model provider request failed: {type(error).__name__}"
        ) from error

    try:
        if response.status_code >= 400:
            raise ProviderError(
                f"Large-model provider returned HTTP {response.status_code}"
            )
        content_type = str(response.headers.get("content-type") or "").lower()
        if "application/json" in content_type and "text/event-stream" not in content_type:
            try:
                body = response.json()
                content = _chat_content(
                    body["choices"][0]["message"]["content"], strip=True
                )
            except (KeyError, IndexError, TypeError, ValueError) as error:
                raise ProviderError(
                    "Large-model provider returned an invalid response"
                ) from error
            if not content:
                raise ProviderError("Large-model provider returned an empty response")
            if len(content) > 30000:
                raise ProviderError("Large-model provider response exceeded the size limit")
            yield content
            return

        received = False
        total = 0
        for raw_line in response.iter_lines(chunk_size=1):
            if not raw_line:
                continue
            if isinstance(raw_line, bytes):
                line = raw_line.decode("utf-8", errors="replace").strip()
            else:
                line = str(raw_line).strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
                if isinstance(event, dict) and event.get("error"):
                    raise ProviderError("Large-model provider returned a stream error")
                choices = event.get("choices") if isinstance(event, dict) else None
                if choices == []:
                    continue
                if not isinstance(choices, list):
                    raise TypeError("stream choices must be a list")
                choice = choices[0]
                if not isinstance(choice, dict):
                    raise TypeError("stream choice must be an object")
                delta = choice.get("delta")
                if delta is None and isinstance(choice.get("message"), dict):
                    delta = choice["message"]
                if delta is None:
                    delta = {}
                if not isinstance(delta, dict):
                    raise TypeError("stream delta must be an object")
                content = _chat_content(
                    delta.get("content"),
                    strip=False,
                )
            except ProviderError:
                raise
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise ProviderError(
                    "Large-model provider returned an invalid stream"
                ) from error
            if not content:
                continue
            total += len(content)
            if total > 30000:
                raise ProviderError("Large-model provider response exceeded the size limit")
            received = True
            yield content
        if not received:
            raise ProviderError("Large-model provider returned an empty response")
    except requests.RequestException as error:
        raise ProviderError(
            f"Large-model provider stream failed: {type(error).__name__}"
        ) from error
    finally:
        response.close()


def _chat_request(
    config: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    stream: bool,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    api_key = str(config.get("api_key") or "").strip()
    if len(api_key) < 8:
        raise ProviderError("Large-model API key is not configured")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must contain at least one item")
    safe_messages = []
    for item in messages:
        if not isinstance(item, dict):
            raise ValueError("messages must be objects")
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in {"system", "user", "assistant"} or not content:
            raise ValueError("messages contain an invalid role or content")
        safe_messages.append({"role": role, "content": content[:12000]})
    api_url = str(config.get("api_url") or "").strip()
    try:
        validate_provider_url(api_url)
    except ValueError as error:
        raise ProviderError("Large-model API URL is not configured") from error
    payload = {
        "model": str(config.get("model") or ""),
        "messages": safe_messages,
        "max_tokens": max(64, min(int(max_tokens), 4000)),
        "temperature": 0.2,
        "stream": bool(stream),
    }
    return (
        api_url,
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        },
        payload,
    )


def _chat_content(value: object, *, strip: bool) -> str:
    if isinstance(value, list):
        value = "".join(
            str(part.get("text") or "") if isinstance(part, dict) else str(part)
            for part in value
        )
    content = str(value or "")
    return content.strip() if strip else content

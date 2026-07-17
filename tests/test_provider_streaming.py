from __future__ import annotations

import json

import pytest

from medical_app.providers import ProviderError, stream_chat_provider


class FakeStreamResponse:
    def __init__(self, lines, *, content_type="text/event-stream", payload=None):
        self.status_code = 200
        self.headers = {"content-type": content_type}
        self._lines = list(lines)
        self._payload = payload
        self.closed = False

    def iter_lines(self, chunk_size=1):
        assert chunk_size == 1
        yield from self._lines

    def json(self):
        return self._payload

    def close(self):
        self.closed = True


def _config():
    return {
        "api_url": "https://example.invalid/v1/chat/completions",
        "model": "stream-model",
        "api_key": "test-key-123456",
    }


def test_chat_provider_parses_openai_sse_deltas(monkeypatch: pytest.MonkeyPatch):
    response = FakeStreamResponse(
        [
            b'data: {"choices":[{"delta":{"role":"assistant"}}]}',
            ("data: " + json.dumps({"choices": [{"delta": {"content": "第一段"}}]}, ensure_ascii=False)).encode(),
            ("data: " + json.dumps({"choices": [{"delta": {"content": [{"text": "，第二段"}]}}]}, ensure_ascii=False)).encode(),
            b'data: {"choices":[],"usage":{"total_tokens":12}}',
            b"data: [DONE]",
        ]
    )
    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return response

    monkeypatch.setattr("medical_app.providers.requests.post", fake_post)
    chunks = list(
        stream_chat_provider(
            _config(),
            [{"role": "user", "content": "测试流式回答"}],
        )
    )
    assert chunks == ["第一段", "，第二段"]
    assert captured["json"]["stream"] is True
    assert captured["headers"]["Accept"] == "text/event-stream"
    assert captured["stream"] is True
    assert response.closed is True


def test_chat_provider_accepts_non_streaming_json_fallback(monkeypatch: pytest.MonkeyPatch):
    response = FakeStreamResponse(
        [],
        content_type="application/json",
        payload={"choices": [{"message": {"content": "网关回退回答"}}]},
    )
    monkeypatch.setattr(
        "medical_app.providers.requests.post", lambda *args, **kwargs: response
    )
    assert list(
        stream_chat_provider(_config(), [{"role": "user", "content": "测试"}])
    ) == ["网关回退回答"]
    assert response.closed is True


def test_chat_provider_rejects_provider_error_without_exposing_body(
    monkeypatch: pytest.MonkeyPatch,
):
    response = FakeStreamResponse(
        [b'data: {"error":{"message":"secret upstream body"}}']
    )
    monkeypatch.setattr(
        "medical_app.providers.requests.post", lambda *args, **kwargs: response
    )
    with pytest.raises(ProviderError, match="stream error") as error:
        list(stream_chat_provider(_config(), [{"role": "user", "content": "测试"}]))
    assert "secret upstream body" not in str(error.value)
    assert response.closed is True


def test_chat_provider_rejects_non_object_choice(monkeypatch: pytest.MonkeyPatch):
    response = FakeStreamResponse([b'data: {"choices":["invalid secret choice"]}'])
    monkeypatch.setattr(
        "medical_app.providers.requests.post", lambda *args, **kwargs: response
    )

    with pytest.raises(ProviderError, match="invalid stream") as error:
        list(stream_chat_provider(_config(), [{"role": "user", "content": "测试"}]))

    assert "invalid secret choice" not in str(error.value)
    assert response.closed is True

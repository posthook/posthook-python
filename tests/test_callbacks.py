from __future__ import annotations

import json

import httpx
import pytest

import posthook
from posthook import CallbackError, CallbackResult
from posthook._callbacks import ack, async_ack, async_nack, nack


# ─── Helpers ────────────────────────────────────────────────────────


def _mock_response(
    status_code: int,
    body: dict | None = None,
    text: str = "",
) -> httpx.Response:
    if body is not None:
        return httpx.Response(status_code, json=body)
    return httpx.Response(status_code, text=text)


# ─── Sync ack() ─────────────────────────────────────────────────────


class TestAckSync:
    def test_success_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **kw: _mock_response(200, {"data": {"status": "completed"}}),
        )
        result = ack("https://api.posthook.io/ack/token123")
        assert result == CallbackResult(applied=True, status="completed")

    def test_success_not_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Server returns 200 but status is not 'completed' (idempotent no-op)."""
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **kw: _mock_response(200, {"data": {"status": "nacked"}}),
        )
        result = ack("https://api.posthook.io/ack/token123")
        assert result == CallbackResult(applied=False, status="nacked")

    def test_404_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **kw: _mock_response(404),
        )
        result = ack("https://api.posthook.io/ack/token123")
        assert result == CallbackResult(applied=False, status="not_found")

    def test_409_conflict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **kw: _mock_response(409),
        )
        result = ack("https://api.posthook.io/ack/token123")
        assert result == CallbackResult(applied=False, status="conflict")

    def test_401_raises_callback_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **kw: _mock_response(401, text="unauthorized"),
        )
        with pytest.raises(CallbackError, match="ack failed: 401"):
            ack("https://api.posthook.io/ack/token123")

    def test_410_raises_callback_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **kw: _mock_response(410, text="gone"),
        )
        with pytest.raises(CallbackError, match="ack failed: 410"):
            ack("https://api.posthook.io/ack/token123")

    def test_500_raises_callback_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **kw: _mock_response(500, text="internal error"),
        )
        with pytest.raises(CallbackError, match="ack failed: 500"):
            ack("https://api.posthook.io/ack/token123")

    def test_json_body_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that a JSON body is serialized and Content-Type is set."""
        captured: dict = {}

        def mock_post(url: str, **kwargs) -> httpx.Response:
            captured["content"] = kwargs.get("content")
            captured["headers"] = kwargs.get("headers")
            return _mock_response(200, {"data": {"status": "completed"}})

        monkeypatch.setattr(httpx, "post", mock_post)
        ack("https://api.posthook.io/ack/token123", body={"done": True})

        assert json.loads(captured["content"]) == {"done": True}
        assert captured["headers"]["Content-Type"] == "application/json"

    def test_no_body_no_content_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without a body, no Content-Type header should be sent."""
        captured: dict = {}

        def mock_post(url: str, **kwargs) -> httpx.Response:
            captured["content"] = kwargs.get("content")
            captured["headers"] = kwargs.get("headers")
            return _mock_response(200, {"data": {"status": "completed"}})

        monkeypatch.setattr(httpx, "post", mock_post)
        ack("https://api.posthook.io/ack/token123")

        assert captured["content"] is None
        assert captured["headers"] == {}


# ─── Sync nack() ────────────────────────────────────────────────────


class TestNackSync:
    def test_success_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **kw: _mock_response(200, {"data": {"status": "nacked"}}),
        )
        result = nack("https://api.posthook.io/nack/token123")
        assert result == CallbackResult(applied=True, status="nacked")

    def test_success_not_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **kw: _mock_response(200, {"data": {"status": "completed"}}),
        )
        result = nack("https://api.posthook.io/nack/token123")
        assert result == CallbackResult(applied=False, status="completed")

    def test_404_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **kw: _mock_response(404),
        )
        result = nack("https://api.posthook.io/nack/token123")
        assert result == CallbackResult(applied=False, status="not_found")

    def test_409_conflict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **kw: _mock_response(409),
        )
        result = nack("https://api.posthook.io/nack/token123")
        assert result == CallbackResult(applied=False, status="conflict")

    def test_410_raises_callback_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **kw: _mock_response(410, text="gone"),
        )
        with pytest.raises(CallbackError, match="nack failed: 410"):
            nack("https://api.posthook.io/nack/token123")


# ─── Async ack/nack ─────────────────────────────────────────────────


class _MockAsyncClient:
    """Replaces httpx.AsyncClient for async tests."""

    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url, **kwargs):
        return self._handler(url, **kwargs)


class TestAsyncAck:
    async def test_success_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda **kw: _MockAsyncClient(
                lambda url, **kw: _mock_response(200, {"data": {"status": "completed"}}),
            ),
        )
        result = await async_ack("https://api.posthook.io/ack/token123")
        assert result == CallbackResult(applied=True, status="completed")

    async def test_410_raises_callback_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda **kw: _MockAsyncClient(
                lambda url, **kw: _mock_response(410, text="gone"),
            ),
        )
        with pytest.raises(CallbackError, match="ack failed: 410"):
            await async_ack("https://api.posthook.io/ack/token123")

    async def test_500_raises_callback_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda **kw: _MockAsyncClient(
                lambda url, **kw: _mock_response(500, text="boom"),
            ),
        )
        with pytest.raises(CallbackError, match="ack failed: 500"):
            await async_ack("https://api.posthook.io/ack/token123")


class TestAsyncNack:
    async def test_success_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda **kw: _MockAsyncClient(
                lambda url, **kw: _mock_response(200, {"data": {"status": "nacked"}}),
            ),
        )
        result = await async_nack("https://api.posthook.io/nack/token123")
        assert result == CallbackResult(applied=True, status="nacked")

    async def test_409_conflict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda **kw: _MockAsyncClient(
                lambda url, **kw: _mock_response(409),
            ),
        )
        result = await async_nack("https://api.posthook.io/nack/token123")
        assert result == CallbackResult(applied=False, status="conflict")

    async def test_410_raises_callback_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda **kw: _MockAsyncClient(
                lambda url, **kw: _mock_response(410, text="gone"),
            ),
        )
        with pytest.raises(CallbackError, match="nack failed: 410"):
            await async_nack("https://api.posthook.io/nack/token123")


# ─── Importability ───────────────────────────────────────────────────


class TestExports:
    def test_callback_result_importable(self) -> None:
        assert hasattr(posthook, "CallbackResult")

    def test_callback_error_importable(self) -> None:
        assert hasattr(posthook, "CallbackError")

    def test_ack_importable(self) -> None:
        assert hasattr(posthook, "ack")
        assert hasattr(posthook, "nack")
        assert hasattr(posthook, "async_ack")
        assert hasattr(posthook, "async_nack")

from __future__ import annotations

import json
from typing import Any

import httpx

import posthook

HOOK_FIXTURE: dict[str, Any] = {
    "id": "hook-uuid-123",
    "path": "/webhooks/test",
    "data": {"userId": "123"},
    "postAt": "2026-03-01T12:00:00Z",
    "status": "pending",
    "postDurationSeconds": 0.0,
    "createdAt": "2026-02-23T10:00:00Z",
    "updatedAt": "2026-02-23T10:00:00Z",
    "attempts": 0,
}

QUOTA_HEADERS = {
    "posthook-hookquota-limit": "10000",
    "posthook-hookquota-usage": "500",
    "posthook-hookquota-remaining": "9500",
    "posthook-hookquota-resets-at": "2026-03-01T00:00:00Z",
}


def make_mock_transport(
    handler: Any,
) -> httpx.MockTransport:
    """Create a mock transport from a handler function."""
    return httpx.MockTransport(handler)


def json_response(
    data: Any,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build an httpx.Response with JSON body."""
    body = json.dumps(data).encode()
    resp_headers = {"content-type": "application/json"}
    if headers:
        resp_headers.update(headers)
    return httpx.Response(
        status_code=status_code,
        content=body,
        headers=resp_headers,
    )


def make_client(
    transport: httpx.MockTransport,
    **kwargs: Any,
) -> posthook.Posthook:
    """Create a Posthook client with a mock transport."""
    http_client = httpx.Client(transport=transport)
    return posthook.Posthook(
        "pk_test_key",
        http_client=http_client,
        **kwargs,
    )


def make_async_client(
    transport: httpx.MockTransport,
    **kwargs: Any,
) -> posthook.AsyncPosthook:
    """Create an AsyncPosthook client with a mock transport."""
    http_client = httpx.AsyncClient(transport=transport)
    return posthook.AsyncPosthook(
        "pk_test_key",
        http_client=http_client,
        **kwargs,
    )

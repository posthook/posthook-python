"""Sync and async helpers for ack/nack callbacks on async hook deliveries."""

from __future__ import annotations

import json
from typing import Any

import httpx

from ._errors import CallbackError
from ._models import CallbackResult


def _parse_callback_response(
    response: httpx.Response,
    action: str,
    expected_status: str,
) -> CallbackResult:
    """Parse an ack/nack HTTP response into a CallbackResult.

    2xx → parse ``{data: {status}}`` from JSON, ``applied = (status == expected)``.
    404 → ``CallbackResult(applied=False, status="not_found")``.
    409 → ``CallbackResult(applied=False, status="conflict")``.
    Other → raise ``CallbackError``.
    """
    if response.is_success:
        try:
            data = response.json()
            status = data.get("data", {}).get("status", "unknown")
        except Exception:
            status = "unknown"
        return CallbackResult(applied=(status == expected_status), status=status)

    if response.status_code == 404:
        return CallbackResult(applied=False, status="not_found")
    if response.status_code == 409:
        return CallbackResult(applied=False, status="conflict")

    text = response.text
    suffix = f": {text}" if text else ""
    raise CallbackError(
        f"{action} failed: {response.status_code}{suffix}",
        status_code=response.status_code,
    )


def _prepare_request(body: Any) -> tuple[bytes | None, dict[str, str]]:
    """Prepare content and headers for a callback request."""
    if body is not None:
        return json.dumps(body).encode(), {"Content-Type": "application/json"}
    return None, {}


def ack(url: str, body: Any = None) -> CallbackResult:
    """Acknowledge async processing completion (synchronous).

    Args:
        url: The ack callback URL from ``delivery.ack_url``.
        body: Optional JSON-serializable body to send with the callback.
            Posthook currently ignores ack bodies.

    Returns:
        A ``CallbackResult`` indicating whether the ack was applied.

    Raises:
        CallbackError: For unexpected failures (401, 410, 5xx).
    """
    content, headers = _prepare_request(body)
    response = httpx.post(url, content=content, headers=headers)
    return _parse_callback_response(response, "ack", "completed")


def nack(url: str, body: Any = None) -> CallbackResult:
    """Reject async processing — triggers retry or failure (synchronous).

    Args:
        url: The nack callback URL from ``delivery.nack_url``.
        body: Optional JSON-serializable body explaining the failure.

    Returns:
        A ``CallbackResult`` indicating whether the nack was applied.

    Raises:
        CallbackError: For unexpected failures (401, 410, 5xx).
    """
    content, headers = _prepare_request(body)
    response = httpx.post(url, content=content, headers=headers)
    return _parse_callback_response(response, "nack", "nacked")


async def async_ack(url: str, body: Any = None) -> CallbackResult:
    """Acknowledge async processing completion (asynchronous).

    Args:
        url: The ack callback URL from ``delivery.ack_url``.
        body: Optional JSON-serializable body to send with the callback.
            Posthook currently ignores ack bodies.

    Returns:
        A ``CallbackResult`` indicating whether the ack was applied.

    Raises:
        CallbackError: For unexpected failures (401, 410, 5xx).
    """
    content, headers = _prepare_request(body)
    async with httpx.AsyncClient() as client:
        response = await client.post(url, content=content, headers=headers)
    return _parse_callback_response(response, "ack", "completed")


async def async_nack(url: str, body: Any = None) -> CallbackResult:
    """Reject async processing — triggers retry or failure (asynchronous).

    Args:
        url: The nack callback URL from ``delivery.nack_url``.
        body: Optional JSON-serializable body explaining the failure.

    Returns:
        A ``CallbackResult`` indicating whether the nack was applied.

    Raises:
        CallbackError: For unexpected failures (401, 410, 5xx).
    """
    content, headers = _prepare_request(body)
    async with httpx.AsyncClient() as client:
        response = await client.post(url, content=content, headers=headers)
    return _parse_callback_response(response, "nack", "nacked")

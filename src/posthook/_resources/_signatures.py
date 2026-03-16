from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping
from typing import Any, Awaitable, Callable

from .._errors import SignatureVerificationError
from .._listener import Result
from .._models import Delivery, _parse_dt

DEFAULT_TOLERANCE = 300  # 5 minutes in seconds


def _get_header(headers: Mapping[str, Any], name: str) -> str | None:
    """Case-insensitive header lookup."""
    # Try exact match first
    val = headers.get(name)
    if val is not None:
        if isinstance(val, list):
            return val[0] if val else None
        return str(val)

    # Try lowercase
    lower = name.lower()
    for key, value in headers.items():
        if key.lower() == lower:
            if isinstance(value, list):
                return value[0] if value else None
            return str(value)
    return None


def _compute_signature(key: str, timestamp: int, body_bytes: bytes) -> str:
    """Compute HMAC-SHA256 signature: v1,<hex>.

    Uses incremental mac.update() to avoid copying the body for large payloads.
    """
    mac = hmac.new(key.encode(), digestmod=hashlib.sha256)
    mac.update(f"{timestamp}.".encode())
    mac.update(body_bytes)
    return f"v1,{mac.hexdigest()}"


def _safe_compare(a: str, b: str) -> bool:
    """Constant-time string comparison."""
    return hmac.compare_digest(a.encode(), b.encode())


class SignaturesService:
    """Webhook signature verification. Shared by sync and async clients (no I/O)."""

    def __init__(self, signing_key: str | None = None) -> None:
        self._signing_key = signing_key

    def parse_delivery(
        self,
        body: bytes | str,
        headers: Mapping[str, Any],
        *,
        signing_key: str | None = None,
        tolerance: int = DEFAULT_TOLERANCE,
    ) -> Delivery:
        """Verify the webhook signature and parse the delivery payload.

        Args:
            body: The raw HTTP request body (bytes or string).
            headers: The request headers as a mapping (dict, HTTPMessage, etc.).
            signing_key: Override the client's signing key for this call.
            tolerance: Maximum age of the timestamp in seconds (default: 300).

        Returns:
            A Delivery object with the parsed and verified payload.

        Raises:
            SignatureVerificationError: If verification fails.
        """
        key = signing_key or self._signing_key
        if not key:
            raise SignatureVerificationError(
                "No signing key provided. Pass signing_key to the Posthook constructor "
                "or to parse_delivery()."
            )

        hook_id = _get_header(headers, "Posthook-Id") or ""

        timestamp_str = _get_header(headers, "Posthook-Timestamp")
        if not timestamp_str:
            raise SignatureVerificationError("Missing Posthook-Timestamp header")

        signature = _get_header(headers, "Posthook-Signature")
        if not signature:
            raise SignatureVerificationError("Missing Posthook-Signature header")

        try:
            timestamp = int(timestamp_str)
        except ValueError:
            raise SignatureVerificationError(
                f"Invalid Posthook-Timestamp: {timestamp_str}"
            )

        now = int(time.time())
        diff = abs(now - timestamp)
        if diff > tolerance:
            raise SignatureVerificationError(
                f"Timestamp too old: {diff}s difference exceeds {tolerance}s tolerance"
            )

        body_bytes = body if isinstance(body, bytes) else body.encode("utf-8")
        expected_sig = _compute_signature(key, timestamp, body_bytes)

        signatures = signature.split(" ")
        verified = False
        for sig in signatures:
            if _safe_compare(sig, expected_sig):
                verified = True
                break

        if not verified:
            raise SignatureVerificationError("Signature verification failed")

        try:
            payload = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SignatureVerificationError(
                f"Failed to parse delivery payload: {exc}"
            )

        # Extract async callback URLs (set both or neither).
        ack_url = _get_header(headers, "Posthook-Ack-URL")
        nack_url = _get_header(headers, "Posthook-Nack-URL")
        has_callbacks = bool(ack_url and nack_url)

        return Delivery(
            hook_id=hook_id,
            timestamp=timestamp,
            path=payload.get("path", ""),
            data=payload.get("data"),
            body=body_bytes,
            post_at=_parse_dt(payload.get("postAt", "")),
            posted_at=_parse_dt(payload.get("postedAt", "")),
            created_at=_parse_dt(payload.get("createdAt", "")),
            updated_at=_parse_dt(payload.get("updatedAt", "")),
            ack_url=ack_url if has_callbacks else None,
            nack_url=nack_url if has_callbacks else None,
        )


    def asgi_handler(
        self,
        handler: Callable[[Delivery], Awaitable[Result]],
    ) -> Callable:
        """Return an ASGI application that verifies signatures and dispatches to a handler.

        The returned ASGI app reads the request body, verifies the Posthook
        signature headers, parses the delivery, calls the handler, and returns
        an appropriate HTTP response.

        Args:
            handler: Async function that receives a Delivery and returns a Result.

        Returns:
            An ASGI application callable.

        Example::

            signatures = posthook.create_signatures("ph_sk_...")

            async def on_hook(delivery):
                print(delivery.data)
                return posthook.Result.ack()

            app = signatures.asgi_handler(on_hook)
            # Mount as an ASGI endpoint (e.g. with uvicorn, Starlette, etc.)
        """

        async def app(scope: dict, receive: Callable, send: Callable) -> None:
            if scope["type"] != "http":
                return

            body = b""
            while True:
                message = await receive()
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break

            headers = {
                k.decode(): v.decode() for k, v in scope.get("headers", [])
            }

            try:
                delivery = self.parse_delivery(body, headers)
            except Exception:
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                })
                await send({
                    "type": "http.response.body",
                    "body": b'{"error":"signature verification failed"}',
                })
                return

            try:
                result = await handler(delivery)
            except Exception:
                result = Result.nack()

            status_map = {"ack": 200, "accept": 202, "nack": 500}
            status = status_map.get(result.kind, 200)

            if result.kind == "nack":
                resp_body = b'{"error":"handler failed"}'
            else:
                resp_body = b'{"ok":true}'

            resp_headers: list[tuple[bytes, bytes]] = [(b"content-type", b"application/json")]
            if result.kind == "accept" and result.timeout is not None:
                resp_headers.append((b"posthook-async-timeout", str(result.timeout).encode()))

            await send({
                "type": "http.response.start",
                "status": status,
                "headers": resp_headers,
            })
            await send({
                "type": "http.response.body",
                "body": resp_body,
            })

        return app

    def wsgi_handler(
        self,
        handler: Callable[[Delivery], Result],
    ) -> Callable:
        """Return a WSGI application that verifies signatures and dispatches to a handler.

        The returned WSGI app reads the request body, verifies the Posthook
        signature headers, parses the delivery, calls the handler synchronously,
        and returns an appropriate HTTP response.

        Args:
            handler: Function that receives a Delivery and returns a Result.

        Returns:
            A WSGI application callable.

        Example::

            signatures = posthook.create_signatures("ph_sk_...")

            def on_hook(delivery):
                print(delivery.data)
                return posthook.Result.ack()

            app = signatures.wsgi_handler(on_hook)
            # Mount as a WSGI endpoint (e.g. with gunicorn, Flask, etc.)
        """

        def app(environ: dict, start_response: Callable) -> list[bytes]:
            content_length = int(environ.get("CONTENT_LENGTH", 0) or 0)
            body = environ["wsgi.input"].read(content_length) if content_length else b""

            headers: dict[str, str] = {}
            for key, value in environ.items():
                if key.startswith("HTTP_"):
                    header_name = key[5:].replace("_", "-").title()
                    headers[header_name] = value
            if "CONTENT_TYPE" in environ:
                headers["Content-Type"] = environ["CONTENT_TYPE"]

            try:
                delivery = self.parse_delivery(body, headers)
            except Exception:
                start_response("401 Unauthorized", [("Content-Type", "application/json")])
                return [b'{"error":"signature verification failed"}']

            try:
                result = handler(delivery)
            except Exception:
                result = Result.nack()

            status_map = {
                "ack": "200 OK",
                "accept": "202 Accepted",
                "nack": "500 Internal Server Error",
            }
            status = status_map.get(result.kind, "200 OK")
            resp_headers: list[tuple[str, str]] = [("Content-Type", "application/json")]
            if result.kind == "accept" and result.timeout is not None:
                resp_headers.append(("Posthook-Async-Timeout", str(result.timeout)))
            start_response(status, resp_headers)

            if result.kind == "nack":
                return [b'{"error":"handler failed"}']
            return [b'{"ok":true}']

        return app


def create_signatures(signing_key: str | None = None) -> SignaturesService:
    """Create a standalone SignaturesService with fail-fast key validation.

    This is the recommended way to create a ``SignaturesService`` for standalone
    webhook verification (outside the ``Posthook`` / ``AsyncPosthook`` clients).
    It ensures a valid signing key is available at construction time rather than
    deferring the error to the first ``parse_delivery()`` call.

    Args:
        signing_key: Your Posthook signing key. Falls back to the
            ``POSTHOOK_SIGNING_KEY`` environment variable if not provided.

    Returns:
        A configured ``SignaturesService`` instance.

    Raises:
        ValueError: If no signing key is provided and the environment variable
            is not set or empty.
    """
    resolved = signing_key or os.environ.get("POSTHOOK_SIGNING_KEY", "")
    if not resolved:
        raise ValueError(
            "No signing key provided. Pass signing_key to create_signatures() "
            "or set the POSTHOOK_SIGNING_KEY environment variable."
        )
    return SignaturesService(resolved)

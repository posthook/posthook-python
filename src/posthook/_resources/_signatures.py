from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping
from typing import Any

from .._errors import SignatureVerificationError
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
        )


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

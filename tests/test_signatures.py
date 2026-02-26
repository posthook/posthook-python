from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import pytest

import posthook
from posthook import create_signatures
from posthook._resources._signatures import SignaturesService


def _make_signature(key: str, timestamp: int, body: str) -> str:
    mac = hmac.new(key.encode(), digestmod=hashlib.sha256)
    mac.update(f"{timestamp}.".encode())
    mac.update(body.encode())
    return f"v1,{mac.hexdigest()}"


def _make_delivery(
    key: str = "ph_sk_test",
    timestamp: int | None = None,
    body_dict: dict | None = None,
) -> tuple[bytes, dict[str, str]]:
    ts = timestamp or int(time.time())
    body_dict = body_dict or {
        "id": "hook-123",
        "path": "/webhooks/test",
        "data": {"userId": "abc"},
        "postAt": "2026-03-01T12:00:00Z",
        "postedAt": "2026-03-01T12:00:01Z",
        "createdAt": "2026-02-23T10:00:00Z",
        "updatedAt": "2026-02-23T10:00:00Z",
    }
    body = json.dumps(body_dict)
    sig = _make_signature(key, ts, body)
    headers = {
        "Posthook-Id": "hook-123",
        "Posthook-Timestamp": str(ts),
        "Posthook-Signature": sig,
    }
    return body.encode(), headers


class TestSignatureVerification:
    def test_valid_signature(self) -> None:
        svc = SignaturesService("ph_sk_test")
        body, headers = _make_delivery()
        delivery = svc.parse_delivery(body, headers)
        assert delivery.hook_id == "hook-123"
        assert delivery.path == "/webhooks/test"
        assert delivery.data == {"userId": "abc"}
        assert delivery.post_at == datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert delivery.posted_at == datetime(2026, 3, 1, 12, 0, 1, tzinfo=timezone.utc)

    def test_valid_signature_string_body(self) -> None:
        svc = SignaturesService("ph_sk_test")
        body_bytes, headers = _make_delivery()
        delivery = svc.parse_delivery(body_bytes.decode(), headers)
        assert delivery.hook_id == "hook-123"

    def test_key_rotation_multiple_signatures(self) -> None:
        svc = SignaturesService("ph_sk_new_key")
        ts = int(time.time())
        body_dict = {
            "id": "hook-123",
            "path": "/test",
            "data": {},
            "postAt": "",
            "postedAt": "",
            "createdAt": "",
            "updatedAt": "",
        }
        body = json.dumps(body_dict)

        old_sig = _make_signature("ph_sk_old_key", ts, body)
        new_sig = _make_signature("ph_sk_new_key", ts, body)
        combined_sig = f"{old_sig} {new_sig}"

        headers = {
            "Posthook-Id": "hook-123",
            "Posthook-Timestamp": str(ts),
            "Posthook-Signature": combined_sig,
        }
        delivery = svc.parse_delivery(body.encode(), headers)
        assert delivery.hook_id == "hook-123"

    def test_expired_timestamp(self) -> None:
        svc = SignaturesService("ph_sk_test")
        old_ts = int(time.time()) - 600  # 10 minutes ago
        body, headers = _make_delivery(timestamp=old_ts)
        with pytest.raises(posthook.SignatureVerificationError, match="Timestamp too old"):
            svc.parse_delivery(body, headers)

    def test_custom_tolerance(self) -> None:
        svc = SignaturesService("ph_sk_test")
        old_ts = int(time.time()) - 600
        body, headers = _make_delivery(timestamp=old_ts)
        # With 15 minute tolerance, this should pass
        delivery = svc.parse_delivery(body, headers, tolerance=900)
        assert delivery.hook_id == "hook-123"

    def test_tampered_body(self) -> None:
        svc = SignaturesService("ph_sk_test")
        body, headers = _make_delivery()
        tampered = body + b"extra"
        with pytest.raises(
            posthook.SignatureVerificationError, match="Signature verification failed"
        ):
            svc.parse_delivery(tampered, headers)

    def test_missing_id_header(self) -> None:
        svc = SignaturesService("ph_sk_test")
        body, headers = _make_delivery()
        del headers["Posthook-Id"]
        delivery = svc.parse_delivery(body, headers)
        assert delivery.hook_id == ""

    def test_missing_timestamp_header(self) -> None:
        svc = SignaturesService("ph_sk_test")
        body, headers = _make_delivery()
        del headers["Posthook-Timestamp"]
        with pytest.raises(posthook.SignatureVerificationError, match="Missing Posthook-Timestamp"):
            svc.parse_delivery(body, headers)

    def test_missing_signature_header(self) -> None:
        svc = SignaturesService("ph_sk_test")
        body, headers = _make_delivery()
        del headers["Posthook-Signature"]
        with pytest.raises(posthook.SignatureVerificationError, match="Missing Posthook-Signature"):
            svc.parse_delivery(body, headers)

    def test_no_signing_key(self) -> None:
        svc = SignaturesService()
        body, headers = _make_delivery()
        with pytest.raises(posthook.SignatureVerificationError, match="No signing key"):
            svc.parse_delivery(body, headers)

    def test_signing_key_override(self) -> None:
        svc = SignaturesService("wrong_key")
        body, headers = _make_delivery(key="ph_sk_test")
        delivery = svc.parse_delivery(body, headers, signing_key="ph_sk_test")
        assert delivery.hook_id == "hook-123"

    def test_invalid_timestamp(self) -> None:
        svc = SignaturesService("ph_sk_test")
        body, headers = _make_delivery()
        headers["Posthook-Timestamp"] = "not-a-number"
        with pytest.raises(posthook.SignatureVerificationError, match="Invalid Posthook-Timestamp"):
            svc.parse_delivery(body, headers)

    def test_case_insensitive_headers(self) -> None:
        svc = SignaturesService("ph_sk_test")
        body_bytes, original_headers = _make_delivery()
        # Use lowercase header names
        headers = {
            "posthook-id": original_headers["Posthook-Id"],
            "posthook-timestamp": original_headers["Posthook-Timestamp"],
            "posthook-signature": original_headers["Posthook-Signature"],
        }
        delivery = svc.parse_delivery(body_bytes, headers)
        assert delivery.hook_id == "hook-123"


class TestCreateSignatures:
    def test_explicit_key(self) -> None:
        svc = create_signatures("ph_sk_test")
        assert isinstance(svc, SignaturesService)
        body, headers = _make_delivery()
        delivery = svc.parse_delivery(body, headers)
        assert delivery.hook_id == "hook-123"

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError, match="No signing key provided"):
            create_signatures(None)

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="No signing key provided"):
            create_signatures("")

    def test_no_argument_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("POSTHOOK_SIGNING_KEY", raising=False)
        with pytest.raises(ValueError, match="No signing key provided"):
            create_signatures()

    def test_env_var_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POSTHOOK_SIGNING_KEY", "ph_sk_test")
        svc = create_signatures()
        assert isinstance(svc, SignaturesService)
        body, headers = _make_delivery()
        delivery = svc.parse_delivery(body, headers)
        assert delivery.hook_id == "hook-123"

    def test_explicit_key_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POSTHOOK_SIGNING_KEY", "wrong_key")
        svc = create_signatures("ph_sk_test")
        body, headers = _make_delivery()
        delivery = svc.parse_delivery(body, headers)
        assert delivery.hook_id == "hook-123"

    def test_importable_from_package(self) -> None:
        assert hasattr(posthook, "create_signatures")
        assert posthook.create_signatures is create_signatures

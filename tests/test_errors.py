from __future__ import annotations

import posthook
from posthook._errors import _create_error


class TestErrorHierarchy:
    def test_base_error(self) -> None:
        err = posthook.PosthookError("test error", status_code=500, code="test")
        assert str(err) == "test error"
        assert err.status_code == 500
        assert err.code == "test"
        assert err.message == "test error"

    def test_bad_request(self) -> None:
        err = posthook.BadRequestError("invalid input")
        assert err.status_code == 400
        assert err.code == "bad_request"
        assert isinstance(err, posthook.PosthookError)

    def test_authentication_error(self) -> None:
        err = posthook.AuthenticationError("invalid key")
        assert err.status_code == 401
        assert err.code == "authentication_error"
        assert isinstance(err, posthook.PosthookError)

    def test_forbidden_error(self) -> None:
        err = posthook.ForbiddenError("access denied")
        assert err.status_code == 403
        assert isinstance(err, posthook.PosthookError)

    def test_not_found_error(self) -> None:
        err = posthook.NotFoundError("not found")
        assert err.status_code == 404
        assert isinstance(err, posthook.PosthookError)

    def test_payload_too_large(self) -> None:
        err = posthook.PayloadTooLargeError("too large")
        assert err.status_code == 413
        assert isinstance(err, posthook.PosthookError)

    def test_rate_limit_error(self) -> None:
        err = posthook.RateLimitError("slow down")
        assert err.status_code == 429
        assert isinstance(err, posthook.PosthookError)

    def test_internal_server_error(self) -> None:
        err = posthook.InternalServerError("server error")
        assert err.status_code == 500
        assert isinstance(err, posthook.PosthookError)

    def test_internal_server_error_custom_status(self) -> None:
        err = posthook.InternalServerError("bad gateway", status_code=502)
        assert err.status_code == 502

    def test_connection_error(self) -> None:
        err = posthook.PosthookConnectionError("timeout")
        assert err.status_code is None
        assert err.code == "connection_error"
        assert isinstance(err, posthook.PosthookError)

    def test_signature_verification_error(self) -> None:
        err = posthook.SignatureVerificationError("bad sig")
        assert err.status_code is None
        assert err.code == "signature_verification_error"
        assert isinstance(err, posthook.PosthookError)

    def test_error_with_headers(self) -> None:
        headers = {"x-request-id": "abc123"}
        err = posthook.BadRequestError("bad", headers)
        assert err.headers == headers


class TestCreateError:
    def test_400(self) -> None:
        err = _create_error(400, "bad request")
        assert isinstance(err, posthook.BadRequestError)

    def test_401(self) -> None:
        err = _create_error(401, "unauthorized")
        assert isinstance(err, posthook.AuthenticationError)

    def test_403(self) -> None:
        err = _create_error(403, "forbidden")
        assert isinstance(err, posthook.ForbiddenError)

    def test_404(self) -> None:
        err = _create_error(404, "not found")
        assert isinstance(err, posthook.NotFoundError)

    def test_413(self) -> None:
        err = _create_error(413, "too large")
        assert isinstance(err, posthook.PayloadTooLargeError)

    def test_429(self) -> None:
        err = _create_error(429, "rate limited")
        assert isinstance(err, posthook.RateLimitError)

    def test_500(self) -> None:
        err = _create_error(500, "server error")
        assert isinstance(err, posthook.InternalServerError)

    def test_502(self) -> None:
        err = _create_error(502, "bad gateway")
        assert isinstance(err, posthook.InternalServerError)
        assert err.status_code == 502

    def test_unknown(self) -> None:
        err = _create_error(418, "teapot")
        assert isinstance(err, posthook.PosthookError)
        assert err.code == "unknown_error"

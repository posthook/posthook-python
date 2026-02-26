from __future__ import annotations


class PosthookError(Exception):
    """Base error class for all Posthook SDK errors."""

    status_code: int | None
    code: str
    message: str
    headers: dict[str, str] | None

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.headers = headers

    def __repr__(self) -> str:
        cls = self.__class__.__name__
        return f"{cls}(message={self.message!r}, status_code={self.status_code})"


class BadRequestError(PosthookError):
    """Raised for HTTP 400 responses."""

    def __init__(self, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message, status_code=400, code="bad_request", headers=headers)


class AuthenticationError(PosthookError):
    """Raised for HTTP 401 responses."""

    def __init__(self, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message, status_code=401, code="authentication_error", headers=headers)


class ForbiddenError(PosthookError):
    """Raised for HTTP 403 responses."""

    def __init__(self, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message, status_code=403, code="forbidden", headers=headers)


class NotFoundError(PosthookError):
    """Raised for HTTP 404 responses."""

    def __init__(self, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message, status_code=404, code="not_found", headers=headers)


class PayloadTooLargeError(PosthookError):
    """Raised for HTTP 413 responses."""

    def __init__(self, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message, status_code=413, code="payload_too_large", headers=headers)


class RateLimitError(PosthookError):
    """Raised for HTTP 429 responses."""

    def __init__(self, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message, status_code=429, code="rate_limit_exceeded", headers=headers)


class InternalServerError(PosthookError):
    """Raised for HTTP 5xx responses."""

    def __init__(
        self, message: str, status_code: int = 500, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message, status_code=status_code, code="internal_error", headers=headers)


class PosthookConnectionError(PosthookError):
    """Raised for network or timeout errors.

    Named ``PosthookConnectionError`` to avoid shadowing the builtin
    ``ConnectionError``.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="connection_error")


class SignatureVerificationError(PosthookError):
    """Raised when webhook signature verification fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="signature_verification_error")


def _create_error(
    status_code: int,
    message: str,
    headers: dict[str, str] | None = None,
) -> PosthookError:
    """Create the appropriate error subclass for the given HTTP status code."""
    if status_code == 400:
        return BadRequestError(message, headers)
    if status_code == 401:
        return AuthenticationError(message, headers)
    if status_code == 403:
        return ForbiddenError(message, headers)
    if status_code == 404:
        return NotFoundError(message, headers)
    if status_code == 413:
        return PayloadTooLargeError(message, headers)
    if status_code == 429:
        return RateLimitError(message, headers)
    if status_code >= 500:
        return InternalServerError(message, status_code, headers)
    return PosthookError(message, status_code=status_code, code="unknown_error", headers=headers)

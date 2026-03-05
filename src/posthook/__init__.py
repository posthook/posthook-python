"""Posthook Python SDK — schedule, manage, and verify webhooks."""

from ._callbacks import ack, async_ack, async_nack, nack
from ._client import AsyncPosthook, Posthook
from ._errors import (
    AuthenticationError,
    BadRequestError,
    CallbackError,
    ForbiddenError,
    InternalServerError,
    NotFoundError,
    PayloadTooLargeError,
    PosthookConnectionError,
    PosthookError,
    RateLimitError,
    SignatureVerificationError,
)
from ._models import (
    SORT_BY_CREATED_AT,
    SORT_BY_POST_AT,
    SORT_ORDER_ASC,
    SORT_ORDER_DESC,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RETRY,
    STRATEGY_EXPONENTIAL,
    STRATEGY_FIXED,
    BulkActionResult,
    CallbackResult,
    Delivery,
    Hook,
    HookRetryOverride,
    QuotaInfo,
)
from ._resources._signatures import SignaturesService, create_signatures
from ._version import VERSION as __version__

__all__ = [
    # Clients
    "Posthook",
    "AsyncPosthook",
    # Models
    "Hook",
    "HookRetryOverride",
    "QuotaInfo",
    "BulkActionResult",
    "CallbackResult",
    "Delivery",
    # Callbacks
    "ack",
    "nack",
    "async_ack",
    "async_nack",
    # Resources
    "SignaturesService",
    "create_signatures",
    # Constants
    "STATUS_PENDING",
    "STATUS_RETRY",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "SORT_BY_POST_AT",
    "SORT_BY_CREATED_AT",
    "SORT_ORDER_ASC",
    "SORT_ORDER_DESC",
    "STRATEGY_FIXED",
    "STRATEGY_EXPONENTIAL",
    # Errors
    "PosthookError",
    "BadRequestError",
    "AuthenticationError",
    "CallbackError",
    "ForbiddenError",
    "NotFoundError",
    "PayloadTooLargeError",
    "RateLimitError",
    "InternalServerError",
    "PosthookConnectionError",
    "SignatureVerificationError",
    # Version
    "__version__",
]

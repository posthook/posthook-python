from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Hook status constants.
STATUS_PENDING = "pending"
STATUS_RETRY = "retry"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# Sort field constants.
SORT_BY_POST_AT = "postAt"
SORT_BY_CREATED_AT = "createdAt"

# Sort order constants.
SORT_ORDER_ASC = "ASC"
SORT_ORDER_DESC = "DESC"

# Retry strategy constants.
STRATEGY_FIXED = "fixed"
STRATEGY_EXPONENTIAL = "exponential"


def _parse_dt(value: str) -> datetime:
    """Parse an RFC 3339 timestamp string into a datetime."""
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    # Handle "Z" suffix which Python's fromisoformat doesn't support before 3.11
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class QuotaInfo:
    """Hook quota information parsed from response headers."""

    limit: int
    usage: int
    remaining: int
    resets_at: datetime | None


@dataclass(frozen=True)
class HookRetryOverride:
    """Per-hook retry configuration that overrides project defaults."""

    min_retries: int
    delay_secs: int
    strategy: str
    backoff_factor: float | None = None
    max_delay_secs: int | None = None
    jitter: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HookRetryOverride:
        return cls(
            min_retries=data["minRetries"],
            delay_secs=data["delaySecs"],
            strategy=data["strategy"],
            backoff_factor=data.get("backoffFactor"),
            max_delay_secs=data.get("maxDelaySecs"),
            jitter=data.get("jitter", False),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "minRetries": self.min_retries,
            "delaySecs": self.delay_secs,
            "strategy": self.strategy,
            "jitter": self.jitter,
        }
        if self.backoff_factor is not None:
            d["backoffFactor"] = self.backoff_factor
        if self.max_delay_secs is not None:
            d["maxDelaySecs"] = self.max_delay_secs
        return d


@dataclass(frozen=True)
class HookSequenceData:
    """Sequence context for a hook that is part of a sequence."""

    sequence_id: str
    step_name: str
    sequence_last_run_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HookSequenceData:
        return cls(
            sequence_id=data["sequenceID"],
            step_name=data["stepName"],
            sequence_last_run_at=data["sequenceLastRunAt"],
        )


@dataclass
class Hook:
    """A scheduled webhook as returned by the Posthook API."""

    id: str
    path: str
    data: Any
    post_at: datetime
    status: str
    post_duration_seconds: float
    created_at: datetime
    updated_at: datetime
    domain: str | None = None
    attempts: int = 0
    failure_error: str = ""
    sequence_data: HookSequenceData | None = None
    retry_override: HookRetryOverride | None = None
    quota: QuotaInfo | None = field(default=None)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Hook:
        seq = data.get("sequenceData")
        retry = data.get("retryOverride")
        return cls(
            id=data["id"],
            path=data["path"],
            data=data.get("data"),
            post_at=_parse_dt(data["postAt"]),
            status=data["status"],
            post_duration_seconds=data.get("postDurationSeconds", 0.0),
            created_at=_parse_dt(data.get("createdAt", "")),
            updated_at=_parse_dt(data.get("updatedAt", "")),
            domain=data.get("domain"),
            attempts=data.get("attempts", 0),
            failure_error=data.get("failureError", ""),
            sequence_data=HookSequenceData.from_dict(seq) if seq else None,
            retry_override=HookRetryOverride.from_dict(retry) if retry else None,
        )


@dataclass(frozen=True)
class BulkActionResult:
    """Result of a bulk action on hooks."""

    affected: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BulkActionResult:
        return cls(affected=data.get("affected", 0))


@dataclass(frozen=True)
class Delivery:
    """A parsed and verified webhook delivery."""

    hook_id: str
    timestamp: int
    path: str
    data: Any
    body: bytes
    post_at: datetime
    posted_at: datetime
    created_at: datetime
    updated_at: datetime

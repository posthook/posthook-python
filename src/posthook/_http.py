from __future__ import annotations

import logging
import platform
import time
from datetime import datetime
from typing import Any

import httpx

from ._errors import PosthookConnectionError, PosthookError, _create_error
from ._models import QuotaInfo
from ._version import VERSION

logger = logging.getLogger("posthook")

DEFAULT_BASE_URL = "https://api.posthook.io"
DEFAULT_TIMEOUT = 30.0


def _parse_quota(headers: httpx.Headers) -> QuotaInfo | None:
    limit = headers.get("posthook-hookquota-limit")
    if not limit:
        return None

    resets_at: datetime | None = None
    raw_resets = headers.get("posthook-hookquota-resets-at", "")
    if raw_resets:
        try:
            resets_at = datetime.fromisoformat(raw_resets.replace("Z", "+00:00"))
        except ValueError:
            pass

    return QuotaInfo(
        limit=int(limit),
        usage=int(headers.get("posthook-hookquota-usage", "0")),
        remaining=int(headers.get("posthook-hookquota-remaining", "0")),
        resets_at=resets_at,
    )


_USER_AGENT = (
    f"posthook-python/{VERSION}"
    f" (Python {platform.python_version()}; {platform.system()})"
)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "X-API-Key": api_key,
        "User-Agent": _USER_AGENT,
        "Content-Type": "application/json",
    }


def _unwrap_data(body: dict[str, Any]) -> Any:
    """Extract 'data' from the API response envelope."""
    return body.get("data")


def _extract_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
        return body.get("error", f"HTTP {response.status_code}")
    except Exception:
        return f"HTTP {response.status_code}"


class SyncHttpClient:
    """Synchronous HTTP client wrapping httpx.Client."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=timeout,
            headers=_headers(api_key),
        )
        if http_client is not None:
            self._client.headers.update(_headers(api_key))

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> tuple[Any, httpx.Headers]:
        url = f"{self._base_url}{path}"

        # Filter out None values from params
        if params:
            params = {k: v for k, v in params.items() if v is not None}

        start = time.monotonic()
        try:
            kwargs: dict[str, Any] = {"json": json, "params": params}
            if timeout is not None:
                kwargs["timeout"] = timeout
            response = self._client.request(
                method, url, **kwargs
            )
            elapsed = time.monotonic() - start
            logger.debug("%s %s -> %d (%.3fs)", method, path, response.status_code, elapsed)

            if response.status_code >= 400:
                msg = _extract_error_message(response)
                hdrs = dict(response.headers)
                raise _create_error(response.status_code, msg, hdrs)

            body = response.json()
            return body, response.headers

        except PosthookError:
            raise
        except httpx.TimeoutException as exc:
            raise PosthookConnectionError(f"Request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise PosthookConnectionError(f"Network error: {exc}") from exc

    def request_data(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> tuple[Any, httpx.Headers]:
        """Make a request and return (unwrapped_data, headers)."""
        body, headers = self.request(method, path, json=json, params=params, timeout=timeout)
        return _unwrap_data(body), headers

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class AsyncHttpClient:
    """Asynchronous HTTP client wrapping httpx.AsyncClient."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout,
            headers=_headers(api_key),
        )
        if http_client is not None:
            self._client.headers.update(_headers(api_key))

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> tuple[Any, httpx.Headers]:
        url = f"{self._base_url}{path}"

        if params:
            params = {k: v for k, v in params.items() if v is not None}

        start = time.monotonic()
        try:
            kwargs: dict[str, Any] = {"json": json, "params": params}
            if timeout is not None:
                kwargs["timeout"] = timeout
            response = await self._client.request(
                method, url, **kwargs
            )
            elapsed = time.monotonic() - start
            logger.debug("%s %s -> %d (%.3fs)", method, path, response.status_code, elapsed)

            if response.status_code >= 400:
                msg = _extract_error_message(response)
                hdrs = dict(response.headers)
                raise _create_error(response.status_code, msg, hdrs)

            body = response.json()
            return body, response.headers

        except PosthookError:
            raise
        except httpx.TimeoutException as exc:
            raise PosthookConnectionError(f"Request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise PosthookConnectionError(f"Network error: {exc}") from exc

    async def request_data(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> tuple[Any, httpx.Headers]:
        body, headers = await self.request(method, path, json=json, params=params, timeout=timeout)
        return _unwrap_data(body), headers

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

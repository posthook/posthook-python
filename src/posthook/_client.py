from __future__ import annotations

import os
from typing import Any

import httpx

from ._errors import AuthenticationError
from ._http import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    AsyncHttpClient,
    SyncHttpClient,
)
from ._resources._hooks import AsyncHooksService, HooksService
from ._resources._signatures import SignaturesService


class Posthook:
    """Synchronous Posthook API client.

    Args:
        api_key: Your Posthook API key. Falls back to the ``POSTHOOK_API_KEY``
            environment variable if not provided.
        base_url: Override the API base URL.
        timeout: Request timeout in seconds.
        signing_key: Key for webhook signature verification. Falls back to
            ``POSTHOOK_SIGNING_KEY`` env var.
        http_client: A custom ``httpx.Client`` instance.
    """

    hooks: HooksService
    signatures: SignaturesService

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        signing_key: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("POSTHOOK_API_KEY", "")
        if not resolved_key:
            raise AuthenticationError(
                "No API key provided. Pass api_key to the Posthook constructor "
                "or set the POSTHOOK_API_KEY environment variable."
            )

        resolved_signing_key = signing_key or os.environ.get("POSTHOOK_SIGNING_KEY")

        self._http = SyncHttpClient(
            resolved_key,
            base_url=base_url,
            timeout=timeout,
            http_client=http_client,
        )
        self.hooks = HooksService(self._http)
        self.signatures = SignaturesService(resolved_signing_key)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def __enter__(self) -> Posthook:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class AsyncPosthook:
    """Asynchronous Posthook API client.

    Args:
        api_key: Your Posthook API key. Falls back to the ``POSTHOOK_API_KEY``
            environment variable if not provided.
        base_url: Override the API base URL.
        timeout: Request timeout in seconds.
        signing_key: Key for webhook signature verification. Falls back to
            ``POSTHOOK_SIGNING_KEY`` env var.
        http_client: A custom ``httpx.AsyncClient`` instance.
    """

    hooks: AsyncHooksService
    signatures: SignaturesService

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        signing_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("POSTHOOK_API_KEY", "")
        if not resolved_key:
            raise AuthenticationError(
                "No API key provided. Pass api_key to the AsyncPosthook constructor "
                "or set the POSTHOOK_API_KEY environment variable."
            )

        resolved_signing_key = signing_key or os.environ.get("POSTHOOK_SIGNING_KEY")

        self._http = AsyncHttpClient(
            resolved_key,
            base_url=base_url,
            timeout=timeout,
            http_client=http_client,
        )
        self.hooks = AsyncHooksService(self._http)
        self.signatures = SignaturesService(resolved_signing_key)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.close()

    async def __aenter__(self) -> AsyncPosthook:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

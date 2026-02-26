from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterator
from urllib.parse import quote


from .._errors import NotFoundError
from .._http import AsyncHttpClient, SyncHttpClient, _parse_quota
from .._models import BulkActionResult, Hook, HookRetryOverride


def _build_schedule_body(
    path: str,
    *,
    data: Any = None,
    post_at: datetime | str | None = None,
    post_at_local: str | None = None,
    timezone_str: str | None = None,
    post_in: str | None = None,
    retry_override: HookRetryOverride | None = None,
) -> dict[str, Any]:
    modes = sum(x is not None for x in (post_at, post_at_local, post_in))
    if modes == 0:
        raise ValueError(
            "Exactly one scheduling mode is required: post_at, post_at_local, or post_in"
        )
    if modes > 1:
        raise ValueError(
            "Only one scheduling mode allowed: post_at, post_at_local, or post_in"
        )

    body: dict[str, Any] = {"path": path}
    if data is not None:
        body["data"] = data
    if post_at is not None:
        if isinstance(post_at, datetime):
            if post_at.tzinfo is None:
                raise ValueError(
                    "post_at datetime must be timezone-aware. "
                    "Use datetime.now(timezone.utc) or datetime(..., tzinfo=timezone.utc)"
                )
            body["postAt"] = post_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            body["postAt"] = post_at
    elif post_at_local is not None:
        body["postAtLocal"] = post_at_local
        if timezone_str is not None:
            body["timezone"] = timezone_str
    elif post_in is not None:
        body["postIn"] = post_in
    if retry_override is not None:
        body["retryOverride"] = retry_override.to_dict()
    return body


def _build_list_params(
    *,
    status: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    post_at_before: str | None = None,
    post_at_after: str | None = None,
    created_at_before: str | None = None,
    created_at_after: str | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if status is not None:
        params["status"] = status
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    if post_at_before is not None:
        params["postAtBefore"] = post_at_before
    if post_at_after is not None:
        params["postAtAfter"] = post_at_after
    if created_at_before is not None:
        params["createdAtBefore"] = created_at_before
    if created_at_after is not None:
        params["createdAtAfter"] = created_at_after
    if sort_by is not None:
        params["sortBy"] = sort_by
    if sort_order is not None:
        params["sortOrder"] = sort_order
    return params


def _build_bulk_body_by_ids(
    hook_ids: list[str],
) -> dict[str, Any]:
    return {"hookIDs": hook_ids}


def _build_bulk_body_by_filter(
    start_time: str,
    end_time: str,
    limit: int,
    *,
    endpoint_key: str | None = None,
    sequence_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "startTime": start_time,
        "endTime": end_time,
        "limit": limit,
    }
    if endpoint_key is not None:
        body["endpointKey"] = endpoint_key
    if sequence_id is not None:
        body["sequenceID"] = sequence_id
    return body


class BulkActions:
    """Synchronous sub-resource for bulk hook actions."""

    def __init__(self, http: SyncHttpClient) -> None:
        self._http = http

    def _do(
        self, path: str, body: dict[str, Any], *, timeout: float | None = None,
    ) -> BulkActionResult:
        data, _ = self._http.request_data("POST", path, json=body, timeout=timeout)
        return BulkActionResult.from_dict(data)

    def retry(
        self, hook_ids: list[str], *, timeout: float | None = None,
    ) -> BulkActionResult:
        return self._do(
            "/v1/hooks/bulk/retry", _build_bulk_body_by_ids(hook_ids),
            timeout=timeout,
        )

    def retry_by_filter(
        self,
        start_time: str,
        end_time: str,
        limit: int,
        *,
        endpoint_key: str | None = None,
        sequence_id: str | None = None,
        timeout: float | None = None,
    ) -> BulkActionResult:
        return self._do(
            "/v1/hooks/bulk/retry",
            _build_bulk_body_by_filter(
                start_time, end_time, limit,
                endpoint_key=endpoint_key, sequence_id=sequence_id,
            ),
            timeout=timeout,
        )

    def replay(
        self, hook_ids: list[str], *, timeout: float | None = None,
    ) -> BulkActionResult:
        return self._do(
            "/v1/hooks/bulk/replay", _build_bulk_body_by_ids(hook_ids),
            timeout=timeout,
        )

    def replay_by_filter(
        self,
        start_time: str,
        end_time: str,
        limit: int,
        *,
        endpoint_key: str | None = None,
        sequence_id: str | None = None,
        timeout: float | None = None,
    ) -> BulkActionResult:
        return self._do(
            "/v1/hooks/bulk/replay",
            _build_bulk_body_by_filter(
                start_time, end_time, limit,
                endpoint_key=endpoint_key, sequence_id=sequence_id,
            ),
            timeout=timeout,
        )

    def cancel(
        self, hook_ids: list[str], *, timeout: float | None = None,
    ) -> BulkActionResult:
        return self._do(
            "/v1/hooks/bulk/cancel", _build_bulk_body_by_ids(hook_ids),
            timeout=timeout,
        )

    def cancel_by_filter(
        self,
        start_time: str,
        end_time: str,
        limit: int,
        *,
        endpoint_key: str | None = None,
        sequence_id: str | None = None,
        timeout: float | None = None,
    ) -> BulkActionResult:
        return self._do(
            "/v1/hooks/bulk/cancel",
            _build_bulk_body_by_filter(
                start_time, end_time, limit,
                endpoint_key=endpoint_key, sequence_id=sequence_id,
            ),
            timeout=timeout,
        )


class HooksService:
    """Synchronous resource for managing hooks."""

    def __init__(self, http: SyncHttpClient) -> None:
        self._http = http
        self.bulk = BulkActions(http)

    def schedule(
        self,
        path: str,
        *,
        data: Any = None,
        post_at: datetime | str | None = None,
        post_at_local: str | None = None,
        timezone: str | None = None,
        post_in: str | None = None,
        retry_override: HookRetryOverride | None = None,
        timeout: float | None = None,
    ) -> Hook:
        body = _build_schedule_body(
            path,
            data=data,
            post_at=post_at,
            post_at_local=post_at_local,
            timezone_str=timezone,
            post_in=post_in,
            retry_override=retry_override,
        )
        resp_data, headers = self._http.request_data(
            "POST", "/v1/hooks", json=body, timeout=timeout,
        )
        hook = Hook.from_dict(resp_data)
        hook.quota = _parse_quota(headers)
        return hook

    def get(self, id: str, *, timeout: float | None = None) -> Hook:
        if not id:
            raise ValueError("hook id is required")
        data, _ = self._http.request_data(
            "GET", f"/v1/hooks/{quote(id, safe='')}", timeout=timeout,
        )
        return Hook.from_dict(data)

    def list(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        post_at_before: str | None = None,
        post_at_after: str | None = None,
        created_at_before: str | None = None,
        created_at_after: str | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        timeout: float | None = None,
    ) -> list[Hook]:
        params = _build_list_params(
            status=status,
            limit=limit,
            offset=offset,
            post_at_before=post_at_before,
            post_at_after=post_at_after,
            created_at_before=created_at_before,
            created_at_after=created_at_after,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        raw_list, _ = self._http.request_data(
            "GET", "/v1/hooks", params=params, timeout=timeout,
        )
        return [Hook.from_dict(h) for h in (raw_list or [])]

    def list_all(
        self,
        *,
        status: str | None = None,
        post_at_after: str | None = None,
        page_size: int = 100,
        timeout: float | None = None,
    ) -> Iterator[Hook]:
        cursor: str | None = post_at_after
        while True:
            hooks = self.list(
                status=status,
                limit=page_size,
                sort_by="postAt",
                sort_order="ASC",
                post_at_after=cursor,
                timeout=timeout,
            )
            yield from hooks
            if len(hooks) < page_size:
                break
            cursor = hooks[-1].post_at.astimezone(timezone.utc).isoformat()

    def delete(self, id: str, *, timeout: float | None = None) -> None:
        if not id:
            raise ValueError("hook id is required")
        try:
            self._http.request_data(
                "DELETE", f"/v1/hooks/{quote(id, safe='')}", timeout=timeout,
            )
        except NotFoundError:
            pass


class AsyncBulkActions:
    """Asynchronous sub-resource for bulk hook actions."""

    def __init__(self, http: AsyncHttpClient) -> None:
        self._http = http

    async def _do(
        self, path: str, body: dict[str, Any], *, timeout: float | None = None,
    ) -> BulkActionResult:
        data, _ = await self._http.request_data("POST", path, json=body, timeout=timeout)
        return BulkActionResult.from_dict(data)

    async def retry(
        self, hook_ids: list[str], *, timeout: float | None = None,
    ) -> BulkActionResult:
        return await self._do(
            "/v1/hooks/bulk/retry", _build_bulk_body_by_ids(hook_ids),
            timeout=timeout,
        )

    async def retry_by_filter(
        self,
        start_time: str,
        end_time: str,
        limit: int,
        *,
        endpoint_key: str | None = None,
        sequence_id: str | None = None,
        timeout: float | None = None,
    ) -> BulkActionResult:
        return await self._do(
            "/v1/hooks/bulk/retry",
            _build_bulk_body_by_filter(
                start_time, end_time, limit,
                endpoint_key=endpoint_key, sequence_id=sequence_id,
            ),
            timeout=timeout,
        )

    async def replay(
        self, hook_ids: list[str], *, timeout: float | None = None,
    ) -> BulkActionResult:
        return await self._do(
            "/v1/hooks/bulk/replay", _build_bulk_body_by_ids(hook_ids),
            timeout=timeout,
        )

    async def replay_by_filter(
        self,
        start_time: str,
        end_time: str,
        limit: int,
        *,
        endpoint_key: str | None = None,
        sequence_id: str | None = None,
        timeout: float | None = None,
    ) -> BulkActionResult:
        return await self._do(
            "/v1/hooks/bulk/replay",
            _build_bulk_body_by_filter(
                start_time, end_time, limit,
                endpoint_key=endpoint_key, sequence_id=sequence_id,
            ),
            timeout=timeout,
        )

    async def cancel(
        self, hook_ids: list[str], *, timeout: float | None = None,
    ) -> BulkActionResult:
        return await self._do(
            "/v1/hooks/bulk/cancel", _build_bulk_body_by_ids(hook_ids),
            timeout=timeout,
        )

    async def cancel_by_filter(
        self,
        start_time: str,
        end_time: str,
        limit: int,
        *,
        endpoint_key: str | None = None,
        sequence_id: str | None = None,
        timeout: float | None = None,
    ) -> BulkActionResult:
        return await self._do(
            "/v1/hooks/bulk/cancel",
            _build_bulk_body_by_filter(
                start_time, end_time, limit,
                endpoint_key=endpoint_key, sequence_id=sequence_id,
            ),
            timeout=timeout,
        )


class AsyncHooksService:
    """Asynchronous resource for managing hooks."""

    def __init__(self, http: AsyncHttpClient) -> None:
        self._http = http
        self.bulk = AsyncBulkActions(http)

    async def schedule(
        self,
        path: str,
        *,
        data: Any = None,
        post_at: datetime | str | None = None,
        post_at_local: str | None = None,
        timezone: str | None = None,
        post_in: str | None = None,
        retry_override: HookRetryOverride | None = None,
        timeout: float | None = None,
    ) -> Hook:
        body = _build_schedule_body(
            path,
            data=data,
            post_at=post_at,
            post_at_local=post_at_local,
            timezone_str=timezone,
            post_in=post_in,
            retry_override=retry_override,
        )
        resp_data, headers = await self._http.request_data(
            "POST", "/v1/hooks", json=body, timeout=timeout,
        )
        hook = Hook.from_dict(resp_data)
        hook.quota = _parse_quota(headers)
        return hook

    async def get(self, id: str, *, timeout: float | None = None) -> Hook:
        if not id:
            raise ValueError("hook id is required")
        data, _ = await self._http.request_data(
            "GET", f"/v1/hooks/{quote(id, safe='')}", timeout=timeout,
        )
        return Hook.from_dict(data)

    async def list(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        post_at_before: str | None = None,
        post_at_after: str | None = None,
        created_at_before: str | None = None,
        created_at_after: str | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        timeout: float | None = None,
    ) -> list[Hook]:
        params = _build_list_params(
            status=status,
            limit=limit,
            offset=offset,
            post_at_before=post_at_before,
            post_at_after=post_at_after,
            created_at_before=created_at_before,
            created_at_after=created_at_after,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        raw_list, _ = await self._http.request_data(
            "GET", "/v1/hooks", params=params, timeout=timeout,
        )
        return [Hook.from_dict(h) for h in (raw_list or [])]

    async def list_all(
        self,
        *,
        status: str | None = None,
        post_at_after: str | None = None,
        page_size: int = 100,
        timeout: float | None = None,
    ) -> AsyncIterator[Hook]:
        cursor: str | None = post_at_after
        while True:
            hooks = await self.list(
                status=status,
                limit=page_size,
                sort_by="postAt",
                sort_order="ASC",
                post_at_after=cursor,
                timeout=timeout,
            )
            for hook in hooks:
                yield hook
            if len(hooks) < page_size:
                break
            cursor = hooks[-1].post_at.astimezone(timezone.utc).isoformat()

    async def delete(self, id: str, *, timeout: float | None = None) -> None:
        if not id:
            raise ValueError("hook id is required")
        try:
            await self._http.request_data(
                "DELETE", f"/v1/hooks/{quote(id, safe='')}", timeout=timeout,
            )
        except NotFoundError:
            pass

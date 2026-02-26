from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from conftest import (
    HOOK_FIXTURE,
    QUOTA_HEADERS,
    json_response,
    make_async_client,
    make_client,
    make_mock_transport,
)

from posthook._models import HookRetryOverride


class TestSchedule:
    def test_schedule_with_post_in(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            import json
            body = json.loads(request.content)
            assert body["path"] == "/webhooks/test"
            assert body["postIn"] == "5m"
            assert body["data"] == {"userId": "123"}
            return json_response(
                {"data": HOOK_FIXTURE, "error": ""},
                headers=QUOTA_HEADERS,
            )

        client = make_client(make_mock_transport(handler))
        hook = client.hooks.schedule(
            "/webhooks/test", post_in="5m", data={"userId": "123"}
        )
        assert hook.id == "hook-uuid-123"
        assert hook.status == "pending"
        assert hook.path == "/webhooks/test"
        assert hook.quota is not None
        assert hook.quota.limit == 10000
        assert hook.quota.usage == 500
        assert hook.quota.remaining == 9500
        client.close()

    def test_schedule_with_post_at_string(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            import json
            body = json.loads(request.content)
            assert body["postAt"] == "2026-03-01T12:00:00Z"
            return json_response({"data": HOOK_FIXTURE, "error": ""})

        client = make_client(make_mock_transport(handler))
        hook = client.hooks.schedule(
            "/webhooks/test", post_at="2026-03-01T12:00:00Z"
        )
        assert hook.id == "hook-uuid-123"
        client.close()

    def test_schedule_with_post_at_datetime(self) -> None:
        dt = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            body = json.loads(request.content)
            assert body["postAt"] == "2026-03-01T12:00:00Z"
            return json_response({"data": HOOK_FIXTURE, "error": ""})

        client = make_client(make_mock_transport(handler))
        hook = client.hooks.schedule("/webhooks/test", post_at=dt)
        assert hook.id == "hook-uuid-123"
        client.close()

    def test_schedule_with_local_time(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            import json
            body = json.loads(request.content)
            assert body["postAtLocal"] == "2026-03-01T09:00:00"
            assert body["timezone"] == "America/New_York"
            return json_response({"data": HOOK_FIXTURE, "error": ""})

        client = make_client(make_mock_transport(handler))
        hook = client.hooks.schedule(
            "/webhooks/test",
            post_at_local="2026-03-01T09:00:00",
            timezone="America/New_York",
        )
        assert hook.id == "hook-uuid-123"
        client.close()

    def test_schedule_with_retry_override(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            import json
            body = json.loads(request.content)
            assert body["retryOverride"]["minRetries"] == 5
            assert body["retryOverride"]["strategy"] == "exponential"
            return json_response({"data": HOOK_FIXTURE, "error": ""})

        client = make_client(make_mock_transport(handler))
        hook = client.hooks.schedule(
            "/webhooks/test",
            post_in="5m",
            retry_override=HookRetryOverride(
                min_retries=5,
                delay_secs=10,
                strategy="exponential",
                backoff_factor=2.0,
                max_delay_secs=3600,
                jitter=True,
            ),
        )
        assert hook.id == "hook-uuid-123"
        client.close()

    def test_quota_resets_at_parsed_as_datetime(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(
                {"data": HOOK_FIXTURE, "error": ""},
                headers=QUOTA_HEADERS,
            )

        client = make_client(make_mock_transport(handler))
        hook = client.hooks.schedule("/webhooks/test", post_in="5m")
        assert hook.quota is not None
        assert isinstance(hook.quota.resets_at, datetime)
        assert hook.quota.resets_at == datetime(2026, 3, 1, tzinfo=timezone.utc)
        client.close()

    def test_schedule_no_quota_headers(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({"data": HOOK_FIXTURE, "error": ""})

        client = make_client(make_mock_transport(handler))
        hook = client.hooks.schedule("/webhooks/test", post_in="5m")
        assert hook.quota is None
        client.close()


class TestGet:
    def test_get_hook(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/v1/hooks/hook-uuid-123" in str(request.url)
            return json_response({"data": HOOK_FIXTURE, "error": ""})

        client = make_client(make_mock_transport(handler))
        hook = client.hooks.get("hook-uuid-123")
        assert hook.id == "hook-uuid-123"
        assert hook.data == {"userId": "123"}
        client.close()


class TestList:
    def test_list_hooks(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "status=failed" in str(request.url)
            assert "limit=50" in str(request.url)
            return json_response({
                "data": [HOOK_FIXTURE],
                "error": "",
            })

        client = make_client(make_mock_transport(handler))
        hooks = client.hooks.list(status="failed", limit=50)
        assert len(hooks) == 1
        assert hooks[0].id == "hook-uuid-123"
        client.close()

    def test_list_empty(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({
                "data": [],
                "error": "",
            })

        client = make_client(make_mock_transport(handler))
        hooks = client.hooks.list()
        assert len(hooks) == 0
        client.close()


class TestListAll:
    def test_list_all_cursor_pagination(self) -> None:
        call_count = 0
        captured_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            captured_urls.append(str(request.url))
            call_count += 1
            if call_count == 1:
                return json_response({
                    "data": [HOOK_FIXTURE, HOOK_FIXTURE],
                    "error": "",
                })
            else:
                modified = {**HOOK_FIXTURE, "id": "hook-uuid-456"}
                return json_response({
                    "data": [modified],
                    "error": "",
                })

        client = make_client(make_mock_transport(handler))
        hooks = list(client.hooks.list_all(status="failed", page_size=2))
        assert len(hooks) == 3
        assert hooks[0].id == "hook-uuid-123"
        assert hooks[2].id == "hook-uuid-456"
        assert call_count == 2

        # First request: sortBy=postAt, sortOrder=ASC, no cursor
        assert "sortBy=postAt" in captured_urls[0]
        assert "sortOrder=ASC" in captured_urls[0]

        # Second request: cursor from last hook's postAt
        assert "postAtAfter=2026-03-01" in captured_urls[1]
        client.close()

    def test_list_all_with_start_cursor(self) -> None:
        captured_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_urls.append(str(request.url))
            return json_response({"data": [], "error": ""})

        client = make_client(make_mock_transport(handler))
        list(client.hooks.list_all(post_at_after="2026-01-01T00:00:00Z"))
        assert "postAtAfter=2026-01-01" in captured_urls[0]
        client.close()


class TestDelete:
    def test_delete_hook(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "DELETE"
            return json_response({"data": {}, "error": ""})

        client = make_client(make_mock_transport(handler))
        client.hooks.delete("hook-uuid-123")  # Should not raise
        client.close()

    def test_delete_idempotent_404(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({"error": "not found"}, status_code=404)

        client = make_client(make_mock_transport(handler))
        client.hooks.delete("hook-uuid-123")  # Should swallow 404
        client.close()


class TestBulkActions:
    def test_bulk_retry(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            import json
            body = json.loads(request.content)
            assert body["hookIDs"] == ["id-1", "id-2"]
            assert "projectID" not in body
            return json_response({
                "data": {"affected": 2},
                "error": "",
            })

        client = make_client(make_mock_transport(handler))
        result = client.hooks.bulk.retry(["id-1", "id-2"])
        assert result.affected == 2
        client.close()

    def test_bulk_cancel_by_filter(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            import json
            body = json.loads(request.content)
            assert "projectID" not in body
            assert body["startTime"] == "2026-01-01T00:00:00Z"
            assert body["endTime"] == "2026-02-01T00:00:00Z"
            assert body["limit"] == 100
            return json_response({
                "data": {"affected": 50},
                "error": "",
            })

        client = make_client(make_mock_transport(handler))
        result = client.hooks.bulk.cancel_by_filter(
            "2026-01-01T00:00:00Z",
            "2026-02-01T00:00:00Z",
            100,
        )
        assert result.affected == 50
        client.close()

    def test_bulk_replay(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/v1/hooks/bulk/replay" in str(request.url)
            return json_response({
                "data": {"affected": 3},
                "error": "",
            })

        client = make_client(make_mock_transport(handler))
        result = client.hooks.bulk.replay(["id-1", "id-2", "id-3"])
        assert result.affected == 3
        client.close()


class TestAsyncHooks:
    @pytest.mark.asyncio
    async def test_schedule(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(
                {"data": HOOK_FIXTURE, "error": ""},
                headers=QUOTA_HEADERS,
            )

        client = make_async_client(make_mock_transport(handler))
        hook = await client.hooks.schedule("/webhooks/test", post_in="5m")
        assert hook.id == "hook-uuid-123"
        assert hook.quota is not None
        assert hook.quota.remaining == 9500
        await client.close()

    @pytest.mark.asyncio
    async def test_list_all(self) -> None:
        call_count = 0
        captured_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            captured_urls.append(str(request.url))
            call_count += 1
            if call_count == 1:
                return json_response({
                    "data": [HOOK_FIXTURE, HOOK_FIXTURE],
                    "error": "",
                })
            else:
                modified = {**HOOK_FIXTURE, "id": "hook-uuid-456"}
                return json_response({
                    "data": [modified],
                    "error": "",
                })

        client = make_async_client(make_mock_transport(handler))
        hooks = []
        async for hook in client.hooks.list_all(page_size=2):
            hooks.append(hook)
        assert len(hooks) == 3
        assert call_count == 2

        # Verify cursor pagination
        assert "sortBy=postAt" in captured_urls[0]
        assert "postAtAfter=2026-03-01" in captured_urls[1]
        await client.close()

    @pytest.mark.asyncio
    async def test_delete_swallows_404(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({"error": "not found"}, status_code=404)

        client = make_async_client(make_mock_transport(handler))
        await client.hooks.delete("hook-uuid-123")  # Should not raise
        await client.close()


class TestEmptyID:
    def test_get_empty_id(self) -> None:
        transport = make_mock_transport(
            lambda req: json_response({"data": HOOK_FIXTURE, "error": ""})
        )
        client = make_client(transport)
        with pytest.raises(ValueError, match="hook id is required"):
            client.hooks.get("")
        client.close()

    def test_delete_empty_id(self) -> None:
        transport = make_mock_transport(
            lambda req: json_response({"data": {}, "error": ""})
        )
        client = make_client(transport)
        with pytest.raises(ValueError, match="hook id is required"):
            client.hooks.delete("")
        client.close()


class TestURLEscape:
    def test_get_escapes_id(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/v1/hooks/id%2Fwith%2Fslashes" in str(request.url)
            return json_response({"data": HOOK_FIXTURE, "error": ""})

        client = make_client(make_mock_transport(handler))
        client.hooks.get("id/with/slashes")
        client.close()

    def test_delete_escapes_id(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/v1/hooks/id%2Fwith%2Fslashes" in str(request.url)
            return json_response({"data": {}, "error": ""})

        client = make_client(make_mock_transport(handler))
        client.hooks.delete("id/with/slashes")
        client.close()


class TestNaiveDatetimeRejection:
    def test_naive_datetime_raises(self) -> None:
        transport = make_mock_transport(
            lambda req: json_response({"data": HOOK_FIXTURE, "error": ""})
        )
        client = make_client(transport)
        naive_dt = datetime(2026, 3, 1, 12, 0, 0)  # no tzinfo
        with pytest.raises(ValueError, match="timezone-aware"):
            client.hooks.schedule("/webhooks/test", post_at=naive_dt)
        client.close()


class TestScheduleValidation:
    def test_no_scheduling_mode(self) -> None:
        transport = make_mock_transport(
            lambda req: json_response({"data": HOOK_FIXTURE, "error": ""})
        )
        client = make_client(transport)
        with pytest.raises(ValueError, match="Exactly one scheduling mode"):
            client.hooks.schedule("/webhooks/test", data={"key": "val"})
        client.close()

    def test_multiple_scheduling_modes(self) -> None:
        transport = make_mock_transport(
            lambda req: json_response({"data": HOOK_FIXTURE, "error": ""})
        )
        client = make_client(transport)
        with pytest.raises(ValueError, match="Only one scheduling mode"):
            client.hooks.schedule(
                "/webhooks/test", post_in="5m", post_at="2026-03-01T12:00:00Z"
            )
        client.close()

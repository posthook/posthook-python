from __future__ import annotations

import os
from unittest import mock

import httpx
import pytest
from conftest import json_response, make_client, make_mock_transport

import posthook


class TestClientInit:
    def test_api_key_required(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            # Remove POSTHOOK_API_KEY if set
            os.environ.pop("POSTHOOK_API_KEY", None)
            with pytest.raises(posthook.AuthenticationError, match="No API key"):
                posthook.Posthook()

    def test_api_key_from_param(self) -> None:
        transport = make_mock_transport(
            lambda req: json_response({"data": {}, "error": ""})
        )
        client = make_client(transport)
        assert client is not None
        client.close()

    def test_api_key_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"POSTHOOK_API_KEY": "pk_from_env"}):
            transport = make_mock_transport(
                lambda req: json_response({"data": {}, "error": ""})
            )
            http_client = httpx.Client(transport=transport)
            client = posthook.Posthook(http_client=http_client)
            assert client is not None
            client.close()

    def test_signing_key_from_param(self) -> None:
        transport = make_mock_transport(
            lambda req: json_response({"data": {}, "error": ""})
        )
        client = make_client(transport, signing_key="ph_sk_test")
        assert client.signatures._signing_key == "ph_sk_test"
        client.close()

    def test_signing_key_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"POSTHOOK_SIGNING_KEY": "ph_sk_env"}):
            transport = make_mock_transport(
                lambda req: json_response({"data": {}, "error": ""})
            )
            client = make_client(transport)
            assert client.signatures._signing_key == "ph_sk_env"
            client.close()

    def test_context_manager(self) -> None:
        transport = make_mock_transport(
            lambda req: json_response({"data": {}, "error": ""})
        )
        with make_client(transport) as client:
            assert client is not None

    def test_has_services(self) -> None:
        transport = make_mock_transport(
            lambda req: json_response({"data": {}, "error": ""})
        )
        client = make_client(transport)
        assert hasattr(client, "hooks")
        assert hasattr(client, "signatures")
        client.close()


class TestAsyncClientInit:
    def test_api_key_required(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("POSTHOOK_API_KEY", None)
            with pytest.raises(posthook.AuthenticationError, match="No API key"):
                posthook.AsyncPosthook()

    @pytest.mark.asyncio
    async def test_async_context_manager(self) -> None:
        transport = make_mock_transport(
            lambda req: json_response({"data": {}, "error": ""})
        )
        http_client = httpx.AsyncClient(transport=transport)
        async with posthook.AsyncPosthook("pk_test", http_client=http_client) as client:
            assert client is not None

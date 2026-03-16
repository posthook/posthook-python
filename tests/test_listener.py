"""Tests for WebSocket listener and stream."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import websockets.asyncio.server

import posthook
from posthook import ConnectionInfo, Listener, Result, Stream, WebSocketError
from posthook._listener import (
    _fetch_ticket,
    _hook_to_delivery,
    _reconnect_delay,
)
from posthook._models import ForwardRequest, WebSocketMeta


# ---- Fixtures ----


CONNECTED_MSG = {
    "type": "connected",
    "connectionId": "conn_test",
    "projectId": "proj_test",
    "projectName": "Test Project",
    "serverTime": "2026-01-01T00:00:00Z",
}

HOOK_MSG: dict[str, Any] = {
    "type": "hook",
    "id": "hook-001",
    "path": "/webhooks/test",
    "data": {"userId": "abc"},
    "postAt": "2026-03-01T12:00:00Z",
    "postedAt": "2026-03-01T12:00:01Z",
    "createdAt": "2026-02-23T10:00:00Z",
    "updatedAt": "2026-02-23T10:00:00Z",
    "timestamp": 1740828000,
    "attempt": 1,
    "maxAttempts": 5,
    "ackUrl": "https://api.posthook.io/ack/tok",
    "nackUrl": "https://api.posthook.io/nack/tok",
}

HOOK_MSG_WITH_FORWARD: dict[str, Any] = {
    **HOOK_MSG,
    "id": "hook-002",
    "forwardRequest": {
        "body": '{"key":"val"}',
        "signature": "v1,abc123",
        "authorization": "Bearer tok",
        "posthookId": "hook-002",
        "posthookTimestamp": "1740828000",
        "posthookSignature": "v1,abc123",
    },
}


# ---- Result tests ----


class TestResult:
    def test_ack(self) -> None:
        r = Result.ack()
        assert r.kind == "ack"
        assert r.timeout == 0
        assert r.error is None

    def test_accept(self) -> None:
        r = Result.accept(30)
        assert r.kind == "accept"
        assert r.timeout == 30
        assert r.error is None

    def test_nack_no_error(self) -> None:
        r = Result.nack()
        assert r.kind == "nack"
        assert r.error is None

    def test_nack_with_string(self) -> None:
        r = Result.nack("something went wrong")
        assert r.kind == "nack"
        assert isinstance(r.error, Exception)
        assert str(r.error) == "something went wrong"

    def test_nack_with_exception(self) -> None:
        err = ValueError("bad value")
        r = Result.nack(err)
        assert r.kind == "nack"
        assert r.error is err

    def test_repr(self) -> None:
        assert "ack" in repr(Result.ack())
        assert "timeout=30" in repr(Result.accept(30))
        assert "nack" in repr(Result.nack("err"))


# ---- ConnectionInfo tests ----


class TestConnectionInfo:
    def test_fields(self) -> None:
        info = ConnectionInfo(
            connection_id="conn_1",
            project_id="proj_1",
            project_name="My Project",
        )
        assert info.connection_id == "conn_1"
        assert info.project_id == "proj_1"
        assert info.project_name == "My Project"

    def test_frozen(self) -> None:
        info = ConnectionInfo("c", "p", "n")
        with pytest.raises(AttributeError):
            info.connection_id = "other"  # type: ignore[misc]


# ---- Helper function tests ----


class TestHookToDelivery:
    def test_basic_conversion(self) -> None:
        delivery = _hook_to_delivery(HOOK_MSG)
        assert delivery.hook_id == "hook-001"
        assert delivery.path == "/webhooks/test"
        assert delivery.data == {"userId": "abc"}
        assert delivery.timestamp == 1740828000
        assert delivery.body == b""
        assert delivery.ack_url == "https://api.posthook.io/ack/tok"
        assert delivery.nack_url == "https://api.posthook.io/nack/tok"
        assert delivery.ws is not None
        assert delivery.ws.attempt == 1
        assert delivery.ws.max_attempts == 5
        assert delivery.ws.forward_request is None

    def test_with_forward_request(self) -> None:
        delivery = _hook_to_delivery(HOOK_MSG_WITH_FORWARD)
        assert delivery.hook_id == "hook-002"
        assert delivery.ws is not None
        fwd = delivery.ws.forward_request
        assert fwd is not None
        assert fwd.body == '{"key":"val"}'
        assert fwd.signature == "v1,abc123"
        assert fwd.authorization == "Bearer tok"
        assert fwd.posthook_id == "hook-002"
        assert fwd.posthook_timestamp == "1740828000"
        assert fwd.posthook_signature == "v1,abc123"

    def test_missing_optional_fields(self) -> None:
        minimal = {
            "type": "hook",
            "id": "h1",
            "path": "/test",
            "createdAt": "2026-01-01T00:00:00Z",
            "attempt": 1,
            "maxAttempts": 3,
        }
        delivery = _hook_to_delivery(minimal)
        assert delivery.hook_id == "h1"
        assert delivery.data is None
        assert delivery.timestamp == 0


class TestReconnectDelay:
    def test_exponential_backoff(self) -> None:
        assert _reconnect_delay(0) == 1.0
        assert _reconnect_delay(1) == 2.0
        assert _reconnect_delay(2) == 4.0
        assert _reconnect_delay(3) == 8.0

    def test_capped_at_30(self) -> None:
        assert _reconnect_delay(5) == 30.0
        assert _reconnect_delay(10) == 30.0


# ---- WebSocket server test fixtures ----


async def _make_ws_server(
    handler,
) -> tuple[websockets.asyncio.server.Server, int]:
    """Start a local WebSocket server and return (server, port)."""
    server = await websockets.asyncio.server.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


class _TicketServer:
    """A minimal HTTP server that returns ticket responses."""

    def __init__(self, ws_port: int) -> None:
        self.ws_port = ws_port
        self._app = None
        self._server = None
        self.port = 0

    async def start(self) -> None:
        import http.server
        import threading

        ws_port = self.ws_port

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                resp = json.dumps({
                    "data": {
                        "ticket": "test-ticket",
                        "url": f"ws://127.0.0.1:{ws_port}",
                        "expiresAt": "2099-01-01T00:00:00Z",
                    }
                })
                self.wfile.write(resp.encode())

            def log_message(self, format: str, *args: Any) -> None:
                pass  # Suppress logs

        self._server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        if self._server:
            self._server.shutdown()


# ---- Listener tests ----


class TestListener:
    @pytest.mark.asyncio
    async def test_hook_delivery_and_ack(self) -> None:
        """Listener receives a hook, handler acks it, and the ack message is sent."""
        received: list[Any] = []
        ack_received = asyncio.Event()

        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            await ws.send(json.dumps(HOOK_MSG))
            # Wait for ack
            raw = await ws.recv()
            msg = json.loads(raw)
            received.append(msg)
            ack_received.set()
            # Keep alive until closed
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        async def handler(delivery: posthook.Delivery) -> Result:
            assert delivery.hook_id == "hook-001"
            assert delivery.path == "/webhooks/test"
            assert delivery.ws is not None
            return Result.ack()

        try:
            listener = Listener(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
                handler=handler,
            )
            await listener.start()

            await asyncio.wait_for(ack_received.wait(), timeout=5.0)
            assert len(received) == 1
            assert received[0]["type"] == "ack"
            assert received[0]["hookId"] == "hook-001"

            await listener.close()
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_handler_returns_accept(self) -> None:
        """Handler returning Result.accept() sends accept message."""
        received: list[Any] = []
        accept_received = asyncio.Event()

        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            await ws.send(json.dumps(HOOK_MSG))
            raw = await ws.recv()
            received.append(json.loads(raw))
            accept_received.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        async def handler(delivery: posthook.Delivery) -> Result:
            return Result.accept(timeout=60)

        try:
            listener = Listener(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
                handler=handler,
            )
            await listener.start()
            await asyncio.wait_for(accept_received.wait(), timeout=5.0)

            assert received[0]["type"] == "accept"
            assert received[0]["hookId"] == "hook-001"
            assert received[0]["timeout"] == 60

            await listener.close()
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_handler_exception_sends_nack(self) -> None:
        """When the handler raises, listener sends a nack."""
        received: list[Any] = []
        nack_received = asyncio.Event()

        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            await ws.send(json.dumps(HOOK_MSG))
            raw = await ws.recv()
            received.append(json.loads(raw))
            nack_received.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        async def handler(delivery: posthook.Delivery) -> Result:
            raise RuntimeError("processing failed")

        try:
            listener = Listener(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
                handler=handler,
            )
            await listener.start()
            await asyncio.wait_for(nack_received.wait(), timeout=5.0)

            assert received[0]["type"] == "nack"
            assert received[0]["hookId"] == "hook-001"
            assert "processing failed" in received[0]["error"]

            await listener.close()
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_on_connected_callback(self) -> None:
        """on_connected callback is called with ConnectionInfo."""
        connected_info: list[ConnectionInfo] = []

        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        async def handler(delivery: posthook.Delivery) -> Result:
            return Result.ack()

        try:
            listener = Listener(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
                handler=handler,
                on_connected=lambda info: connected_info.append(info),
            )
            await listener.start()

            assert len(connected_info) == 1
            assert connected_info[0].connection_id == "conn_test"
            assert connected_info[0].project_id == "proj_test"
            assert connected_info[0].project_name == "Test Project"

            await listener.close()
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_close_and_wait(self) -> None:
        """close() stops the listener and wait() resolves."""
        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        async def handler(delivery: posthook.Delivery) -> Result:
            return Result.ack()

        try:
            listener = Listener(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
                handler=handler,
            )
            await listener.start()
            await listener.close()
            # wait() should return immediately after close
            await asyncio.wait_for(listener.wait(), timeout=2.0)
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_ping_pong(self) -> None:
        """Listener responds to server ping messages with pong."""
        pong_received = asyncio.Event()

        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            # Send application-level ping
            await ws.send(json.dumps({"type": "ping", "timestamp": "2026-01-01T00:00:00Z"}))
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("type") == "pong":
                pong_received.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        async def handler(delivery: posthook.Delivery) -> Result:
            return Result.ack()

        try:
            listener = Listener(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
                handler=handler,
            )
            await listener.start()
            await asyncio.wait_for(pong_received.wait(), timeout=5.0)
            await listener.close()
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_nacks_overflow_at_capacity(self) -> None:
        """When at max_concurrency, overflow hooks are nacked immediately."""
        handler_calls: list[str] = []
        received_messages: list[dict[str, Any]] = []
        all_msgs = asyncio.Event()

        hook_b: dict[str, Any] = {**HOOK_MSG, "id": "hook-b", "path": "/b"}

        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            await ws.send(json.dumps(HOOK_MSG))
            await asyncio.sleep(0.05)
            await ws.send(json.dumps(hook_b))
            # Collect 2 messages: 1 nack (overflow) + 1 ack (handled)
            for _ in range(2):
                raw = await ws.recv()
                received_messages.append(json.loads(raw))
            all_msgs.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        async def handler(delivery: posthook.Delivery) -> Result:
            handler_calls.append(delivery.hook_id)
            await asyncio.sleep(0.3)
            return Result.ack()

        try:
            listener = Listener(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
                handler=handler,
                max_concurrency=1,
            )
            await listener.start()
            await asyncio.wait_for(all_msgs.wait(), timeout=5.0)

            # Only hook-001 was handled; hook-b was nacked at capacity
            assert handler_calls == ["hook-001"]
            assert len(received_messages) == 2
            nacks = [m for m in received_messages if m["type"] == "nack"]
            acks = [m for m in received_messages if m["type"] == "ack"]
            assert len(nacks) == 1
            assert nacks[0]["hookId"] == "hook-b"
            assert len(acks) == 1
            assert acks[0]["hookId"] == "hook-001"

            await listener.close()
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_retries_on_pre_connected_close(self) -> None:
        """Close before connected message triggers retry; second connection succeeds."""
        connection_count = 0
        connected_info: list[ConnectionInfo] = []

        async def ws_handler(ws: Any) -> None:
            nonlocal connection_count
            connection_count += 1
            if connection_count == 1:
                # Close immediately without sending connected
                await ws.close(1006)
                return
            # Second connection succeeds
            await ws.send(json.dumps(CONNECTED_MSG))
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        async def handler(delivery: posthook.Delivery) -> Result:
            return Result.ack()

        try:
            listener = Listener(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
                handler=handler,
                on_connected=lambda info: connected_info.append(info),
            )
            await asyncio.wait_for(listener.start(), timeout=10.0)

            assert connection_count == 2
            assert len(connected_info) == 1
            assert connected_info[0].connection_id == "conn_test"

            await listener.close()
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_rejects_on_pre_connected_auth_close(self) -> None:
        """Auth close code (4001) before connected message raises immediately, no retry."""
        connection_count = 0

        async def ws_handler(ws: Any) -> None:
            nonlocal connection_count
            connection_count += 1
            # Close with auth code, no connected message
            await ws.close(4001, "Unauthorized")

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        async def handler(delivery: posthook.Delivery) -> Result:
            return Result.ack()

        try:
            listener = Listener(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
                handler=handler,
            )
            with pytest.raises(WebSocketError, match="Authentication"):
                await asyncio.wait_for(listener.start(), timeout=5.0)

            # Only one connection attempt -- no retry
            assert connection_count == 1

            await listener.close()
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_reconnects_on_disconnect(self) -> None:
        """After a successful connection, a non-auth close triggers reconnection."""
        connection_count = 0
        disconnected_calls: list[Any] = []
        reconnecting_calls: list[int] = []
        second_connected = asyncio.Event()

        async def ws_handler(ws: Any) -> None:
            nonlocal connection_count
            connection_count += 1
            await ws.send(json.dumps(CONNECTED_MSG))
            if connection_count == 1:
                await asyncio.sleep(0.05)
                await ws.close(1001, "going away")
                return
            # Second connection stays alive
            second_connected.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        async def handler(delivery: posthook.Delivery) -> Result:
            return Result.ack()

        try:
            listener = Listener(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
                handler=handler,
                on_disconnected=lambda err: disconnected_calls.append(err),
                on_reconnecting=lambda attempt: reconnecting_calls.append(attempt),
            )
            await listener.start()
            await asyncio.wait_for(second_connected.wait(), timeout=10.0)

            assert len(disconnected_calls) >= 1
            assert len(reconnecting_calls) >= 1

            await listener.close()
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_handler_on_dead_connection_is_silent_noop(self) -> None:
        """Connection drops while handler is running. Handler finishes and
        calls _send_result() — the WebSocket is closed, so the result is
        silently dropped. No crash, no unhandled exception."""
        handler_started = asyncio.Event()
        handler_gate = asyncio.Event()
        handler_finished = asyncio.Event()

        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            await ws.send(json.dumps(HOOK_MSG))
            # Wait for handler to start, then kill the connection
            # (we can't synchronize directly, so use a short sleep)
            await asyncio.sleep(0.1)
            await ws.close(1001, "going away")

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        async def handler(delivery: posthook.Delivery) -> Result:
            handler_started.set()
            # Block until the gate is released (connection will be killed)
            await handler_gate.wait()
            handler_finished.set()
            return Result.ack()

        try:
            listener = Listener(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
                handler=handler,
            )
            await listener.start()

            # Wait for the handler to start processing
            await asyncio.wait_for(handler_started.wait(), timeout=5.0)

            # Let the server close the connection
            await asyncio.sleep(0.2)

            # Release the handler — it will try to send ack on the dead connection
            handler_gate.set()
            await asyncio.wait_for(handler_finished.wait(), timeout=5.0)

            # No crash, no unhandled exception — handler completed silently
            await listener.close()
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_no_reconnect_on_auth_close_code(self) -> None:
        """Auth close code (4001) after connected message does not trigger reconnection."""
        reconnecting_calls: list[int] = []
        disconnected_calls: list[Any] = []

        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            await asyncio.sleep(0.05)
            await ws.close(4001, "auth revoked")

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        async def handler(delivery: posthook.Delivery) -> Result:
            return Result.ack()

        try:
            listener = Listener(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
                handler=handler,
                on_disconnected=lambda err: disconnected_calls.append(err),
                on_reconnecting=lambda attempt: reconnecting_calls.append(attempt),
            )
            await listener.start()
            # Wait for the close to be processed
            await asyncio.wait_for(listener.wait(), timeout=5.0)

            assert len(disconnected_calls) >= 1
            assert len(reconnecting_calls) == 0

            await listener.close()
        finally:
            server.close()
            await ticket_server.stop()


# ---- Stream tests ----


class TestStream:
    @pytest.mark.asyncio
    async def test_async_for_delivery(self) -> None:
        """Stream yields deliveries via async for."""
        ack_received = asyncio.Event()
        received_msgs: list[Any] = []

        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            await ws.send(json.dumps(HOOK_MSG))
            raw = await ws.recv()
            received_msgs.append(json.loads(raw))
            ack_received.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        try:
            stream = Stream(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
            )
            await stream.start()

            # Get first delivery
            delivery = await asyncio.wait_for(stream.__anext__(), timeout=5.0)
            assert delivery.hook_id == "hook-001"
            assert delivery.path == "/webhooks/test"
            assert delivery.ws is not None

            # Explicitly ack
            await stream.ack(delivery.hook_id)
            await asyncio.wait_for(ack_received.wait(), timeout=5.0)

            assert received_msgs[0]["type"] == "ack"
            assert received_msgs[0]["hookId"] == "hook-001"

            await stream.close()
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Stream works as an async context manager."""
        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            await ws.send(json.dumps(HOOK_MSG))
            try:
                await ws.recv()
            except Exception:
                pass
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        try:
            stream = Stream(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
            )
            await stream.start()

            async with stream:
                delivery = await asyncio.wait_for(stream.__anext__(), timeout=5.0)
                assert delivery.hook_id == "hook-001"
                await stream.ack(delivery.hook_id)

            # After exiting context manager, stream should be closed
            assert stream._closed
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_nack(self) -> None:
        """Stream can nack deliveries."""
        received_msgs: list[Any] = []
        nack_received = asyncio.Event()

        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            await ws.send(json.dumps(HOOK_MSG))
            raw = await ws.recv()
            received_msgs.append(json.loads(raw))
            nack_received.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        try:
            stream = Stream(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
            )
            await stream.start()

            delivery = await asyncio.wait_for(stream.__anext__(), timeout=5.0)
            await stream.nack(delivery.hook_id, "test error")
            await asyncio.wait_for(nack_received.wait(), timeout=5.0)

            assert received_msgs[0]["type"] == "nack"
            assert received_msgs[0]["error"] == "test error"

            await stream.close()
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_accept(self) -> None:
        """Stream can accept deliveries for async processing."""
        received_msgs: list[Any] = []
        accept_received = asyncio.Event()

        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            await ws.send(json.dumps(HOOK_MSG))
            raw = await ws.recv()
            received_msgs.append(json.loads(raw))
            accept_received.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        try:
            stream = Stream(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
            )
            await stream.start()

            delivery = await asyncio.wait_for(stream.__anext__(), timeout=5.0)
            await stream.accept(delivery.hook_id, timeout=120)
            await asyncio.wait_for(accept_received.wait(), timeout=5.0)

            assert received_msgs[0]["type"] == "accept"
            assert received_msgs[0]["timeout"] == 120

            await stream.close()
        finally:
            server.close()
            await ticket_server.stop()

    @pytest.mark.asyncio
    async def test_close_terminates_iterator(self) -> None:
        """Closing the stream causes the async iterator to stop."""
        async def ws_handler(ws: Any) -> None:
            await ws.send(json.dumps(CONNECTED_MSG))
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

        server, ws_port = await _make_ws_server(ws_handler)
        ticket_server = _TicketServer(ws_port)
        await ticket_server.start()

        try:
            stream = Stream(
                api_key="pk_test",
                base_url=f"http://127.0.0.1:{ticket_server.port}",
            )
            await stream.start()

            # Close after a short delay
            async def close_later() -> None:
                await asyncio.sleep(0.1)
                await stream.close()

            asyncio.create_task(close_later())

            deliveries = []
            async for delivery in stream:
                deliveries.append(delivery)

            assert len(deliveries) == 0
        finally:
            server.close()
            await ticket_server.stop()


# ---- Model tests ----


class TestWebSocketModels:
    def test_forward_request_from_dict(self) -> None:
        data = {
            "body": '{"k":"v"}',
            "signature": "v1,sig",
            "authorization": "Bearer tok",
            "posthookId": "h1",
            "posthookTimestamp": "123",
            "posthookSignature": "v1,psig",
        }
        fwd = ForwardRequest.from_dict(data)
        assert fwd.body == '{"k":"v"}'
        assert fwd.signature == "v1,sig"
        assert fwd.authorization == "Bearer tok"
        assert fwd.posthook_id == "h1"
        assert fwd.posthook_timestamp == "123"
        assert fwd.posthook_signature == "v1,psig"

    def test_forward_request_minimal(self) -> None:
        fwd = ForwardRequest.from_dict({"body": "x", "signature": "y"})
        assert fwd.body == "x"
        assert fwd.authorization is None
        assert fwd.posthook_id is None

    def test_websocket_meta(self) -> None:
        meta = WebSocketMeta(attempt=2, max_attempts=5)
        assert meta.attempt == 2
        assert meta.max_attempts == 5
        assert meta.forward_request is None

    def test_delivery_ws_field_default_none(self) -> None:
        """Delivery.ws defaults to None for HTTP-delivered hooks."""
        from posthook._models import Delivery, _parse_dt

        d = Delivery(
            hook_id="h1",
            timestamp=0,
            path="/test",
            data=None,
            body=b"",
            post_at=_parse_dt("2026-01-01T00:00:00Z"),
            posted_at=_parse_dt(""),
            created_at=_parse_dt(""),
            updated_at=_parse_dt(""),
        )
        assert d.ws is None


# ---- Error tests ----


class TestWebSocketError:
    def test_websocket_error(self) -> None:
        err = WebSocketError("connection lost")
        assert err.code == "websocket_error"
        assert err.status_code is None
        assert str(err) == "connection lost"
        assert isinstance(err, posthook.PosthookError)

    def test_importable(self) -> None:
        assert hasattr(posthook, "WebSocketError")


# ---- Export tests ----


class TestExports:
    def test_result_importable(self) -> None:
        assert hasattr(posthook, "Result")

    def test_listener_importable(self) -> None:
        assert hasattr(posthook, "Listener")

    def test_stream_importable(self) -> None:
        assert hasattr(posthook, "Stream")

    def test_connection_info_importable(self) -> None:
        assert hasattr(posthook, "ConnectionInfo")

    def test_websocket_meta_importable(self) -> None:
        assert hasattr(posthook, "WebSocketMeta")

    def test_forward_request_importable(self) -> None:
        assert hasattr(posthook, "ForwardRequest")

    def test_websocket_error_importable(self) -> None:
        assert hasattr(posthook, "WebSocketError")

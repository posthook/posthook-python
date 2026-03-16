"""WebSocket listener and stream for real-time hook delivery."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx
import websockets
import websockets.asyncio.client

try:
    import certifi

    _ssl_context = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _ssl_context = ssl.create_default_context()

from ._errors import AuthenticationError, WebSocketError
from ._models import Delivery, ForwardRequest, WebSocketMeta, _parse_dt

logger = logging.getLogger("posthook")

# ---- Constants ----

HEARTBEAT_TIMEOUT_S = 45.0
MAX_RECONNECT_ATTEMPTS = 10
AUTH_CLOSE_CODES = {4001, 4003}


# ---- Result class ----


class Result:
    """Result of processing a webhook delivery.

    Use the static factory methods to create instances:

    - ``Result.ack()`` -- mark as delivered successfully.
    - ``Result.accept(timeout)`` -- accept for async processing.
    - ``Result.nack(error?)`` -- reject and trigger retry.
    """

    __slots__ = ("kind", "timeout", "error")

    def __init__(
        self,
        kind: str,
        timeout: int = 0,
        error: Exception | None = None,
    ) -> None:
        self.kind = kind
        self.timeout = timeout
        self.error = error

    @staticmethod
    def ack() -> Result:
        """Acknowledge the delivery as successfully processed."""
        return Result("ack")

    @staticmethod
    def accept(timeout: int) -> Result:
        """Accept the delivery for async processing.

        Args:
            timeout: Maximum seconds before the server times the hook out.
        """
        return Result("accept", timeout=timeout)

    @staticmethod
    def nack(error: Exception | str | None = None) -> Result:
        """Reject the delivery, triggering a retry.

        Args:
            error: An optional error or message describing the failure.
        """
        if isinstance(error, str):
            error = Exception(error)
        return Result("nack", error=error)

    def __repr__(self) -> str:
        if self.kind == "accept":
            return f"Result(kind={self.kind!r}, timeout={self.timeout})"
        if self.kind == "nack" and self.error:
            return f"Result(kind={self.kind!r}, error={self.error!r})"
        return f"Result(kind={self.kind!r})"


# ---- ConnectionInfo ----


@dataclass(frozen=True)
class ConnectionInfo:
    """Information about the established WebSocket connection."""

    connection_id: str
    """Unique ID for this connection."""
    project_id: str
    """The project ID this connection is authenticated for."""
    project_name: str
    """The project name."""


# ---- Helpers ----


def _hook_to_delivery(msg: dict[str, Any]) -> Delivery:
    """Convert a wire-format hook message to a Delivery."""
    fwd_raw = msg.get("forwardRequest")
    fwd = ForwardRequest.from_dict(fwd_raw) if fwd_raw else None

    return Delivery(
        hook_id=msg["id"],
        timestamp=msg.get("timestamp", 0),
        path=msg["path"],
        data=msg.get("data"),
        body=b"",
        post_at=_parse_dt(msg.get("postAt", "")),
        posted_at=_parse_dt(msg.get("postedAt", "")),
        created_at=_parse_dt(msg.get("createdAt", "")),
        updated_at=_parse_dt(msg.get("updatedAt", "")),
        ack_url=msg.get("ackUrl"),
        nack_url=msg.get("nackUrl"),
        ws=WebSocketMeta(
            attempt=msg.get("attempt", 1),
            max_attempts=msg.get("maxAttempts", 1),
            forward_request=fwd,
        ),
    )


def _reconnect_delay(attempt: int) -> float:
    """Exponential backoff: min(1 * 2^attempt, 30) seconds."""
    return min(1.0 * (2 ** attempt), 30.0)


async def _fetch_ticket(api_key: str, base_url: str) -> tuple[str, str]:
    """Fetch a WebSocket ticket from the API.

    Returns:
        A tuple of (ticket, ws_url).

    Raises:
        AuthenticationError: If the API returns 401 or 403.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/v1/ws/ticket",
            headers={"X-API-Key": api_key},
        )
        if resp.status_code in (401, 403):
            raise AuthenticationError(
                f"WebSocket ticket request failed: {resp.status_code}"
            )
        resp.raise_for_status()
        data = resp.json()["data"]
        return data["ticket"], data["url"]


# ---- Base connection ----


class _BaseConnection:
    """Shared WebSocket connection logic for Listener and Stream.

    Subclasses implement ``_on_hook()`` and ``_on_terminal()`` to customise
    behaviour when a hook message arrives and when the connection terminates.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        on_connected: Callable[[ConnectionInfo], None] | None = None,
        on_disconnected: Callable[[Exception | None], None] | None = None,
        on_reconnecting: Callable[[int], None] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_reconnecting = on_reconnecting

        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._closed = False
        self._reconnect_attempts = 0
        self._last_activity = 0.0
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._recv_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Connect to the WebSocket server.

        Resolves once the first ``connected`` message is received.
        Retries with exponential backoff on pre-connected failures.

        Raises:
            AuthenticationError: If the ticket request fails with 401/403.
            WebSocketError: If the initial connection fails with an auth close code
                or max reconnect attempts are exhausted.
        """
        while not self._closed:
            try:
                await self._connect_once()
                return
            except AuthenticationError:
                raise
            except WebSocketError as exc:
                if "Authentication" in str(exc):
                    raise
                if self._closed:
                    return
                if self._reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                    self._closed = True
                    self._on_terminal()
                    raise WebSocketError(
                        "Connection failed after max reconnect attempts"
                    )
                delay = _reconnect_delay(self._reconnect_attempts)
                self._reconnect_attempts += 1
                if self._on_reconnecting:
                    self._on_reconnecting(self._reconnect_attempts)
                await asyncio.sleep(delay)

    async def close(self) -> None:
        """Gracefully close the connection. No further reconnections will occur."""
        self._closed = True
        self._stop_heartbeat()
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None

    # ---- Abstract hooks for subclasses ----

    @abstractmethod
    def _on_hook(self, msg: dict[str, Any]) -> None:
        """Handle a hook message. Called from the recv loop."""

    @abstractmethod
    def _on_terminal(self) -> None:
        """Signal that the connection is permanently done (no more reconnects)."""

    # ---- Internal connection management ----

    async def _connect_once(self) -> None:
        ticket, ws_url = await _fetch_ticket(self._api_key, self._base_url)
        url = f"{ws_url}?ticket={ticket}"

        connected_event = asyncio.Event()
        connect_error: list[Exception] = []

        try:
            ws = await websockets.asyncio.client.connect(url, ssl=_ssl_context)
        except Exception as exc:
            raise WebSocketError(f"Failed to connect: {exc}") from exc

        self._ws = ws
        self._last_activity = time.monotonic()
        self._start_heartbeat()

        # Start receive loop in background
        self._recv_task = asyncio.create_task(
            self._recv_loop(ws, connected_event, connect_error)
        )

        # Wait for the connected message or an error
        await connected_event.wait()
        if connect_error:
            raise connect_error[0]

    async def _recv_loop(
        self,
        ws: websockets.asyncio.client.ClientConnection,
        connected_event: asyncio.Event,
        connect_error: list[Exception],
    ) -> None:
        connected = False
        try:
            async for raw in ws:
                self._last_activity = time.monotonic()
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                msg_type = msg.get("type")

                if msg_type == "connected":
                    connected = True
                    self._reconnect_attempts = 0
                    info = ConnectionInfo(
                        connection_id=msg.get("connectionId", ""),
                        project_id=msg.get("projectId", ""),
                        project_name=msg.get("projectName", ""),
                    )
                    if self._on_connected:
                        self._on_connected(info)
                    connected_event.set()

                elif msg_type == "hook":
                    self._on_hook(msg)

                elif msg_type == "ping":
                    await self._send({"type": "pong"})

                elif msg_type in ("closing", "error", "ack_timeout", "async_ack"):
                    pass

        except websockets.exceptions.ConnectionClosed as exc:
            code = exc.rcvd.code if exc.rcvd else 1006
            reason = str(exc.rcvd.reason) if exc.rcvd and exc.rcvd.reason else ""

            err = WebSocketError(
                f"WebSocket closed: {code}{' ' + reason if reason else ''}"
            )
            if self._on_disconnected:
                self._on_disconnected(err)

            self._stop_heartbeat()
            self._ws = None

            if not connected:
                if code in AUTH_CLOSE_CODES:
                    connect_error.append(
                        WebSocketError(f"Authentication failed (close code {code})")
                    )
                else:
                    connect_error.append(
                        WebSocketError(
                            f"Connection closed before handshake (close code {code})"
                        )
                    )
                connected_event.set()
                # Let start()'s retry loop handle reconnection
                return

            if self._closed:
                self._on_terminal()
                return

            if code in AUTH_CLOSE_CODES:
                self._closed = True
                self._on_terminal()
                return

            await self._schedule_reconnect()
            return

        except asyncio.CancelledError:
            return

        # Normal close (server sent close frame)
        self._stop_heartbeat()
        self._ws = None

        if self._on_disconnected:
            self._on_disconnected(None)

        if not connected:
            connect_error.append(
                WebSocketError("Connection closed before handshake")
            )
            connected_event.set()
            # Let start()'s retry loop handle reconnection
            return

        if self._closed:
            self._on_terminal()
            return

        await self._schedule_reconnect()

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws:
            try:
                await self._ws.send(json.dumps(payload))
            except Exception:
                pass

    # ---- Heartbeat ----

    def _start_heartbeat(self) -> None:
        self._stop_heartbeat()
        self._last_activity = time.monotonic()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def _stop_heartbeat(self) -> None:
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(HEARTBEAT_TIMEOUT_S)
                elapsed = time.monotonic() - self._last_activity
                if elapsed >= HEARTBEAT_TIMEOUT_S:
                    logger.debug("Heartbeat timeout, closing connection")
                    if self._ws:
                        await self._ws.close()
                    return
        except asyncio.CancelledError:
            pass

    # ---- Reconnection ----

    async def _schedule_reconnect(self) -> None:
        if self._reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
            self._closed = True
            self._on_terminal()
            return

        delay = _reconnect_delay(self._reconnect_attempts)
        self._reconnect_attempts += 1

        if self._on_reconnecting:
            self._on_reconnecting(self._reconnect_attempts)

        logger.debug("Reconnecting in %.1fs (attempt %d)", delay, self._reconnect_attempts)
        await asyncio.sleep(delay)

        if self._closed:
            return

        try:
            await self._connect_once()
        except AuthenticationError:
            self._closed = True
            self._on_terminal()
        except WebSocketError as exc:
            if "Authentication" in str(exc):
                self._closed = True
                self._on_terminal()
            else:
                # Pre-connected non-auth failure during reconnect: retry
                await self._schedule_reconnect()
        except Exception:
            await self._schedule_reconnect()


# ---- Listener ----


class Listener:
    """A long-lived WebSocket listener that dispatches incoming hook deliveries
    to a user-provided handler. Thin wrapper over :class:`Stream` — every
    delivery attempt is dispatched independently (no per-hookId dedup or local
    queuing).

    When all ``max_concurrency`` slots are busy, overflow deliveries are nacked
    immediately so the server can retry them (possibly on another listener).

    Create via ``client.hooks.listen(handler)`` rather than constructing directly.

    Args:
        api_key: Posthook API key.
        base_url: API base URL.
        handler: Async function that receives a Delivery and returns a Result.
        max_concurrency: Maximum concurrent handler invocations (default: 0, unlimited).
            Deliveries that arrive while at capacity are nacked immediately.
        on_connected: Called when the WebSocket connection is established.
        on_disconnected: Called when the connection is lost.
        on_reconnecting: Called before each reconnection attempt with the attempt number.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        handler: Callable[[Delivery], Awaitable[Result]],
        *,
        max_concurrency: int = 0,
        on_connected: Callable[[ConnectionInfo], None] | None = None,
        on_disconnected: Callable[[Exception | None], None] | None = None,
        on_reconnecting: Callable[[int], None] | None = None,
    ) -> None:
        self._stream = Stream(
            api_key,
            base_url,
            on_connected=on_connected,
            on_disconnected=on_disconnected,
            on_reconnecting=on_reconnecting,
        )
        self._handler = handler
        self._max_concurrency = max_concurrency
        self._in_flight = 0
        self._done_event = asyncio.Event()
        self._closed = False

    async def start(self) -> None:
        """Connect to the WebSocket server. Resolves once the first ``connected``
        message is received, then starts consuming deliveries in the background."""
        await self._stream.start()
        asyncio.create_task(self._consume_loop())

    async def close(self) -> None:
        """Gracefully close the connection. No further reconnections will occur."""
        self._closed = True
        await self._stream.close()
        self._done_event.set()

    async def wait(self) -> None:
        """Wait until the listener is closed (via ``close()`` or after
        exhausting reconnect attempts)."""
        if self._closed:
            return
        await self._done_event.wait()

    # ---- Internal consume loop ----

    async def _consume_loop(self) -> None:
        async for delivery in self._stream:
            if self._max_concurrency > 0 and self._in_flight >= self._max_concurrency:
                await self._stream.nack(delivery.hook_id, "At capacity")
                continue

            self._in_flight += 1
            asyncio.create_task(self._run_handler(delivery))

        # Stream ended (closed or reconnects exhausted)
        self._done_event.set()

    async def _run_handler(self, delivery: Delivery) -> None:
        try:
            result = await self._handler(delivery)
        except Exception as exc:
            result = Result.nack(str(exc))

        if result.kind == "ack":
            await self._stream.ack(delivery.hook_id)
        elif result.kind == "accept":
            await self._stream.accept(delivery.hook_id, result.timeout)
        elif result.kind == "nack":
            error_msg = str(result.error) if result.error else None
            await self._stream.nack(delivery.hook_id, error_msg)

        self._in_flight -= 1



# ---- Stream ----


class Stream(_BaseConnection):
    """An async-iterable stream of hook deliveries.

    Each iteration yields a :class:`Delivery` that must be explicitly acked,
    accepted, or nacked via the stream's ``ack()``, ``accept()``, or ``nack()``
    methods.

    Create via ``client.hooks.stream()`` rather than constructing directly.

    Example::

        async with await client.hooks.stream() as stream:
            async for delivery in stream:
                print(delivery.hook_id, delivery.data)
                await stream.ack(delivery.hook_id)

    Args:
        api_key: Posthook API key.
        base_url: API base URL.
        on_connected: Called when the WebSocket connection is established.
        on_disconnected: Called when the connection is lost.
        on_reconnecting: Called before each reconnection attempt with the attempt number.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        on_connected: Callable[[ConnectionInfo], None] | None = None,
        on_disconnected: Callable[[Exception | None], None] | None = None,
        on_reconnecting: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(
            api_key,
            base_url,
            on_connected=on_connected,
            on_disconnected=on_disconnected,
            on_reconnecting=on_reconnecting,
        )
        self._queue: asyncio.Queue[Delivery | None] = asyncio.Queue()

    def __aiter__(self) -> Stream:
        return self

    async def __anext__(self) -> Delivery:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def ack(self, hook_id: str) -> None:
        """Acknowledge a delivery as successfully processed."""
        await self._send({"type": "ack", "hookId": hook_id})

    async def accept(self, hook_id: str, timeout: int) -> None:
        """Accept a delivery for async processing.

        Args:
            hook_id: The hook ID.
            timeout: Maximum seconds before the server times the hook out.
        """
        await self._send({"type": "accept", "hookId": hook_id, "timeout": timeout})

    async def nack(self, hook_id: str, error: str | None = None) -> None:
        """Reject a delivery, triggering a retry.

        Args:
            hook_id: The hook ID.
            error: Optional error message.
        """
        payload: dict[str, Any] = {"type": "nack", "hookId": hook_id}
        if error:
            payload["error"] = error
        await self._send(payload)

    async def close(self) -> None:
        """Close the stream. The async iterator will terminate."""
        await super().close()
        self._queue.put_nowait(None)

    async def __aenter__(self) -> Stream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    # ---- _BaseConnection hooks ----

    def _on_hook(self, msg: dict[str, Any]) -> None:
        delivery = _hook_to_delivery(msg)
        self._queue.put_nowait(delivery)

    def _on_terminal(self) -> None:
        self._queue.put_nowait(None)

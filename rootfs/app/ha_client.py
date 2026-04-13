"""Home Assistant WebSocket and REST API client."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from typing import Any, Callable, Coroutine

import aiohttp

logger = logging.getLogger(__name__)

# Supervisor proxy URLs (used when SUPERVISOR_TOKEN is available)
WS_URL_SUPERVISOR = "ws://supervisor/core/websocket"
WS_URL_SUPERVISOR_ALT = "ws://supervisor/core/api/websocket"
REST_URL_SUPERVISOR = "http://supervisor/core/api"

# Direct HA Core URLs (used with Long-Lived Access Token)
WS_URL_DIRECT = "ws://homeassistant.local.hass.io:8123/api/websocket"
WS_URL_DIRECT_LOCAL = "ws://172.30.32.1:8123/api/websocket"
REST_URL_DIRECT = "http://172.30.32.1:8123/api"

StateChangeCallback = Callable[[str, dict[str, Any], dict[str, Any]], Coroutine[Any, Any, None]]


class HAClient:
    """Connects to Home Assistant via WebSocket for real-time state changes
    and REST API for service calls."""

    def __init__(self, ha_token: str = "") -> None:
        # Try tokens in order: SUPERVISOR_TOKEN → HASSIO_TOKEN → manual ha_token
        self._supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "") or os.environ.get("HASSIO_TOKEN", "")
        self._manual_token = ha_token
        self._use_supervisor = bool(self._supervisor_token)

        if self._supervisor_token:
            self._token = self._supervisor_token
            logger.info("Using SUPERVISOR_TOKEN (length=%d)", len(self._token))
        elif self._manual_token:
            self._token = self._manual_token
            logger.info("Using manual ha_token from options (length=%d)", len(self._token))
        else:
            self._token = ""
            logger.error("No auth token found! Set ha_token in add-on options.")
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._msg_id = 0
        self._callbacks: list[StateChangeCallback] = []
        self._watched_entities: set[str] = set()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._event_queue: asyncio.Queue[tuple[str, dict[str, Any], dict[str, Any]]] = (
            asyncio.Queue()
        )
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()
        self._running = False

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    @property
    def is_connected(self) -> bool:
        """Whether the HA websocket is currently connected."""
        return self._connected.is_set()

    async def wait_connected(self) -> None:
        """Wait until the Home Assistant connection is ready."""
        await self._connected.wait()

    @property
    def queued_events(self) -> int:
        """Number of queued state-change events waiting for dispatch."""
        return self._event_queue.qsize()

    def on_state_change(self, callback: StateChangeCallback) -> None:
        """Register a callback for state_changed events."""
        self._callbacks.append(callback)

    def watch_entities(self, entity_ids: set[str]) -> None:
        """Add entity IDs to the watch list."""
        self._watched_entities.update(entity_ids)

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def start(self) -> None:
        """Start the WebSocket connection with auto-reconnect."""
        self._running = True
        if self._dispatcher_task is None or self._dispatcher_task.done():
            self._dispatcher_task = asyncio.create_task(self._dispatch_loop())
        self._session = aiohttp.ClientSession()
        while self._running:
            try:
                await self._connect()
                await self._listen()
            except Exception as e:
                logger.warning("WebSocket error: %s", e)
            if self._running:
                self._connected.clear()
                logger.info("Reconnecting in 5 seconds...")
                await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the client and close connections."""
        self._running = False
        self._connected.clear()
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._dispatcher_task and not self._dispatcher_task.done():
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._event_queue.join(), timeout=5.0)
            if not self._event_queue.empty():
                logger.warning(
                    "Dropping %d queued state-change events during shutdown",
                    self._event_queue.qsize(),
                )
        if self._session and not self._session.closed:
            await self._session.close()
        if self._dispatcher_task and not self._dispatcher_task.done():
            self._dispatcher_task.cancel()
            await asyncio.gather(self._dispatcher_task, return_exceptions=True)
        self._dispatcher_task = None
        self._ws = None
        self._session = None

    async def _connect(self) -> None:
        """Establish WebSocket connection and authenticate."""
        assert self._session is not None

        # Build list of WS URLs to try based on token source
        if self._use_supervisor:
            ws_urls = [WS_URL_SUPERVISOR, WS_URL_SUPERVISOR_ALT]
        else:
            ws_urls = [WS_URL_DIRECT_LOCAL, WS_URL_DIRECT]

        # Try each WS URL
        logger.info("Connecting to HA WebSocket (supervisor=%s)...", self._use_supervisor)
        last_error: Exception | None = None
        for url in ws_urls:
            try:
                logger.info("Trying %s", url)
                self._ws = await self._session.ws_connect(url)
                logger.info("Connected to %s", url)
                break
            except Exception as e:
                last_error = e
                logger.info("Failed to connect to %s: %s", url, e)
        else:
            raise ConnectionError(f"All WS URLs failed. Last error: {last_error}")

        # Wait for auth_required
        msg = await self._ws.receive_json()
        if msg.get("type") != "auth_required":
            raise ConnectionError(f"Unexpected message: {msg}")

        # Send auth
        await self._ws.send_json({"type": "auth", "access_token": self._token})
        msg = await self._ws.receive_json()
        if msg.get("type") != "auth_ok":
            raise ConnectionError(f"Auth failed: {msg}")

        logger.info("Authenticated with HA (version %s)", msg.get("ha_version"))

        # Subscribe to state_changed events
        sub_id = self._next_id()
        await self._ws.send_json({
            "id": sub_id,
            "type": "subscribe_events",
            "event_type": "state_changed",
        })
        msg = await self._ws.receive_json()
        if not msg.get("success"):
            raise ConnectionError(f"Subscription failed: {msg}")

        logger.info("Subscribed to state_changed events")
        self._connected.set()

    @property
    def rest_url(self) -> str:
        """REST API base URL based on token source."""
        return REST_URL_SUPERVISOR if self._use_supervisor else REST_URL_DIRECT

    async def _listen(self) -> None:
        """Listen for WebSocket messages and dispatch state changes."""
        assert self._ws is not None
        async for raw_msg in self._ws:
            if raw_msg.type == aiohttp.WSMsgType.TEXT:
                msg = json.loads(raw_msg.data)
                await self._handle_message(msg)
            elif raw_msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Process incoming WebSocket message."""
        msg_type = msg.get("type")

        # Handle response to a command
        msg_id = msg.get("id")
        if msg_id and msg_id in self._pending:
            future = self._pending[msg_id]
            if not future.done():
                future.set_result(msg)
            return

        # Handle state_changed event
        if msg_type == "event":
            event = msg.get("event", {})
            if event.get("event_type") == "state_changed":
                data = event.get("data", {})
                entity_id = data.get("entity_id", "")

                if entity_id in self._watched_entities:
                    old_state = data.get("old_state", {}) or {}
                    new_state = data.get("new_state", {}) or {}
                    self._event_queue.put_nowait((entity_id, old_state, new_state))

    async def _dispatch_loop(self) -> None:
        """Serialize state-change callbacks in arrival order."""
        try:
            while True:
                entity_id, old_state, new_state = await self._event_queue.get()
                try:
                    for callback in self._callbacks:
                        await self._safe_callback(
                            callback,
                            entity_id,
                            old_state,
                            new_state,
                        )
                finally:
                    self._event_queue.task_done()
        except asyncio.CancelledError:
            pass

    @staticmethod
    async def _safe_callback(
        callback: StateChangeCallback,
        entity_id: str,
        old_state: dict[str, Any],
        new_state: dict[str, Any],
    ) -> None:
        try:
            await callback(entity_id, old_state, new_state)
        except Exception:
            logger.exception("Error in state_change callback for %s", entity_id)

    async def _send_command(self, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        """Send a command and wait for the response."""
        await self.wait_connected()
        assert self._ws is not None

        msg_id = self._next_id()
        payload["id"] = msg_id

        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future

        try:
            await self._ws.send_json(payload)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(msg_id, None)

    async def get_state(self, entity_id: str) -> dict[str, Any] | None:
        """Get current state of an entity via REST API."""
        await self.wait_connected()
        assert self._session is not None
        url = f"{self.rest_url}/states/{entity_id}"
        try:
            async with self._session.get(url, headers=self.headers) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.warning("GET %s returned %d", url, resp.status)
                return None
        except Exception:
            logger.exception("Failed to get state for %s", entity_id)
            return None

    async def get_states(self) -> list[dict[str, Any]]:
        """Get all entity states via REST API."""
        await self.wait_connected()
        assert self._session is not None
        url = f"{self.rest_url}/states"
        try:
            async with self._session.get(url, headers=self.headers) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.warning("GET %s returned %d", url, resp.status)
                return []
        except Exception:
            logger.exception("Failed to get states")
            return []

    async def render_template(self, template: str) -> str | None:
        """Render a Jinja2 template via HA REST API."""
        await self.wait_connected()
        assert self._session is not None
        url = f"{self.rest_url}/template"
        try:
            async with self._session.post(
                url, headers=self.headers, json={"template": template}
            ) as resp:
                if resp.status == 200:
                    return await resp.text()
                logger.warning("POST %s returned %d", url, resp.status)
                return None
        except Exception:
            logger.exception("Failed to render template")
            return None

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | None = None,
        data: dict[str, Any] | None = None,
        return_response: bool = False,
    ) -> dict[str, Any]:
        """Call a HA service via WebSocket.

        For services that return data (like calendar.get_events),
        set return_response=True.
        """
        payload: dict[str, Any] = {
            "type": "call_service",
            "domain": domain,
            "service": service,
        }
        if entity_id:
            payload["target"] = {"entity_id": entity_id}
        if data:
            payload["service_data"] = data
        if return_response:
            payload["return_response"] = True

        result = await self._send_command(payload)
        return result

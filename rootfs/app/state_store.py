"""Persistent state store for DTEK Telegram Bot."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_PATH = Path("/data/state.json")


class StateStore:
    """JSON-backed persistent state with async save."""

    def __init__(self, path: Path = STATE_PATH) -> None:
        self._path = path
        self._data: dict[str, Any] = {"addresses": {}}
        self._lock = asyncio.Lock()
        self._dirty = False
        self._save_task: asyncio.Task[None] | None = None

    def load(self) -> None:
        """Load state from disk. Call once at startup."""
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.info("Loaded state from %s", self._path)
            except Exception:
                logger.exception("Failed to load state, starting fresh")
                self._data = {"addresses": {}}
        else:
            logger.info("No existing state file, starting fresh")

    def get(self, address_prefix: str, key: str, default: Any = None) -> Any:
        """Get a value from the address state."""
        addr_state = self._data.get("addresses", {}).get(address_prefix, {})
        return addr_state.get(key, default)

    def set(self, address_prefix: str, key: str, value: Any) -> None:
        """Set a value in the address state and schedule save."""
        addresses = self._data.setdefault("addresses", {})
        addr_state = addresses.setdefault(address_prefix, {})
        if addr_state.get(key) == value:
            return
        addr_state[key] = value
        self._dirty = True
        self._schedule_save()

    def get_all(self, address_prefix: str) -> dict[str, Any]:
        """Get the full state dict for an address."""
        return dict(self._data.get("addresses", {}).get(address_prefix, {}))

    def _schedule_save(self) -> None:
        """Schedule a debounced save (1 second delay)."""
        if self._save_task is not None and not self._save_task.done():
            return
        self._save_task = asyncio.create_task(self._debounced_save())

    async def _debounced_save(self) -> None:
        """Wait briefly then save to avoid excessive writes."""
        await asyncio.sleep(1.0)
        await self.save()

    async def save(self) -> None:
        """Save state to disk."""
        if not self._dirty:
            return
        async with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                self._dirty = False
                logger.debug("State saved to %s", self._path)
            except Exception:
                logger.exception("Failed to save state")

    async def flush(self) -> None:
        """Force immediate save. Call before shutdown."""
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._save_task
        await self.save()

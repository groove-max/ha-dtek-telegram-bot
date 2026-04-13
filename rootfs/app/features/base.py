"""Abstract base class for all features."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from config import AddressConfig
from ha_client import HAClient
from state_store import StateStore
from telegram_service import TelegramService
from template_engine import TemplateEngine
from utils import format_datetime, now_kyiv

logger = logging.getLogger(__name__)


class Feature(ABC):
    """Base class for all monitoring features.

    Each feature:
    - Has an `enabled` flag (from config)
    - Declares which HA entities it watches
    - Reacts to state changes via `on_state_change`
    - Optionally performs periodic work via `on_tick`
    """

    name: str = "base"

    def __init__(
        self,
        address_config: AddressConfig,
        ha_client: HAClient,
        telegram: TelegramService,
        state_store: StateStore,
        templates: TemplateEngine,
    ) -> None:
        self.config = address_config
        self.ha = ha_client
        self.tg = telegram
        self.state = state_store
        self.templates = templates
        self.log = logging.getLogger(f"{__name__}.{self.name}")

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether this feature is active based on config."""
        ...

    @abstractmethod
    def get_watched_entities(self) -> list[str]:
        """Return list of HA entity_ids this feature needs to monitor."""
        ...

    @abstractmethod
    async def on_state_change(
        self, entity_id: str, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        """Handle a state change for a watched entity."""
        ...

    async def on_tick(self) -> None:
        """Periodic callback (every ~60 seconds). Override if needed."""

    async def on_start(self) -> None:
        """Called once after all features are initialized. Override if needed."""

    async def on_stop(self) -> None:
        """Called during graceful shutdown. Override if needed."""

    # ── Helpers ──

    # Map short feature names to actual dtek_monitor entity suffixes
    _SUFFIX_MAP: dict[str, str] = {
        "status": "outage_status",
        "possible_schedule": "possible_outage_schedule",
    }

    def entity(self, suffix: str) -> str:
        """Build full entity_id from address prefix and suffix.

        Example: entity("status") -> "sensor.dtek_doroga_liustdorfska_56v_outage_status"
        """
        prefix = self.config.entity_prefix
        if suffix.startswith("binary_sensor.") or suffix.startswith("sensor.") or suffix.startswith("calendar."):
            return suffix
        if suffix.startswith("_"):
            suffix = suffix[1:]
        # Remap short names to actual entity suffixes
        suffix = self._SUFFIX_MAP.get(suffix, suffix)
        # Determine domain by suffix
        if suffix == "power":
            return f"binary_sensor.{prefix}_{suffix}"
        if suffix in ("outage_schedule", "possible_outage_schedule"):
            return f"calendar.{prefix}_{suffix}"
        return f"sensor.{prefix}_{suffix}"

    def render(self, template_name: str, **extra_context: Any) -> str:
        """Render a template with common context + extra variables."""
        ctx = {
            "display_name": self.config.display_name,
            "group": self.state_get("current_group", "—"),
            "timestamp": format_datetime(now_kyiv()),
        }
        ctx.update(extra_context)
        return self.templates.render(template_name, **ctx)

    def state_get(self, key: str, default: Any = None) -> Any:
        """Get a value from persistent state for this address."""
        return self.state.get(self.config.entity_prefix, key, default)

    def state_set(self, key: str, value: Any) -> None:
        """Set a value in persistent state for this address."""
        self.state.set(self.config.entity_prefix, key, value)

    @property
    def telegram_chat_id(self) -> str | None:
        """Optional per-address Telegram chat override."""
        return self.config.telegram_chat_id or None

    async def send_message(self, text: str, **kwargs: Any) -> int | None:
        """Send a message to the configured chat for this address."""
        return await self.tg.send_message(
            text,
            chat_id=self.telegram_chat_id,
            **kwargs,
        )

    async def edit_message(self, message_id: int, text: str, **kwargs: Any) -> bool:
        """Edit a message in the configured chat for this address."""
        return await self.tg.edit_message(
            message_id,
            text,
            chat_id=self.telegram_chat_id,
            **kwargs,
        )

    async def edit_message_result(self, message_id: int, text: str, **kwargs: Any) -> str:
        """Edit a message and return the classified outcome."""
        return await self.tg.edit_message_result(
            message_id,
            text,
            chat_id=self.telegram_chat_id,
            **kwargs,
        )

    async def pin_message(self, message_id: int, **kwargs: Any) -> bool:
        """Pin a message in the configured chat for this address."""
        return await self.tg.pin_message(
            message_id,
            chat_id=self.telegram_chat_id,
            **kwargs,
        )

    async def unpin_message(self, message_id: int, **kwargs: Any) -> bool:
        """Unpin a message in the configured chat for this address."""
        return await self.tg.unpin_message(
            message_id,
            chat_id=self.telegram_chat_id,
            **kwargs,
        )

    @staticmethod
    def get_state_value(state: dict[str, Any]) -> str:
        """Extract the state value from an HA state dict."""
        return str(state.get("state", ""))

    @staticmethod
    def get_attribute(state: dict[str, Any], attr: str, default: Any = None) -> Any:
        """Extract an attribute from an HA state dict."""
        return state.get("attributes", {}).get(attr, default)

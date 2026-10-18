"""Abstract base class for all features."""

from __future__ import annotations

import logging
import hashlib
import json
from abc import ABC, abstractmethod
from typing import Any
from datetime import datetime

from config import AddressConfig
from ha_client import HAClient
from state_store import StateStore
from telegram_service import TelegramService
from template_engine import TemplateEngine
from groups import state_groups, select_events, presentation_context, display_labels
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

    # Map logical names to actual dtek_monitor entity suffixes.
    # schedule_group supports both the current primary_schedule_group entity
    # and the legacy schedule_group name used by older builds.
    _SUFFIX_ALIASES: dict[str, tuple[str, ...]] = {
        "status": ("outage_status",),
        "possible_schedule": ("possible_outage_schedule",),
        "schedule_group": ("primary_schedule_group", "schedule_group"),
    }

    @classmethod
    def _entity_domain(cls, suffix: str) -> str:
        if suffix == "power":
            return "binary_sensor"
        if suffix in ("outage_schedule", "possible_outage_schedule"):
            return "calendar"
        return "sensor"

    def entity_candidates(self, suffix: str) -> list[str]:
        """Build all supported entity_id candidates for a logical suffix."""
        prefix = self.config.entity_prefix
        if (
            suffix.startswith("binary_sensor.")
            or suffix.startswith("sensor.")
            or suffix.startswith("calendar.")
        ):
            return [suffix]
        if suffix.startswith("_"):
            suffix = suffix[1:]

        resolved_suffixes = self._SUFFIX_ALIASES.get(suffix, (suffix,))
        entity_ids: list[str] = []
        for resolved_suffix in resolved_suffixes:
            entity_ids.append(
                f"{self._entity_domain(resolved_suffix)}.{prefix}_{resolved_suffix}"
            )
        return entity_ids

    def entity(self, suffix: str) -> str:
        """Build full entity_id from address prefix and suffix.

        Example: entity("status") -> "sensor.dtek_vulytsia_prykladna_12a_outage_status"
        """
        return self.entity_candidates(suffix)[0]

    async def read_groups(self, *, require_schedule: bool = False) -> list[str]:
        for entity in self.entity_candidates("schedule_group"):
            state = await self.ha.get_state(entity)
            groups = state_groups(state)
            if groups:
                attrs = state.get("attributes", {})
                if require_schedule and "schedule_notifications_ready" in attrs:
                    if (
                        attrs["schedule_notifications_ready"] is not True
                        or not isinstance(attrs.get("schedule_snapshot_id"), str)
                        or not attrs["schedule_snapshot_id"]
                    ):
                        raise ValueError(
                            "Schedule has not been verified; notifications paused"
                        )
                self.state_set("groups_available", True)
                self.state_set("groups_snapshot", state)
                self.state_set("current_groups", groups)
                selected = getattr(self.config, "selected_group", "")
                self.state_set(
                    "current_group",
                    selected if selected in groups else ", ".join(groups),
                )
                return groups
        self.state_set("groups_available", False)
        raise ValueError("Address groups are unavailable")

    async def calendar_snapshot(self, fetch) -> list[dict]:
        before = await self.read_groups(require_schedule=True)
        revision = self._schedule_revision()
        source = self._schedule_source()
        events = await fetch()
        after = await self.read_groups(require_schedule=True)
        if before != after or revision != self._schedule_revision() or source != self._schedule_source():
            raise ValueError("Address groups changed during calendar request")
        selected = select_events(
            events, after, getattr(self.config, "selected_group", "")
        )
        self._verified_calendar = (tuple(after), revision, source)
        self.state_set(
            "calendar_snapshot",
            {
                "groups": after,
                "events": selected,
                "at": now_kyiv().isoformat(),
                "snapshot_id": revision,
            },
        )
        return selected

    def has_schedule_coverage(self, start: datetime, end: datetime, kind: str = "outage") -> bool:
        """Missing published days must not become an all-clear notification."""
        attrs = (self.state_get("groups_snapshot") or {}).get("attributes", {})
        if "schedule_coverage" not in attrs:
            return True  # Legacy integration, without the coverage contract.
        coverage = attrs["schedule_coverage"]
        if not isinstance(coverage, list):
            return False
        groups = self.state_get("current_groups", [])
        selected = getattr(self.config, "selected_group", "")
        if selected:
            if selected not in groups:
                return False
            groups = [selected]
        if not groups:
            return False
        try:
            for group in groups:
                intervals = sorted(
                    (datetime.fromisoformat(c["start"]), datetime.fromisoformat(c["end"]))
                    for c in coverage if c["group_id"] == group and c["kind"] == kind
                )
                cursor = start
                for left, right in intervals:
                    if left.tzinfo is None or right.tzinfo is None or left >= right:
                        return False
                    if left > cursor:
                        break
                    cursor = max(cursor, right)
                if cursor < end:
                    return False
        except (KeyError, TypeError, ValueError):
            return False
        return True

    def _schedule_source(self) -> tuple[str | None, str | None]:
        """Optional contract metadata; old integrations retain their original behavior."""
        attrs = (self.state_get("groups_snapshot") or {}).get("attributes", {})
        return attrs.get("provider_id"), attrs.get("address_id")

    def _schedule_revision(self) -> str | None:
        return (
            (self.state_get("groups_snapshot") or {})
            .get("attributes", {})
            .get("schedule_snapshot_id")
        )

    def render(self, template_name: str, **extra_context: Any) -> str:
        """Render a template with common context + extra variables."""
        ctx = {
            "display_name": self.config.display_name,
            "group": self.state_get("current_group", "—"),
            "groups": self.state_get("current_groups", []),
            "selected_group": getattr(self.config, "selected_group", ""),
            "timestamp": format_datetime(now_kyiv()),
        }
        ctx.update(extra_context)
        identity = {k: v for k, v in ctx.items() if k != "timestamp"}
        identity.update(
            template=template_name,
            feature=self.name,
            address=self.config.entity_prefix,
            power_last_change=self.state_get("power_last_change"),
        )
        fingerprint = hashlib.sha256(
            json.dumps(identity, sort_keys=True, default=str).encode()
        ).hexdigest()
        previous = self.state_get(f"delivery_identity_{self.name}")
        revision = self.state_get(f"delivery_revision_{self.name}", 0)
        if previous != fingerprint:
            revision += 1
            self.state_set(f"delivery_identity_{self.name}", fingerprint)
            self.state_set(f"delivery_revision_{self.name}", revision)
        self._delivery_key = f"{fingerprint}:{revision}"
        return self.templates.render(template_name, **presentation_context(ctx, self.state_get("groups_snapshot")))

    def schedule_display_labels(self) -> dict[str, str] | None:
        return display_labels(self.state_get("groups_snapshot"))

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

    @property
    def effective_telegram_chat_id(self) -> str:
        """Resolved Telegram chat ID used for this address."""
        if hasattr(self.tg, "resolve_chat_id"):
            return self.tg.resolve_chat_id(self.telegram_chat_id)
        return self.telegram_chat_id or getattr(self.tg, "default_chat_id", "")

    async def send_message(self, text: str, **kwargs: Any) -> int | None:
        """Send a message to the configured chat for this address."""
        delivery_key = kwargs.pop("delivery_key", getattr(self, "_delivery_key", None))
        delivery_context = kwargs.pop("delivery_context", {})
        context = {
            "feature": self.name,
            "address": self.config.entity_prefix,
            "state": {
                key: self.state_get(key)
                for key in (
                    "current_groups",
                    "current_group",
                    "schedule_signature",
                    "power_state",
                    "groups_snapshot",
                    "calendar_snapshot",
                    "last_outage_start",
                    "last_outage_end",
                    "last_outage_description",
                )
            },
            **delivery_context,
        }
        verified_calendar = getattr(self, "_verified_calendar", None)
        # Persist the delivery revision before Telegram can acknowledge a send.
        # Otherwise a crash before the debounced save can invent a new key.
        save = getattr(self.state, "save", None)
        if save is not None:
            await save()
        if self.name in ("schedule_change", "group_change", "upcoming_outage"):
            groups = await self.read_groups(require_schedule=True)
            revision = self._schedule_revision()
            if (
                verified_calendar is not None
                and verified_calendar != (tuple(groups), revision, self._schedule_source())
            ) or (revision is not None and verified_calendar is None):
                raise ValueError("Schedule changed before send; fetch it again")
        expires = delivery_context.get("expires")
        if expires is not None and now_kyiv().timestamp() >= expires:
            self.log.info("Notification expired while validating; skipping send")
            return None
        return await self.tg.send_message(
            text,
            delivery_key=delivery_key if self.name != "status_message" else None,
            context=context,
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

    async def edit_message_result(
        self, message_id: int, text: str, **kwargs: Any
    ) -> str:
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

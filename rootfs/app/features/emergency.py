"""Feature 2: Emergency/outage status monitoring."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from features.base import Feature
from utils import (
    OUTAGE_TYPE_UA,
    TZ_KYIV,
    format_datetime,
    format_duration,
    now_kyiv,
)

if TYPE_CHECKING:
    from features.status_message import StatusMessageFeature


class EmergencyFeature(Feature):
    """Monitors outage status changes (start, extension, type change, end).

    Watches:
      - sensor.<prefix>_status: outage type transitions
      - sensor.<prefix>_outage_start: late/corrected outage start detection
      - sensor.<prefix>_outage_end: time extension detection
      - sensor.<prefix>_outage_description: reason/type text changes

    Events detected:
      - Outage start: ok → emergency/stabilization/planned
      - Start correction: outage_start changes while status ≠ ok
      - Time extension: outage_end changes while status ≠ ok
      - Type change: status changes between non-ok types
      - Outage end: non-ok → ok
    """

    name = "emergency"
    update_batch_delay = 0.75

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._status_message: StatusMessageFeature | None = None
        self._pending_update_task: asyncio.Task[None] | None = None
        self._pending_update: dict[str, Any] = {}

    def set_status_message(self, sm: StatusMessageFeature) -> None:
        """Inject StatusMessage dependency for push updates."""
        self._status_message = sm

    @property
    def enabled(self) -> bool:
        return self.config.emergency.enabled

    def get_watched_entities(self) -> list[str]:
        return [
            self.entity("status"),
            self.entity("outage_start"),
            self.entity("outage_end"),
            self.entity("outage_description"),
        ]

    async def on_start(self) -> None:
        """Seed emergency state from current HA entities.

        This keeps outage extension/type-change handling stable across add-on
        restarts while DTEK still reports an active outage.
        """
        current_status = await self._get_current_status()
        self.state_set("last_emergency_status", current_status)

        if current_status == "ok":
            self.state_set("last_outage_start", None)
            self.state_set("last_outage_end", None)
            self.state_set("last_outage_description", None)
            self.state_set("last_emergency_context_at", None)
            self.state_set("last_emergency_snapshot_key", None)
            return

        start_raw = await self._get_entity_value("outage_start", "")
        end_raw = await self._get_entity_value("outage_end", "")
        description = await self._get_entity_value("outage_description", "")

        self.state_set("last_outage_start", start_raw or None)
        self.state_set("last_outage_end", end_raw or None)
        self.state_set("last_outage_description", description or None)
        self._mark_emergency_context()
        if start_raw:
            self.state_set("outage_start_time", start_raw)
        self.state_set(
            "last_emergency_snapshot_key",
            self._emergency_snapshot_key(
                outage_type=current_status,
                description=description,
                start_raw=start_raw,
                end_raw=end_raw,
            ),
        )

    async def on_stop(self) -> None:
        """Cancel any pending batched outage update during shutdown."""
        self._cancel_pending_update()

    async def on_state_change(
        self, entity_id: str, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        status_entity = self.entity("status")
        outage_start_entity = self.entity("outage_start")
        outage_end_entity = self.entity("outage_end")
        outage_description_entity = self.entity("outage_description")

        if entity_id == status_entity:
            await self._handle_status_change(old_state, new_state)
        elif entity_id == outage_start_entity:
            await self._handle_outage_start_change(old_state, new_state)
        elif entity_id == outage_end_entity:
            await self._handle_outage_end_change(old_state, new_state)
        elif entity_id == outage_description_entity:
            await self._handle_description_change(old_state, new_state)

    async def _handle_status_change(
        self, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        """Handle outage status transitions."""
        old_val = self.get_state_value(old_state)
        new_val = self.get_state_value(new_state)

        # Ignore unavailable/unknown
        if new_val in ("unavailable", "unknown", ""):
            return

        if old_val == new_val:
            return

        # When transitioning from unavailable, use stored state as "old"
        if old_val in ("unavailable", "unknown", ""):
            old_val = self.state_get("last_emergency_status", "ok")
            if old_val == new_val:
                # No real change
                self.state_set("last_emergency_status", new_val)
                return

        self.log.info("Status changed: %s -> %s", old_val, new_val)

        # Outage started
        if old_val == "ok" and new_val != "ok":
            await self._on_outage_start(new_val)

        # Outage ended
        elif old_val != "ok" and new_val == "ok":
            await self._on_outage_end(old_val)

        # Type changed (e.g. emergency -> stabilization)
        elif old_val != "ok" and new_val != "ok":
            await self._on_type_change(old_val, new_val)

        self.state_set("last_emergency_status", new_val)

    async def _handle_outage_start_change(
        self, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        """Handle outage_start changes while an outage is active."""
        old_val = self.get_state_value(old_state)
        new_val = self.get_state_value(new_state)
        current_status = await self._get_current_status()

        if new_val in ("unavailable", "unknown", ""):
            return
        if old_val == new_val:
            return

        if old_val in ("unavailable", "unknown", ""):
            old_val = self.state_get("last_outage_start", "")
            if old_val == new_val:
                self.state_set("last_outage_start", new_val)
                if current_status != "ok":
                    self.state_set("outage_start_time", new_val)
                return

        self.log.info("Outage start changed: %s -> %s", old_val, new_val)
        self.state_set("last_outage_start", new_val)

        if current_status == "ok":
            return

        self.state_set("outage_start_time", new_val)
        await self._send_current_outage_snapshot(current_status)

    async def _handle_outage_end_change(
        self, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        """Handle outage_end timestamp changes (time extension)."""
        old_val = self.get_state_value(old_state)
        new_val = self.get_state_value(new_state)
        current_status = await self._get_current_status()
        stored_end = self.state_get("last_outage_end", "")

        if new_val in ("unavailable", "unknown", ""):
            return
        if old_val == new_val:
            return

        if stored_end == new_val:
            self.state_set("last_outage_end", new_val)
            return

        # When coming from unavailable, use stored value as old
        if old_val in ("unavailable", "unknown", ""):
            old_val = stored_end
            if old_val == new_val:
                self.state_set("last_outage_end", new_val)
                return
            if not old_val:
                self.log.info("Outage end became available: %s", new_val)
                self.state_set("last_outage_end", new_val)
                if current_status != "ok":
                    self._queue_update(end_old=None, end_new=new_val)
                return

        # Only notify about extension if there's an active outage
        if current_status == "ok":
            self.state_set("last_outage_end", new_val)
            return

        self.log.info("Outage end changed: %s -> %s", old_val, new_val)
        self.state_set("last_outage_end", new_val)
        self._queue_update(end_old=old_val, end_new=new_val)

    async def _handle_description_change(
        self, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        """Handle outage description changes while an outage is active."""
        old_val = self.get_state_value(old_state)
        new_val = self.get_state_value(new_state)

        if new_val in ("unavailable", "unknown", ""):
            return

        if old_val in ("unavailable", "unknown", ""):
            old_val = self.state_get("last_outage_description", "")
            if old_val == new_val:
                self.state_set("last_outage_description", new_val)
                return

        if old_val == new_val:
            self.state_set("last_outage_description", new_val)
            return

        self.log.info("Outage description changed: %s -> %s", old_val, new_val)
        self.state_set("last_outage_description", new_val)

        current_status = await self._get_current_status()
        if current_status == "ok":
            return

        self._queue_update(
            reason_old=old_val or OUTAGE_TYPE_UA.get(current_status, current_status),
            reason_new=new_val,
        )

    async def _on_outage_start(self, outage_type: str) -> None:
        """Send notification about outage start."""
        self._cancel_pending_update()
        start_raw = await self._get_entity_value("outage_start", "")
        end_raw = await self._get_entity_value("outage_end", "")
        self.state_set("outage_start_time", start_raw or now_kyiv().isoformat())
        self.state_set("last_outage_start", start_raw or None)
        self.state_set("last_outage_end", end_raw)
        self._mark_emergency_context()
        await self._send_current_outage_snapshot(outage_type)

    async def _on_outage_end(self, previous_type: str) -> None:
        """Handle outage end from DTEK."""
        self._cancel_pending_update()
        outage_start_iso = self.state_get("outage_start_time")
        duration_str = "—"
        if outage_start_iso:
            try:
                start_dt = datetime.fromisoformat(outage_start_iso)
                duration_seconds = (now_kyiv() - start_dt).total_seconds()
                duration_str = format_duration(duration_seconds)
            except (ValueError, TypeError):
                pass

        # When power_presence is active, it handles restore notifications
        # via sensors — don't duplicate with DTEK-based message.
        if self.config.power.enabled:
            self.log.info(
                "DTEK outage ended (duration %s), power_presence handles restore notification",
                duration_str,
            )
        else:
            text = self.render("emergency_end", duration=duration_str)
            await self.send_message(
                text,
                disable_notification=self.config.emergency.silent,
            )

        # Clean up state
        self.state_set("outage_start_time", None)
        self.state_set("last_outage_start", None)
        self.state_set("last_outage_end", None)
        self.state_set("last_outage_description", None)
        self.state_set("last_emergency_context_at", None)
        self.state_set("last_emergency_snapshot_key", None)
        await self._notify_status_message()

    async def _on_type_change(self, old_type: str, new_type: str) -> None:
        """Send notification about outage type change."""
        current_description = await self._get_entity_value("outage_description", "")
        old_reason = self.state_get("last_outage_description") or OUTAGE_TYPE_UA.get(
            old_type,
            old_type,
        )
        new_reason = current_description or OUTAGE_TYPE_UA.get(new_type, new_type)
        self._queue_update(
            reason_old=str(old_reason),
            reason_new=str(new_reason),
        )

    async def _notify_status_message(self) -> None:
        """Push update to status message if available."""
        if self._status_message:
            await self._status_message.request_update()

    async def _send_current_outage_snapshot(self, outage_type: str) -> None:
        """Fetch current DTEK outage fields and send a start/update snapshot."""
        self._cancel_pending_update()
        description = await self._get_entity_value("outage_description", "")
        start_raw = await self._get_entity_value("outage_start", "")
        end_raw = await self._get_entity_value("outage_end", "")
        snapshot_key = self._emergency_snapshot_key(
            outage_type=outage_type,
            description=description,
            start_raw=start_raw,
            end_raw=end_raw,
        )

        self.state_set("last_outage_start", start_raw or None)
        self.state_set("last_outage_end", end_raw)
        self.state_set("last_outage_description", description)
        self._mark_emergency_context()
        if start_raw:
            self.state_set("outage_start_time", start_raw)

        if self.state_get("last_emergency_snapshot_key") == snapshot_key:
            self.log.info("Duplicate outage snapshot suppressed: %s", snapshot_key)
            await self._notify_status_message()
            return

        self.state_set("last_emergency_snapshot_key", snapshot_key)

        text = self.render(
            "emergency_start",
            outage_type=OUTAGE_TYPE_UA.get(outage_type, outage_type),
            description=description,
            start=format_datetime(start_raw),
            end=format_datetime(end_raw),
        )
        await self.send_message(
            text,
            disable_notification=self.config.emergency.silent,
        )
        await self._notify_status_message()

    def _queue_update(
        self,
        *,
        reason_old: str | None = None,
        reason_new: str | None = None,
        end_old: str | None = None,
        end_new: str | None = None,
    ) -> None:
        """Batch outage metadata changes into one follow-up notification."""
        pending = self._pending_update

        if reason_old is not None and "reason_old" not in pending:
            pending["reason_old"] = reason_old
        if reason_new is not None:
            pending["reason_new"] = reason_new

        if end_old is not None and "end_old" not in pending:
            pending["end_old"] = end_old
        if end_new is not None:
            pending["end_new"] = end_new

        if (
            reason_new is not None
            and (
                pending.get("reason_old") is None
                or pending.get("reason_old") != pending.get("reason_new")
            )
        ):
            pending["reason_changed"] = True

        if (
            end_new is not None
            and (
                pending.get("end_old") is None
                or pending.get("end_old") != pending.get("end_new")
            )
        ):
            pending["end_changed"] = True

        if self._pending_update_task and not self._pending_update_task.done():
            return

        self._pending_update_task = asyncio.create_task(
            self._flush_pending_update_after_delay()
        )

    async def _flush_pending_update_after_delay(self) -> None:
        try:
            await asyncio.sleep(self.update_batch_delay)
            await self._flush_pending_update()
        finally:
            if self._pending_update_task is asyncio.current_task():
                self._pending_update_task = None

    async def _flush_pending_update(self) -> None:
        pending = self._pending_update
        self._pending_update = {}

        current_status = await self._get_current_status()
        if current_status == "ok":
            return

        reason_changed = bool(pending.get("reason_changed"))
        end_changed = bool(pending.get("end_changed"))

        if not reason_changed and not end_changed:
            return

        text = self.render(
            "emergency_update",
            reason_changed=reason_changed,
            old_reason=pending.get("reason_old") or "—",
            new_reason=pending.get("reason_new") or "—",
            end_changed=end_changed,
            old_end=format_datetime(pending.get("end_old")),
            new_end=format_datetime(pending.get("end_new")),
        )

        await self.send_message(
            text,
            disable_notification=self.config.emergency.silent,
        )
        self._mark_emergency_context()
        await self._notify_status_message()

    def _cancel_pending_update(self) -> None:
        if self._pending_update_task and not self._pending_update_task.done():
            self._pending_update_task.cancel()
        self._pending_update_task = None
        self._pending_update = {}

    async def _get_entity_value(self, suffix: str, default: str = "") -> str:
        """Get current value of a dtek_monitor entity."""
        entity_id = self.entity(suffix)
        state = await self.ha.get_state(entity_id)
        if state is None:
            return default
        val = str(state.get("state", ""))
        if val in ("unavailable", "unknown", ""):
            return default
        return val

    async def _get_current_status(self) -> str:
        """Return current outage status with HA fallback for restart resilience."""
        stored = self.state_get("last_emergency_status")
        if stored not in (None, ""):
            return str(stored)
        return await self._get_entity_value("status", "ok")

    def _mark_emergency_context(self) -> None:
        self.state_set("last_emergency_context_at", now_kyiv().isoformat())

    @staticmethod
    def _emergency_snapshot_key(
        *,
        outage_type: str,
        description: str,
        start_raw: str,
        end_raw: str,
    ) -> str:
        """Build an idempotency key for a full outage snapshot notification."""
        return "|".join(
            (
                str(outage_type or ""),
                str(description or ""),
                str(start_raw or ""),
                str(end_raw or ""),
            )
        )

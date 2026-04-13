"""Feature 7: Status message delivery (push-based)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from features.base import Feature
from outage_calendar import extract_calendar_events, next_planned_outage
from utils import OUTAGE_TYPE_UA, TZ_KYIV, format_datetime, format_duration, now_kyiv

if TYPE_CHECKING:
    from power_monitor import PowerMonitor


class StatusMessageFeature(Feature):
    """Maintains a status message in the Telegram group.

    Push-based: other features call request_update() to trigger refresh.
    Rate-limited by status_message_min_update_interval.

    Also refreshes periodically (status_message_update_interval).
    Can either edit one reusable message or send a new message every update.
    """

    name = "status_message"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._last_attempt_ts: float = 0.0
        self._last_update_ts: float = 0.0
        self._power_monitor: PowerMonitor | None = None
        self._delayed_task: asyncio.Task[None] | None = None
        self._update_lock = asyncio.Lock()

    def set_power_monitor(self, pm: PowerMonitor) -> None:
        """Inject PowerMonitor dependency."""
        self._power_monitor = pm

    @property
    def enabled(self) -> bool:
        return self.config.status_message.enabled

    def get_watched_entities(self) -> list[str]:
        return []  # Push-based — no entity watching

    async def on_state_change(
        self, entity_id: str, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        pass  # Not used — updates come via request_update()

    async def request_update(self) -> None:
        """Public API: request a status message update.

        Rate-limited by status_message_min_update_interval.
        If called too soon after the last update, schedules a
        delayed update for when the interval expires.
        """
        min_interval = self.config.status_message.min_update_interval
        elapsed = time.monotonic() - self._last_attempt_ts

        if elapsed >= min_interval and not self._update_lock.locked():
            self._cancel_delayed()
            await self._update_status_message()
        else:
            # Schedule update after remaining cooldown
            if not self._delayed_task or self._delayed_task.done():
                remaining = min_interval - elapsed
                self._delayed_task = asyncio.create_task(
                    self._delayed_update(remaining)
                )

    async def on_tick(self) -> None:
        """Periodic update for the reusable status message."""
        interval = self.config.status_message.update_interval
        if interval <= 0:
            return
        if time.monotonic() - self._last_update_ts < interval:
            return
        if self._update_lock.locked():
            return
        if self.config.status_message.delivery_mode == "send_new":
            return
        await self._update_status_message()

    async def on_start(self) -> None:
        """Send or update status message on startup."""
        await self._update_status_message()

    async def on_stop(self) -> None:
        """Cancel pending delayed updates during shutdown."""
        self._cancel_delayed()

    async def _delayed_update(self, delay: float) -> None:
        """Execute a delayed status message update."""
        try:
            await asyncio.sleep(delay)
            await self._update_status_message()
        finally:
            if self._delayed_task is asyncio.current_task():
                self._delayed_task = None

    def _cancel_delayed(self) -> None:
        """Cancel any pending delayed update."""
        if self._delayed_task and not self._delayed_task.done():
            self._delayed_task.cancel()
        self._delayed_task = None

    async def _update_status_message(self) -> None:
        """Build and deliver the status message using configured mode."""
        async with self._update_lock:
            self._last_attempt_ts = time.monotonic()

            house_state = (
                await self._power_monitor.get_power_state()
                if self._power_monitor
                else "on"
            )
            text = await self._build_status_text(house_state)
            delivery_mode = self.config.status_message.delivery_mode
            silent = self.config.status_message.silent

            updated = False
            msg_id = None

            if delivery_mode == "send_new":
                msg_id = await self.send_message(
                    text,
                    disable_notification=silent,
                )
                updated = msg_id is not None
            else:
                msg_id = self.state_get("status_message_id")

                if msg_id:
                    result = await self.edit_message_result(msg_id, text)
                    if result == "ok":
                        updated = True
                    elif result == "not_found":
                        msg_id = None

                if not msg_id:
                    msg_id = await self.send_message(
                        text,
                        disable_notification=silent,
                        pin=self.config.status_message.pin,
                    )
                    if msg_id:
                        self.state_set("status_message_id", msg_id)
                        updated = True

            if updated:
                self._last_update_ts = time.monotonic()
                self.log.debug(
                    "Status updated (power=%s, mode=%s, msg_id=%s)",
                    house_state,
                    delivery_mode,
                    msg_id,
                )
                return

        retry_delay = min(
            5.0,
            max(float(self.config.status_message.min_update_interval), 1.0),
        )
        if self._delayed_task is asyncio.current_task():
            self._delayed_task = None
        if not self._delayed_task or self._delayed_task.done():
            self._delayed_task = asyncio.create_task(self._delayed_update(retry_delay))
        self.log.warning("Status message update failed; retrying in %.1fs", retry_delay)

    async def _build_status_text(self, house_state: str) -> str:
        """Build the status message text."""
        short_name = self.config.display_name
        group = await self._resolve_current_group()

        if house_state == "on":
            return await self._build_on_text(short_name, group)
        if house_state == "partial":
            return await self._build_partial_text(short_name, group)
        return await self._build_off_text(short_name, group)

    async def _build_on_text(self, short_name: str, group: str) -> str:
        """Build status text when power is on."""
        snapshot: dict[str, Any] = {}

        if self._power_monitor:
            snapshot = await self._power_monitor.get_voltage_snapshot()

        next_outage = await self._get_next_outage()

        return self.render(
            "status_on",
            short_name=short_name,
            group=group,
            voltage=snapshot.get("single_voltage"),
            phases=snapshot.get("phases"),
            next_outage=next_outage,
        )

    async def _build_partial_text(self, short_name: str, group: str) -> str:
        """Build status text when only part of the configured phases are present."""
        snapshot: dict[str, Any] = {}
        if self._power_monitor:
            snapshot = await self._power_monitor.get_voltage_snapshot()

        next_outage = await self._get_next_outage()

        return self.render(
            "status_partial",
            short_name=short_name,
            group=group,
            phases=snapshot.get("phases"),
            missing_phases=snapshot.get("missing_phases"),
            unknown_phases=snapshot.get("unknown_phases"),
            next_outage=next_outage,
        )

    async def _build_off_text(self, short_name: str, group: str) -> str:
        """Build status text when power is off."""
        status_raw = None
        dtek_reports_ok = False
        outage_type_raw = None
        outage_description = None
        outage_start = None
        outage_end = None
        outage_duration = None

        power_last_change = self.state_get("power_last_change")
        if power_last_change:
            try:
                outage_duration = format_duration(
                    (now_kyiv() - datetime.fromisoformat(str(power_last_change))).total_seconds()
                )
            except (TypeError, ValueError):
                outage_duration = None

        status_state = await self.ha.get_state(self.entity("status"))
        if status_state:
            status_raw = self.get_state_value(status_state)
            dtek_reports_ok = status_raw == "ok"
            if status_raw not in ("unavailable", "unknown", "ok", ""):
                outage_type_raw = status_raw

        if not dtek_reports_ok:
            description_state = await self.ha.get_state(self.entity("outage_description"))
            if description_state:
                val = self.get_state_value(description_state)
                if val not in ("unavailable", "unknown", ""):
                    outage_description = val

            start_state = await self.ha.get_state(self.entity("outage_start"))
            if start_state:
                val = self.get_state_value(start_state)
                if val not in ("unavailable", "unknown", ""):
                    outage_start = format_datetime(val)

            end_state = await self.ha.get_state(self.entity("outage_end"))
            if end_state:
                val = self.get_state_value(end_state)
                if val not in ("unavailable", "unknown", ""):
                    outage_end = format_datetime(val)

        outage_type = OUTAGE_TYPE_UA.get(outage_type_raw, outage_type_raw) if outage_type_raw else None

        return self.render(
            "status_off",
            short_name=short_name,
            group=group,
            outage_duration=outage_duration,
            dtek_reports_ok=dtek_reports_ok,
            outage_type=outage_type,
            outage_description=outage_description,
            outage_start=outage_start,
            outage_end=outage_end,
        )

    async def _resolve_current_group(self) -> str:
        """Get the current schedule group, falling back to live HA state when needed."""
        stored = str(self.state_get("current_group", "")).strip()
        if stored and stored not in ("unknown", "unavailable"):
            return stored

        for entity_id in self.entity_candidates("schedule_group"):
            state = await self.ha.get_state(entity_id)
            if not state:
                continue
            value = self.get_state_value(state)
            if value in ("", "unknown", "unavailable"):
                continue
            self.state_set("current_group", value)
            return value

        return "—"

    async def _get_next_outage(self) -> str | None:
        """Get the next planned outage as a formatted string."""
        calendar_entity = self.entity("outage_schedule")
        now = now_kyiv()

        result = await self.ha.call_service(
            domain="calendar",
            service="get_events",
            entity_id=calendar_entity,
            data={
                "start_date_time": now.isoformat(),
                "end_date_time": (now + timedelta(days=2)).isoformat(),
            },
            return_response=True,
        )

        events = extract_calendar_events(
            result,
            calendar_entity,
            exclude_emergency=True,
        )
        upcoming = next_planned_outage(events, now=now)
        if upcoming:
            start_dt, end_dt = upcoming
            s = start_dt.astimezone(TZ_KYIV)
            e = end_dt.astimezone(TZ_KYIV)
            return f"{s.strftime('%d.%m')} {s.strftime('%H:%M')}–{e.strftime('%H:%M')}"

        return None

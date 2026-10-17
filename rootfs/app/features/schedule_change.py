"""Feature 1: Schedule change notifications."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from typing import Any

from features.base import Feature
from groups import canonical_groups
from schedule_notifications import (
    notification_lock,
    schedule_signature,
    acknowledge_schedule,
)
from outage_calendar import build_schedule_lines, extract_calendar_events
from utils import TZ_KYIV, now_kyiv

RECENT_SCHEDULE_WINDOW = timedelta(hours=24)


class ScheduleChangeFeature(Feature):
    """Monitors schedule changes and sends updated schedule to Telegram.

    Trigger: sensor.<prefix>_schedule_changed timestamp changes.
    Action: fetch calendar events for today+tomorrow, compute signature,
    compare with stored signature, send message if changed.
    """

    name = "schedule_change"

    @property
    def enabled(self) -> bool:
        return self.config.schedule_change.enabled

    def get_watched_entities(self) -> list[str]:
        return [self.entity("schedule_changed")]

    async def on_state_change(
        self, entity_id: str, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        if self.get_state_value(new_state) in ("unavailable", "unknown", ""):
            return
        await self._check_and_notify()

    async def on_start(self) -> None:
        await self._check_and_notify()

    async def on_tick(self) -> None:
        await self._check_and_notify()

    async def _check_and_notify(self) -> None:
        async with notification_lock(self.state, self.config.entity_prefix):
            await self._check_and_notify_locked()

    async def _check_and_notify_locked(self) -> None:
        """Let a pending group notification deliver its schedule first."""
        previous_groups = self.state_get("notified_groups")
        if previous_groups is None:
            previous_groups = canonical_groups(self.state_get("current_group", ""))
        events = await self.calendar_snapshot(self._fetch_outage_events)
        if (
            getattr(getattr(self.config, "group_change", None), "enabled", False)
            and previous_groups
            and previous_groups != self.state_get("current_groups")
        ):
            return
        schedule_lines = self._build_schedule_lines(events)
        signature = self._compute_signature(events)

        old_signature = self.state_get("schedule_signature", "")
        if signature == old_signature:
            self.log.debug("Schedule signature unchanged, skipping")
            return

        if not old_signature:
            self.state_set("schedule_signature", signature)
            self.state_set("has_schedule", bool(schedule_lines))
            if schedule_lines:
                self.state_set("last_schedule_seen_at", now_kyiv().isoformat())
            return

        had_schedule = self.state_get("has_schedule", False)
        had_recent_schedule = self._had_recent_schedule()
        if schedule_lines:
            self.state_set("last_schedule_seen_at", now_kyiv().isoformat())
            text = self.render("schedule_change", schedule_lines=schedule_lines)
        elif had_schedule and had_recent_schedule:
            # Only send "no outages" if a non-empty schedule existed recently.
            start = now_kyiv().replace(hour=0, minute=0, second=0, microsecond=0)
            if not self.has_schedule_coverage(start, start + timedelta(days=2)):
                self.log.debug("Incomplete published coverage; suppressing all-clear")
                return
            text = self.render("schedule_empty")
        else:
            if had_schedule:
                self.log.info(
                    "Schedule cleared but last non-empty schedule is older than %s, skipping notification",
                    RECENT_SCHEDULE_WINDOW,
                )
            else:
                self.log.info("Schedule still empty, skipping notification")
            self.state_set("schedule_signature", signature)
            self.state_set("has_schedule", False)
            return

        delivered = await self.send_message(
            text,
            disable_notification=self.config.schedule_change.silent,
        )
        if delivered:
            await acknowledge_schedule(self, events, schedule_lines)
        self.log.info("Schedule update processed (%d lines)", len(schedule_lines))

    async def _fetch_outage_events(self) -> list[dict[str, Any]]:
        """Fetch outage calendar events for today and tomorrow."""
        calendar_entity = self.entity("outage_schedule")
        now = now_kyiv()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=2)

        result = await self.ha.call_service(
            domain="calendar",
            service="get_events",
            entity_id=calendar_entity,
            data={
                "start_date_time": start.isoformat(),
                "end_date_time": end.isoformat(),
            },
            return_response=True,
        )

        events = extract_calendar_events(
            result,
            calendar_entity,
            exclude_emergency=True,
        )
        if not events:
            self.log.info(
                "No events from %s",
                calendar_entity,
            )

        self.log.info(
            "Fetched %d outage events",
            len(events),
        )
        return events

    def _build_schedule_lines(self, events: list[dict[str, Any]]) -> list[str]:
        """Build formatted schedule lines grouped by day.

        Merges overlapping time slots within each day.
        Returns lines like: "📅 05.03 — 14:00–18:00, 20:00–00:00"
        """
        return build_schedule_lines(
            events, header_group=self.state_get("current_group", ""), group_labels=self.schedule_display_labels()
        )

    def _had_recent_schedule(self) -> bool:
        """Check whether a non-empty schedule was seen recently enough."""
        raw = self.state_get("last_schedule_seen_at")
        if not raw:
            return False

        try:
            seen_at = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            self.log.warning("Invalid last_schedule_seen_at value: %r", raw)
            return False

        if seen_at.tzinfo is None:
            seen_at = seen_at.replace(tzinfo=TZ_KYIV)

        return now_kyiv() - seen_at.astimezone(TZ_KYIV) <= RECENT_SCHEDULE_WINDOW

    _compute_signature = staticmethod(schedule_signature)

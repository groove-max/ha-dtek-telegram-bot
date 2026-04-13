"""Feature 1: Schedule change notifications."""

from __future__ import annotations

import hashlib
from datetime import datetime
from datetime import timedelta
from typing import Any

from features.base import Feature
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
        old_val = self.get_state_value(old_state)
        new_val = self.get_state_value(new_state)

        # Ignore transitions from/to unavailable/unknown
        if new_val in ("unavailable", "unknown", ""):
            return
        if old_val in ("unavailable", "unknown", ""):
            # First valid value after startup — store signature only, don't notify
            self.log.info("Initial schedule_changed value: %s (storing signature)", new_val)
            events = await self._fetch_outage_events()
            schedule_lines = self._build_schedule_lines(events)
            self.state_set("schedule_signature", self._compute_signature(events))
            self.state_set("has_schedule", bool(schedule_lines))
            if schedule_lines:
                self.state_set("last_schedule_seen_at", now_kyiv().isoformat())
            return

        self.log.info("Schedule changed timestamp: %s -> %s", old_val, new_val)
        await self._check_and_notify()

    async def _check_and_notify(self) -> None:
        """Fetch calendar events, build schedule, compare signature, send if new."""
        events = await self._fetch_outage_events()
        schedule_lines = self._build_schedule_lines(events)
        signature = self._compute_signature(events)

        old_signature = self.state_get("schedule_signature", "")
        if signature == old_signature:
            self.log.debug("Schedule signature unchanged, skipping")
            return

        had_schedule = self.state_get("has_schedule", False)
        had_recent_schedule = self._had_recent_schedule()
        self.state_set("schedule_signature", signature)
        self.state_set("has_schedule", bool(schedule_lines))

        if schedule_lines:
            self.state_set("last_schedule_seen_at", now_kyiv().isoformat())
            text = self.render("schedule_change", schedule_lines=schedule_lines)
        elif had_schedule and had_recent_schedule:
            # Only send "no outages" if a non-empty schedule existed recently.
            text = self.render("schedule_empty")
        else:
            if had_schedule:
                self.log.info(
                    "Schedule cleared but last non-empty schedule is older than %s, skipping notification",
                    RECENT_SCHEDULE_WINDOW,
                )
            else:
                self.log.info("Schedule still empty, skipping notification")
            return

        await self.send_message(
            text,
            disable_notification=self.config.schedule_change.silent,
        )
        self.log.info("Sent schedule update (%d lines)", len(schedule_lines))

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
        return build_schedule_lines(events)

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

    @staticmethod
    def _compute_signature(events: list[dict[str, Any]]) -> str:
        """Compute a hash signature for deduplication."""
        parts: list[str] = []
        for e in events:
            start = e.get("start", "")
            end = e.get("end", "")
            parts.append(f"{start}-{end}")
        raw = "|".join(parts) if parts else "none"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

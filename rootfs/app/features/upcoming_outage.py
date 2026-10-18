"""Feature 6: Upcoming outage warning."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from features.base import Feature
from groups import event_group
from outage_calendar import extract_calendar_events, parse_event_time
from utils import TZ_KYIV, format_datetime, now_kyiv


class UpcomingOutageFeature(Feature):
    """Warns about upcoming planned outages before they start.

    Runs periodically (on_tick, ~60s). Checks calendar for outage events
    starting within `upcoming_outage_minutes`. Sends warning once per event.
    Delivery can be filtered by current house power state.
    """

    name = "upcoming_outage"

    @property
    def enabled(self) -> bool:
        return self.config.upcoming_outage.enabled

    def get_watched_entities(self) -> list[str]:
        return []

    async def on_state_change(
        self, entity_id: str, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        pass

    async def on_tick(self) -> None:
        """Check for upcoming outages."""
        power_state = self.state_get("power_state", "unknown")
        power_filter = self.config.upcoming_outage.power_filter
        if power_filter == "only_when_available" and power_state not in (
            "on",
            "partial",
        ):
            return
        if power_filter == "only_when_missing" and power_state != "off":
            return

        now = now_kyiv()
        minutes = self.config.upcoming_outage.minutes
        window_end = now + timedelta(minutes=minutes)

        events = await self.calendar_snapshot(
            lambda: self._fetch_upcoming_events(now, window_end)
        )
        if not events:
            return

        for event in events:
            ev_start = parse_event_time(event.get("start", ""))
            ev_end = parse_event_time(event.get("end", ""))
            if ev_start is None or ev_end is None:
                continue

            # Only future events (not already started)
            if ev_start <= now:
                continue

            # Check if within warning window
            time_until = (ev_start - now).total_seconds() / 60
            if time_until > minutes:
                continue

            # Deduplication: don't warn about the same event twice
            event_key = (
                f"{event_group(event)}:{ev_start.isoformat()}:{ev_end.isoformat()}"
            )
            warned = self.state_get("warned_outage_start", "")
            warned_events = self.state_get("warned_outages", [])
            if warned == event_key or event_key in warned_events:
                continue

            text = self.render(
                "upcoming_outage",
                group=event_group(event) or self.state_get("current_group", "—"),
                minutes=max(math.ceil(time_until), 1),
                start=format_datetime(ev_start),
                end=format_datetime(ev_end),
            )
            delivered = await self.send_message(
                text,
                delivery_key=f"upcoming:{self.config.entity_prefix}:{event_key}",
                delivery_context={"stream": event_key, "expires": ev_start.timestamp()},
                disable_notification=self.config.upcoming_outage.silent,
            )
            if delivered:
                self.state_set("warned_outage_start", event_key)
                self.state_set("warned_outages", [*warned_events, event_key][-64:])
            self.log.info(
                "Processed upcoming outage warning (in %d min): %s – %s",
                max(math.ceil(time_until), 1),
                format_datetime(ev_start),
                format_datetime(ev_end),
            )
            break  # One warning per tick

    async def _fetch_upcoming_events(
        self, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Fetch calendar events in the given time window."""
        calendar_entity = self.entity("outage_schedule")
        result = await self.ha.call_service(
            domain="calendar",
            service="get_events",
            entity_id=calendar_entity,
            data={
                "start_date_time": start.isoformat(),
                "end_date_time": (end + timedelta(hours=1)).isoformat(),
            },
            return_response=True,
        )
        return extract_calendar_events(result, calendar_entity, exclude_emergency=True)

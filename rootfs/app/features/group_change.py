"""Feature 3: Schedule group change notifications."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from features.base import Feature
from outage_calendar import build_schedule_lines, extract_calendar_events
from utils import now_kyiv


class GroupChangeFeature(Feature):
    """Monitors schedule group changes and sends notification with new schedule.

    On group change:
    1. Sends notification about old → new group
    2. Fetches and includes the schedule for the new group (reuses schedule logic)
    """

    name = "group_change"

    @property
    def enabled(self) -> bool:
        return self.config.group_change.enabled

    def get_watched_entities(self) -> list[str]:
        return [self.entity("schedule_group")]

    async def on_state_change(
        self, entity_id: str, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        old_val = self.get_state_value(old_state)
        new_val = self.get_state_value(new_state)

        if new_val in ("unavailable", "unknown", ""):
            return
        if old_val in ("unavailable", "unknown", ""):
            # First valid value — just store it
            self.state_set("current_group", new_val)
            return
        if old_val == new_val:
            return

        self.log.info("Group changed: %s -> %s", old_val, new_val)
        self.state_set("current_group", new_val)

        events = await self._fetch_outage_events()
        schedule_lines = build_schedule_lines(events)

        text = self.render(
            "group_change",
            old_group=old_val,
            new_group=new_val,
            schedule_lines=schedule_lines,
        )
        await self.send_message(
            text,
            disable_notification=self.config.group_change.silent,
        )

    async def _fetch_outage_events(self) -> list[dict[str, Any]]:
        """Fetch outage calendar events for today and tomorrow."""
        calendar_entity = self.entity("outage_schedule")
        start = now_kyiv().replace(hour=0, minute=0, second=0, microsecond=0)
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
        return extract_calendar_events(
            result,
            calendar_entity,
            exclude_emergency=True,
        )

    async def on_start(self) -> None:
        """Store initial group value on startup."""
        entity_id = self.entity("schedule_group")
        state = await self.ha.get_state(entity_id)
        if state:
            val = self.get_state_value(state)
            if val not in ("unavailable", "unknown", ""):
                self.state_set("current_group", val)
                self.log.info("Initial group: %s", val)

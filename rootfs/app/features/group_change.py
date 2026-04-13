"""Feature 3: Schedule group change notifications."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from features.base import Feature
from outage_calendar import build_schedule_lines, extract_calendar_events
from utils import now_kyiv

if TYPE_CHECKING:
    from features.status_message import StatusMessageFeature


class GroupChangeFeature(Feature):
    """Monitors schedule group changes and sends notification with new schedule.

    On group change:
    1. Sends notification about old → new group
    2. Fetches and includes the schedule for the new group (reuses schedule logic)
    """

    name = "group_change"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._status_message: StatusMessageFeature | None = None

    def set_status_message(self, sm: StatusMessageFeature) -> None:
        """Inject StatusMessageFeature to refresh the pinned status on group updates."""
        self._status_message = sm

    @property
    def enabled(self) -> bool:
        return self.config.group_change.enabled

    def get_watched_entities(self) -> list[str]:
        return self.entity_candidates("schedule_group")

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
            await self._notify_status_message()
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
        await self._notify_status_message()

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
        for entity_id in self.entity_candidates("schedule_group"):
            state = await self.ha.get_state(entity_id)
            if not state:
                continue
            val = self.get_state_value(state)
            if val not in ("unavailable", "unknown", ""):
                self.state_set("current_group", val)
                self.log.info("Initial group: %s", val)
                return

    async def _notify_status_message(self) -> None:
        """Refresh the pinned status when the current group becomes available or changes."""
        if self._status_message:
            await self._status_message.request_update()

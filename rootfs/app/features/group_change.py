"""Feature 3: Schedule group change notifications."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from features.base import Feature
from schedule_notifications import notification_lock, acknowledge_schedule
from groups import state_groups, canonical_groups
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
        async with notification_lock(self.state, self.config.entity_prefix):
            await self._handle_state_change(entity_id, old_state, new_state)

    async def _handle_state_change(
        self, entity_id: str, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        groups = state_groups(new_state)
        if not groups:
            self.state_set("groups_available", False)
            return
        self.state_set("groups_available", True)
        previous = self.state_get("notified_groups")
        if previous is None:
            previous = canonical_groups(
                self.state_get("current_group", "")
            ) or state_groups(old_state)
        if previous:
            self.state_set("notified_groups", previous)
        self.state_set("current_groups", groups)
        selected = getattr(self.config, "selected_group", "")
        display = selected if selected in groups else ", ".join(groups)
        self.state_set("current_group", display)
        self.state_set("groups_snapshot", new_state)
        if not previous:
            self.state_set("notified_groups", groups)
            await self._notify_status_message()
            return
        if previous == groups:
            return
        # A failed calendar request must not turn into an empty-schedule claim.
        events = await self.calendar_snapshot(self._fetch_outage_events)
        if self.state_get("current_groups") != groups:
            return
        lines = build_schedule_lines(events, header_group=display, group_labels=self.schedule_display_labels())
        start = now_kyiv().replace(hour=0, minute=0, second=0, microsecond=0)
        complete = self.has_schedule_coverage(start, start + timedelta(days=2))
        text = self.render(
            "group_change",
            old_group=", ".join(previous),
            new_group=display,
            schedule_lines=lines,
            schedule_complete=complete,
        )
        if await self.send_message(
            text, disable_notification=self.config.group_change.silent
        ):
            self.state_set("notified_groups", groups)
            if lines or complete:
                await acknowledge_schedule(self, events, lines)
            else:
                save = getattr(self.state, "save", None)
                if save is not None:
                    await save()
            await self._notify_status_message()

    async def on_tick(self) -> None:
        for entity_id in self.entity_candidates("schedule_group"):
            state = await self.ha.get_state(entity_id)
            if state_groups(state):
                await self.on_state_change(entity_id, {}, state)
                return
        self.state_set("groups_available", False)

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
        await self.on_tick()

    async def _notify_status_message(self) -> None:
        """Refresh the pinned status when the current group becomes available or changes."""
        if self._status_message:
            await self._status_message.request_update()

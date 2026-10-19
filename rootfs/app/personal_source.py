"""Account-backed HA view consumed by the existing notification features."""

from datetime import datetime
import asyncio

from ha_client import HACommandError
from outage_calendar import ScheduleUnavailable


class PersonalSource:
    def __init__(self, ha, config):
        self.ha, self.config = ha, config
        self.snapshot = None
        self._lock = asyncio.Lock()

    def __getattr__(self, name):
        return getattr(self.ha, name)

    async def refresh(self):
        async with self._lock:
            try:
                self.snapshot = await self.ha.command(
                    "dtek_monitor/bot_account",
                    entity_id=self.config.account_entity,
                    action="snapshot",
                )
                if self.snapshot.get("version") != 1:
                    raise HACommandError("unsupported_version")
            except Exception:
                self.snapshot = None
                raise
            watch = getattr(self.ha, "watch_entities", None)
            if watch:
                watch(
                    set(self.snapshot.get("entities", {}).values())
                    | {self.config.account_entity}
                )
            return self.snapshot

    def _suffix(self, entity):
        for domain in ("sensor", "binary_sensor", "calendar"):
            prefix = f"{domain}.{self.config.entity_prefix}_"
            if entity.startswith(prefix):
                return entity[len(prefix) :]
        return None

    async def get_state(self, entity):
        suffix = self._suffix(entity)
        if suffix is None:
            return await self.ha.get_state(entity)
        try:
            data = await self.refresh()
        except Exception:
            return {
                "entity_id": entity,
                "state": "unavailable",
                "attributes": {"cabinet_group_status": "unavailable"},
            }
        attrs = data["attributes"]
        if suffix in ("primary_schedule_group", "schedule_group"):
            return {
                "entity_id": entity,
                "state": ", ".join(data["groups"]) or "unavailable",
                "attributes": attrs,
            }
        if suffix == "schedule_changed":
            return {
                "entity_id": entity,
                "state": data["checked_at"]
                if attrs["schedule_notifications_ready"]
                else "unavailable",
                "attributes": attrs,
            }
        actual = data.get("entities", {}).get(suffix)
        return (
            await self.ha.get_state(actual)
            if actual
            else {"entity_id": entity, "state": "unavailable", "attributes": {}}
        )

    async def call_service(self, domain, service, entity_id=None, data=None, **kwargs):
        suffix = self._suffix(entity_id or "")
        if (
            domain != "calendar"
            or service != "get_events"
            or suffix not in ("outage_schedule", "possible_outage_schedule")
        ):
            return await self.ha.call_service(
                domain=domain, service=service, entity_id=entity_id, data=data, **kwargs
            )
        try:
            snapshot = await self.refresh()
        except (HACommandError, TimeoutError, ConnectionError) as exc:
            raise ValueError("Cabinet schedule unavailable") from exc
        kind = "outage" if suffix == "outage_schedule" else "possible"
        calendar = snapshot["calendars"][kind]
        if not calendar["available"]:
            raise ScheduleUnavailable("Cabinet schedule unavailable")
        start, end = (
            datetime.fromisoformat(data[k])
            for k in ("start_date_time", "end_date_time")
        )
        events = [
            e
            for e in calendar["events"]
            if datetime.fromisoformat(e["start"]) < end
            and datetime.fromisoformat(e["end"]) > start
        ]
        return {
            "success": True,
            "result": {"response": {entity_id: {"events": events}}},
        }

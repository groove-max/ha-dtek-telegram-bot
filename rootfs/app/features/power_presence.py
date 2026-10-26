"""Debounced power evidence, with telemetry loss kept separate from outages."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from features.base import Feature
from utils import format_duration, now_kyiv

if TYPE_CHECKING:
    from features.status_message import StatusMessageFeature
    from power_monitor import PowerMonitor


class PowerPresenceFeature(Feature):
    """Unknown readings never establish an outage or a physical restoration.

    Deadlines use a monotonic clock and survive repeated identical observations.
    Confirmed history is retained across gaps, but never presented as live data.
    """

    name = "power_presence"
    CHECK_INTERVAL = 5

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._power_monitor: PowerMonitor | None = None
        self._status_message: StatusMessageFeature | None = None
        self._voltage_entities = [
            v.entity for v in self.config.voltage.entities if v.entity
        ]
        self._lock = asyncio.Lock()
        self._watchdog: asyncio.Task | None = None
        self._timer: asyncio.Task | None = None
        self._candidate: str | None = None
        self._candidate_since = 0.0
        self._candidate_at: datetime | None = None
        self._phase_candidates: dict[str, tuple[bool, float]] = {}
        self._confirm_started_at: datetime | None = None
        self._confirm_since = 0.0
        self._loss_rejected = False
        self._stopped = False
        self._pending_house: tuple[str, str, str | None] | None = None

    def set_power_monitor(self, pm: PowerMonitor) -> None:
        self._power_monitor = pm

    def set_status_message(self, sm: StatusMessageFeature) -> None:
        self._status_message = sm

    @property
    def enabled(self) -> bool:
        return self.config.power.enabled

    def get_watched_entities(self) -> list[str]:
        if self.config.power.mode == "sources":
            return list(dict.fromkeys(
                [s.entity for s in self.config.power.sources] + self._voltage_entities
            ))
        entities = (
            [self.entity("power")]
            if self.config.power.mode == "dtek_only"
            else list(self._voltage_entities)
        )
        if (
            self.config.power.mode == "loss_plus_voltage"
            and self.config.power.loss_entity
        ):
            entities.append(self.config.power.loss_entity)
        if len(self._voltage_entities) > 1:
            entities.extend(self._voltage_entities)
        return list(dict.fromkeys(entities))

    async def on_start(self) -> None:
        self._stopped = False
        if self._power_monitor:
            self._power_monitor.sources.reset()
        # A persisted timeout is not valid evidence after a process restart.
        self.state_set("loss_stale_reference_at", None)
        self.state_set("power_observation_gap", True)
        async with self._lock:
            if self._power_monitor:
                actual = await self._power_monitor.detect_current_power()
                previous = self.state_get("power_state", "unknown")
                if not self.state_get("power_last_confirmed_state") and previous in (
                    "on",
                    "partial",
                    "off",
                ):
                    self.state_set("power_last_confirmed_state", previous)
                if (
                    actual in ("on", "partial")
                    and self.state_get("power_last_confirmed_state") != "off"
                ):
                    self.state_set("power_state", actual)
                    self.state_set("power_last_confirmed_state", actual)
                    if not self.state_get("power_last_change"):
                        self.state_set("power_last_change", now_kyiv().isoformat())
                else:
                    self.state_set("power_state", "unknown")
                snapshot = await self._power_monitor.get_voltage_snapshot()
                for phase in snapshot["phases"]:
                    if phase["available"] is not None:
                        self.state_set(
                            self._phase_state_key(phase["entity_id"]),
                            "on" if phase["available"] else "off",
                        )
        await self.on_tick()
        self._watchdog = asyncio.create_task(self._watch())

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(self.CHECK_INTERVAL)
            await self._safe_tick()

    async def _safe_tick(self) -> None:
        try:
            await self.on_tick()
        except Exception:
            self.log.exception("Power observation failed; will retry")

    async def on_stop(self) -> None:
        self._stopped = True
        tasks = [
            t
            for t in (self._timer, self._watchdog)
            if t and t is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._timer = self._watchdog = None
        self._candidate = None
        self._phase_candidates.clear()

    async def on_state_change(
        self, entity_id: str, old_state: dict, new_state: dict
    ) -> None:
        # Read current HA state: queued/reordered websocket events are not evidence.
        if entity_id in self.get_watched_entities():
            await self.on_tick()

    async def on_tick(self) -> None:
        if self._stopped or not self._power_monitor:
            return
        async with self._lock:
            if not self._stopped:
                await self._reconcile()

    async def _reconcile(self) -> None:
        pm = self._power_monitor
        assert pm is not None
        clock = self._monotonic()
        snapshot = await pm.get_voltage_snapshot()
        deadlines: list[float] = []
        if self.config.power.mode == "loss_plus_voltage":
            if self._loss_rejected and any(
                p["available"] is False for p in snapshot["phases"]
            ):
                self._loss_rejected = False
                self._confirm_started_at = None
            if not await pm.loss_condition_met():
                self._confirm_started_at = None
                self._loss_rejected = False
                self.state_set("loss_stale_reference_at", None)
            elif not self._loss_rejected:
                if self._confirm_started_at is None:
                    self._confirm_started_at, self._confirm_since = now_kyiv(), clock
                if any(
                    p["available"] is True
                    and (
                        pm._parse_state_datetime(p["last_updated"])
                        or self._confirm_started_at
                    )
                    > self._confirm_started_at
                    for p in snapshot["phases"]
                ):
                    self._loss_rejected = True
                    self.state_set("loss_stale_reference_at", None)
                elif clock - self._confirm_since >= self.config.power.confirm_timeout:
                    self.state_set(
                        "loss_stale_reference_at", self._confirm_started_at.isoformat()
                    )
                else:
                    deadlines.append(
                        self._confirm_since + self.config.power.confirm_timeout
                    )

        if self.config.power.mode == "sources":
            evidence = await pm.sources.snapshot()
            actual = evidence["state"]
            if evidence.get("pending_seconds"):
                deadlines.append(clock + evidence["pending_seconds"])
        else:
            actual = await pm.detect_current_power()
        current = self.state_get("power_state", "unknown")
        if actual != self._candidate:
            self._candidate, self._candidate_since = actual, clock
            self._candidate_at = now_kyiv().astimezone(timezone.utc)
            self._pending_house = None
        house_transition = False
        if actual == "unknown":
            self.state_set("power_observation_gap", True)
            if current != "unknown":
                self.state_set("power_state", "unknown")
                await self._status_update()
            delay = getattr(self.config.power, "telemetry_loss_delay", 20)
            if clock - self._candidate_since >= delay:
                if getattr(
                    self.config.power, "telemetry_notifications", False
                ) and not self.state_get("power_telemetry_alert"):
                    delivered = await self._send_house("power_telemetry_lost")
                    if delivered is not None:
                        self.state_set("power_telemetry_alert", True)
            else:
                deadlines.append(self._candidate_since + delay)
        elif (
            actual != current
            or self.state_get("power_telemetry_alert")
            or self.state_get("power_last_confirmed_state") != actual
        ):
            delay = (
                self.config.power.loss_delay
                if actual == "off" or (actual == "partial" and current == "on")
                else self.config.power.restore_delay
            )
            if clock - self._candidate_since >= delay:
                if await self._recovery_is_confirmed(actual):
                    house_transition = await self._commit(actual, snapshot)
                    if self._candidate is None:
                        return  # Send-time evidence invalidated this snapshot.
            else:
                deadlines.append(self._candidate_since + delay)
        if self.config.power.mode != "sources":
            await self._phases(snapshot, clock, deadlines, suppress=house_transition)
        self._arm(deadlines)

    @staticmethod
    def _monotonic() -> float:
        return asyncio.get_running_loop().time()

    def _arm(self, deadlines: list[float]) -> None:
        if self._timer and self._timer is not asyncio.current_task():
            self._timer.cancel()
        self._timer = None
        if deadlines and not self._stopped:

            async def wake() -> None:
                await asyncio.sleep(
                    max(0.001, min(deadlines) - asyncio.get_running_loop().time())
                )
                await self._safe_tick()

            self._timer = asyncio.create_task(wake())

    async def _commit(self, target: str, snapshot: dict) -> bool:
        previous = self.state_get("power_last_confirmed_state")
        # Older state files did not carry confirmed history separately.
        if previous is None:
            previous = self.state_get("power_state", "unknown")
        gap = self.state_get("power_observation_gap", False)
        duration = (
            "невідомо (була перерва в даних)"
            if gap
            else self._calc_duration(self.state_get("power_last_change"), now_kyiv())
        )
        template = None
        if target == "off" and previous != "off":
            template = "power_lost"
        elif target in ("on", "partial") and previous == "off":
            template = "power_restored"
        elif self.state_get("power_telemetry_alert") and getattr(
            self.config.power, "telemetry_notifications", False
        ):
            template = "power_telemetry_restored"
        self.state_set("power_state", target)
        if template:
            delivered = await self._send_house(
                template,
                duration=duration,
                house_state=target,
                voltage=snapshot.get("single_voltage"),
                phases=snapshot.get("phases"),
                missing_phases=snapshot.get("missing_phases", []),
                unknown_phases=snapshot.get("unknown_phases", []),
            )
            # Keep retrying the same delivery identity until the journal accepts it.
            if delivered is None:
                return False
        if (
            gap
            or (target == "off") != (previous == "off")
            or not self.state_get("power_last_change")
        ):
            self.state_set("power_last_change", now_kyiv().isoformat())
        self.state_set("power_state", target)
        self.state_set("power_last_confirmed_state", target)
        self.state_set("power_telemetry_alert", False)
        self.state_set("power_observation_gap", False)
        self._pending_house = None
        await self._status_update()
        return template in ("power_lost", "power_restored")

    async def _recovery_is_confirmed(self, target: str) -> bool:
        if target not in ("on", "partial") or self.state_get("power_last_confirmed_state") != "off":
            return True
        assert self._power_monitor is not None and self._candidate_at is not None
        return await self._power_monitor.restoration_confirmed_since(
            self._candidate_at + timedelta(seconds=self.config.power.restore_delay)
        )

    async def _notification_is_current(self, target: str) -> bool:
        assert self._power_monitor is not None
        if self._stopped:
            return False
        if (
            await self._power_monitor.detect_current_power() == target
            and await self._recovery_is_confirmed(target)
        ):
            return not self._stopped
        # The event ended or became uncertain while preparing the send. Keep
        # confirmed history, but debounce any new candidate from scratch.
        self._pending_house = None
        self._candidate = None
        self.state_set("power_state", "unknown")
        self.state_set("power_observation_gap", True)
        await self._status_update()
        return False

    async def _send_house(self, template: str, **context: Any) -> int | None:
        # Freeze text and identity across retries, including an ambiguous timeout.
        if self._pending_house is None or self._pending_house[0] != template:
            text = self.render(template, **context)
            self._pending_house = (template, text, getattr(self, "_delivery_key", None))
        _, text, key = self._pending_house

        async def still_current() -> bool:
            return await self._notification_is_current(context["house_state"])

        return await self.send_message(
            text, delivery_key=key, disable_notification=self.config.power.silent,
            before_send=still_current if template in ("power_lost", "power_restored") else None,
        )

    async def _phases(
        self,
        snapshot: dict,
        clock: float,
        deadlines: list[float],
        *,
        suppress: bool = False,
    ) -> None:
        if len(self._voltage_entities) < 2 or not self.config.power.phase_notifications:
            return
        for phase in snapshot["phases"]:
            entity, available = phase["entity_id"], phase["available"]
            key = self._phase_state_key(entity)
            previous = self.state_get(key)
            target = "on" if available else "off"
            if available is None or previous == target:
                self._phase_candidates.pop(entity, None)
                continue
            if previous is None:
                self.state_set(key, target)
                continue
            candidate = self._phase_candidates.get(entity)
            if candidate is None or candidate[0] != available:
                candidate = self._phase_candidates[entity] = (available, clock)
            delay = (
                self.config.power.restore_delay
                if available
                else self.config.power.loss_delay
            )
            if clock - candidate[1] < delay:
                deadlines.append(candidate[1] + delay)
                continue
            if (
                not suppress
                and snapshot["house_state"] != "off"
                and self.state_get("power_last_confirmed_state") != "off"
            ):
                delivered = await self.send_message(
                    self.render(
                        "phase_restored" if available else "phase_lost",
                        phase_label=phase["label"],
                        phases=snapshot["phases"],
                    ),
                    disable_notification=self.config.power.silent,
                )
                if delivered is None:
                    continue
            self.state_set(key, target)
            self._phase_candidates.pop(entity, None)

    async def _status_update(self) -> None:
        if self._status_message:
            await self._status_message.request_update()

    @staticmethod
    def _phase_state_key(entity_id: str) -> str:
        return f"phase_state::{entity_id}"

    @staticmethod
    def _calc_duration(last_change_iso: str | None, now: datetime) -> str:
        if not last_change_iso:
            return "—"
        try:
            return format_duration(
                max(0, (now - datetime.fromisoformat(last_change_iso)).total_seconds())
            )
        except (ValueError, TypeError):
            return "—"

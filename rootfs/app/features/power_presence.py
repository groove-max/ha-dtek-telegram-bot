"""Feature 5: House power presence and phase topology detection."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from features.base import Feature
from utils import format_duration, now_kyiv, parse_condition

if TYPE_CHECKING:
    from features.status_message import StatusMessageFeature
    from power_monitor import PowerMonitor


class PowerPresenceFeature(Feature):
    """Tracks house power as on, partial, or off.

    Modes:
    - dtek_only: binary DTEK sensor decides on/off, phases are informational only
    - voltage_only: aggregated voltage topology decides on/partial/off
    - loss_plus_voltage: loss_entity is a fast trigger, voltage topology is the
      source of truth for full house outages and restores
    """

    name = "power_presence"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._loss_delay_task: asyncio.Task[None] | None = None
        self._confirm_timeout_task: asyncio.Task[None] | None = None
        self._restore_delay_task: asyncio.Task[None] | None = None
        self._waiting_for_confirm = False
        self._confirm_started_at: datetime | None = None
        self._power_monitor: PowerMonitor | None = None
        self._status_message: StatusMessageFeature | None = None
        self._voltage_entities = [ve.entity for ve in self.config.voltage.entities if ve.entity]

    def set_power_monitor(self, pm: PowerMonitor) -> None:
        """Inject PowerMonitor dependency."""
        self._power_monitor = pm

    def set_status_message(self, sm: StatusMessageFeature) -> None:
        """Inject StatusMessage dependency for push updates."""
        self._status_message = sm

    @property
    def enabled(self) -> bool:
        return self.config.power.enabled

    def get_watched_entities(self) -> list[str]:
        entities: list[str] = []
        mode = self.config.power.mode

        if mode == "dtek_only":
            entities.append(self.entity("power"))
        else:
            entities.extend(self._voltage_entities)
            if mode == "loss_plus_voltage" and self.config.power.loss_entity:
                entities.append(self.config.power.loss_entity)

        if mode == "dtek_only" and len(self._voltage_entities) > 1:
            entities.extend(self._voltage_entities)

        return list(dict.fromkeys(entity for entity in entities if entity))

    async def on_start(self) -> None:
        """Initialize stored house and phase state from current HA state."""
        actual_power = (
            await self._power_monitor.detect_current_power()
            if self._power_monitor
            else "on"
        )
        stored = self.state_get("power_state")
        if stored != actual_power:
            self.log.info("Power state sync: stored=%s, actual=%s", stored, actual_power)
            self.state_set("power_state", actual_power)
            if self.state_get("power_last_change") is None:
                self.state_set("power_last_change", now_kyiv().isoformat())

        await self._seed_phase_states()

    async def on_tick(self) -> None:
        """Reconcile stored state with current HA state."""
        if not self._power_monitor:
            return

        stored = self.state_get("power_state")
        if stored not in ("on", "partial", "off"):
            return

        actual = await self._power_monitor.detect_current_power()
        if actual == stored:
            return

        self.log.warning(
            "Power state mismatch detected: stored=%s, actual=%s; reconciling",
            stored,
            actual,
        )

        await self._apply_house_state(actual, send_transition_message=(stored == "off" or actual == "off"))

    async def on_stop(self) -> None:
        """Cancel any pending timers during shutdown."""
        self._cancel_task("_loss_delay_task")
        self._cancel_task("_confirm_timeout_task")
        self._cancel_task("_restore_delay_task")
        self._waiting_for_confirm = False
        self._confirm_started_at = None

    async def on_state_change(
        self, entity_id: str, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        mode = self.config.power.mode

        if entity_id in self._voltage_entities and len(self._voltage_entities) > 1:
            await self._handle_phase_change(entity_id, new_state)

        if mode == "dtek_only":
            await self._handle_dtek_only(entity_id, old_state, new_state)
            return

        if mode == "loss_plus_voltage" and entity_id == self.config.power.loss_entity:
            await self._handle_loss_entity_change(new_state)
            return

        if entity_id in self._voltage_entities:
            await self._handle_voltage_topology_change()

    async def _handle_dtek_only(
        self,
        entity_id: str,
        old_state: dict[str, Any],
        new_state: dict[str, Any],
    ) -> None:
        if entity_id != self.entity("power"):
            return

        old_val = self.get_state_value(old_state)
        new_val = self.get_state_value(new_state)
        if new_val in ("unavailable", "unknown") or old_val == new_val:
            return

        if new_val == "off":
            await self._on_power_lost()
        elif new_val == "on":
            await self._on_power_restored(target_state="on")

    async def _handle_loss_entity_change(self, new_state: dict[str, Any]) -> None:
        """Use loss_entity as a fast trigger for full-house loss confirmation."""
        if not self._power_monitor:
            return

        loss_met = parse_condition(
            self.config.power.loss_state,
            self.get_state_value(new_state),
        ) or self.get_state_value(new_state) in ("unavailable", "unknown")

        if not loss_met:
            if self._waiting_for_confirm:
                self._clear_confirm_wait("Loss entity recovered, confirmation cancelled")
            self.state_set("loss_stale_reference_at", None)
            return

        voltage_state = await self._power_monitor.get_restore_ready_state()
        if voltage_state == "off":
            self._schedule_loss_delay()
            return

        if not self._waiting_for_confirm:
            self._waiting_for_confirm = True
            self._confirm_started_at = now_kyiv()
            self._schedule_confirm_timeout()
            self.log.info(
                "Loss entity triggered, waiting up to %ss for a fresh voltage sample or explicit outage",
                self.config.power.confirm_timeout,
            )

    async def _handle_voltage_topology_change(self) -> None:
        """React to voltage topology changes for house-state detection."""
        if not self._power_monitor:
            return

        stored = self.state_get("power_state", "on")
        snapshot = await self._power_monitor.get_voltage_snapshot()
        actual = await self._power_monitor.detect_current_power()

        if self._waiting_for_confirm and actual == "off":
            self._clear_confirm_wait()
            self._schedule_loss_delay()
            return

        if self._waiting_for_confirm and self._has_fresh_voltage_sample(snapshot):
            self._clear_confirm_wait("Fresh voltage data arrived after loss trigger; treating it as a false alarm")
            self.state_set("loss_stale_reference_at", None)
            if stored != actual:
                await self._apply_house_state(actual, send_transition_message=False)
            return

        if stored == "off":
            if actual != "off":
                self._schedule_restore_delay()
            else:
                self._cancel_task("_restore_delay_task")
            return

        if actual == "off":
            self._schedule_loss_delay()
            return

        self._cancel_task("_loss_delay_task")
        if stored != actual:
            await self._apply_house_state(actual, send_transition_message=False)

    def _schedule_loss_delay(self) -> None:
        """Debounce a full house outage before notifying."""
        if self._loss_delay_task and not self._loss_delay_task.done():
            return

        async def _delayed() -> None:
            await asyncio.sleep(self.config.power.loss_delay)
            if not self._power_monitor:
                return
            actual = await self._power_monitor.detect_current_power()
            if actual == "off":
                await self._on_power_lost()

        self._loss_delay_task = asyncio.create_task(_delayed())

    def _schedule_confirm_timeout(self) -> None:
        """Wait for a fresh voltage sample before treating cached voltage as stale."""
        self._cancel_task("_confirm_timeout_task")

        async def _timeout() -> None:
            await asyncio.sleep(self.config.power.confirm_timeout)
            if (
                not self._waiting_for_confirm
                or not self._power_monitor
                or self._confirm_started_at is None
            ):
                return
            snapshot = await self._power_monitor.get_voltage_snapshot()
            if self._has_fresh_voltage_sample(snapshot):
                self._clear_confirm_wait(
                    "Fresh voltage data arrived within the confirmation window; power loss ignored"
                )
                self.state_set("loss_stale_reference_at", None)
                return

            self.state_set("loss_stale_reference_at", self._confirm_started_at.isoformat())
            self._clear_confirm_wait(
                "No fresh voltage data arrived within the confirmation window; cached voltage is now considered stale"
            )
            actual = await self._power_monitor.detect_current_power()
            if actual == "off":
                self._schedule_loss_delay()
            else:
                self.state_set("loss_stale_reference_at", None)
                self.log.info("Confirmation timeout elapsed but house did not go fully off")

        self._confirm_timeout_task = asyncio.create_task(_timeout())

    def _schedule_restore_delay(self) -> None:
        """Debounce restore from off to partial/on."""
        if self._restore_delay_task and not self._restore_delay_task.done():
            return

        async def _delayed() -> None:
            await asyncio.sleep(self.config.power.restore_delay)
            if not self._power_monitor:
                return
            actual = await self._power_monitor.detect_current_power()
            if actual != "off":
                await self._on_power_restored(target_state=actual)

        self._restore_delay_task = asyncio.create_task(_delayed())

    def _cancel_task(self, attr: str) -> None:
        """Cancel an async task by attribute name."""
        task = getattr(self, attr, None)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
        setattr(self, attr, None)

    async def _on_power_lost(self) -> None:
        """Handle full house outage."""
        if self.state_get("power_state") == "off":
            return

        self._waiting_for_confirm = False
        self._confirm_started_at = None
        self._cancel_task("_loss_delay_task")
        self._cancel_task("_confirm_timeout_task")
        self._cancel_task("_restore_delay_task")

        now = now_kyiv()
        last_change_iso = self.state_get("power_last_change")
        duration_str = self._calc_duration(last_change_iso, now)

        self.state_set("power_state", "off")
        self.state_set("power_last_change", now.isoformat())

        text = self.render(
            "power_lost",
            duration=duration_str,
            house_state="off",
        )
        await self.send_message(
            text,
            disable_notification=self.config.power.silent,
        )
        self.log.info("House power lost (was available for %s)", duration_str)

        if self._status_message:
            await self._status_message.request_update()

    async def _on_power_restored(self, target_state: str) -> None:
        """Handle restore from off to partial or on."""
        if self.state_get("power_state") != "off":
            return
        await self._apply_house_state(target_state, send_transition_message=True)

    async def _apply_house_state(
        self,
        target_state: str,
        *,
        send_transition_message: bool,
    ) -> None:
        """Apply a new house state and notify if this crosses off <-> available."""
        current = self.state_get("power_state", "on")
        if current == target_state:
            return

        self._waiting_for_confirm = False
        self._confirm_started_at = None
        self._cancel_task("_loss_delay_task")
        self._cancel_task("_confirm_timeout_task")
        self._cancel_task("_restore_delay_task")

        now = now_kyiv()
        last_change_iso = self.state_get("power_last_change")
        duration_str = self._calc_duration(last_change_iso, now)

        if current == "off" and target_state in ("partial", "on"):
            self.state_set("loss_stale_reference_at", None)
            self.state_set("power_state", target_state)
            self.state_set("power_last_change", now.isoformat())
            await self._send_restore_message(duration_str, target_state)
            return

        if current in ("on", "partial") and target_state == "off":
            await self._on_power_lost()
            return

        self.state_set("power_state", target_state)
        if target_state != "off":
            self.state_set("loss_stale_reference_at", None)
        if send_transition_message and target_state in ("partial", "on"):
            await self._send_restore_message(duration_str, target_state)
        else:
            self.log.info("House power detail changed: %s -> %s", current, target_state)
            if self._status_message:
                await self._status_message.request_update()

    async def _send_restore_message(self, duration_str: str, target_state: str) -> None:
        voltage = None
        phases = None
        missing_phases: list[str] = []
        unknown_phases: list[str] = []

        if self._power_monitor:
            snapshot = await self._power_monitor.get_voltage_snapshot()
            voltage = snapshot.get("single_voltage")
            phases = snapshot.get("phases") or None
            missing_phases = snapshot.get("missing_phases") or []
            unknown_phases = snapshot.get("unknown_phases") or []

        text = self.render(
            "power_restored",
            duration=duration_str,
            voltage=voltage,
            phases=phases,
            house_state=target_state,
            missing_phases=missing_phases,
            unknown_phases=unknown_phases,
        )
        await self.send_message(
            text,
            disable_notification=self.config.power.silent,
        )
        self.log.info("House power restored to %s (was off for %s)", target_state, duration_str)

        if self._status_message:
            await self._status_message.request_update()

    async def _seed_phase_states(self) -> None:
        """Seed persisted phase availability from current voltage snapshot."""
        if len(self._voltage_entities) <= 1 or not self._power_monitor:
            return

        snapshot = await self._power_monitor.get_voltage_snapshot()
        for phase in snapshot.get("phases", []):
            if phase["available"] is None:
                continue
            self.state_set(
                self._phase_state_key(str(phase["entity_id"])),
                "on" if phase["available"] else "off",
            )

    async def _handle_phase_change(
        self,
        entity_id: str,
        new_state: dict[str, Any],
    ) -> None:
        """Send per-phase messages for multi-phase configurations."""
        if len(self._voltage_entities) <= 1 or not self._power_monitor:
            return
        if not self.config.power.phase_notifications:
            return

        key = self._phase_state_key(entity_id)
        was_available = self.state_get(key, "on") == "on"
        is_available = self._phase_available(self.get_state_value(new_state))
        if is_available is None:
            return
        if was_available == is_available:
            return

        snapshot = await self._power_monitor.get_voltage_snapshot()
        current_house_state = self.state_get("power_state", "on")
        phase_label = self._power_monitor.phase_label_for_entity(entity_id)

        self.state_set(key, "on" if is_available else "off")

        if not is_available:
            if snapshot.get("house_state") == "off":
                return
            text = self.render(
                "phase_lost",
                phase_label=phase_label,
                phases=snapshot.get("phases", []),
            )
            await self.send_message(
                text,
                disable_notification=self.config.power.silent,
            )
            self.log.info("Phase %s lost", phase_label)
        else:
            if current_house_state == "off":
                return
            text = self.render(
                "phase_restored",
                phase_label=phase_label,
                phases=snapshot.get("phases", []),
            )
            await self.send_message(
                text,
                disable_notification=self.config.power.silent,
            )
            self.log.info("Phase %s restored", phase_label)

        if self._status_message:
            await self._status_message.request_update()

    def _phase_available(self, value: str) -> bool | None:
        if value in ("unavailable", "unknown", ""):
            if bool(getattr(self.config.voltage, "unavailable_as_missing", True)):
                return False
            return None
        try:
            return float(value) >= self.config.voltage.present_above
        except (ValueError, TypeError):
            if bool(getattr(self.config.voltage, "unavailable_as_missing", True)):
                return False
            return None

    @staticmethod
    def _phase_state_key(entity_id: str) -> str:
        return f"phase_state::{entity_id}"

    @staticmethod
    def _calc_duration(last_change_iso: str | None, now: datetime) -> str:
        """Calculate duration string from last off/on boundary to now."""
        if not last_change_iso:
            return "—"
        try:
            last_dt = datetime.fromisoformat(last_change_iso)
            seconds = (now - last_dt).total_seconds()
            return format_duration(seconds)
        except (ValueError, TypeError):
            return "—"

    def _clear_confirm_wait(self, reason: str | None = None) -> None:
        self._waiting_for_confirm = False
        self._confirm_started_at = None
        self._cancel_task("_confirm_timeout_task")
        if reason:
            self.log.info(reason)

    def _has_fresh_voltage_sample(self, snapshot: dict[str, Any]) -> bool:
        if self._confirm_started_at is None:
            return False
        for phase in snapshot.get("phases", []):
            if phase.get("available") is not True:
                continue
            last_updated_raw = phase.get("last_updated")
            if not last_updated_raw:
                continue
            try:
                last_updated = datetime.fromisoformat(str(last_updated_raw))
            except (TypeError, ValueError):
                continue
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=self._confirm_started_at.tzinfo)
            else:
                last_updated = last_updated.astimezone(self._confirm_started_at.tzinfo)
            if last_updated >= self._confirm_started_at:
                return True
        return False

"""Feature 4: Voltage quality monitoring."""

from __future__ import annotations

import asyncio
from typing import Any

from features.base import Feature


class VoltageFeature(Feature):
    """Monitors voltage sensors and alerts on low/high values.

    Supports single-phase and multi-phase configurations.
    Sends alerts when voltage is outside [voltage_low, voltage_high] range
    for voltage_delay seconds. Sends normalization message when voltage
    returns to normal after an alert.
    """

    name = "voltage"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # entity -> label mapping
        self._entity_labels: dict[str, str] = {}
        # entity -> pending alert task (for delay)
        self._pending_alerts: dict[str, asyncio.Task[None]] = {}
        for ve in self.config.voltage.entities:
            self._entity_labels[ve.entity] = ve.label

    @staticmethod
    def _alert_keys(entity_id: str) -> tuple[str, str]:
        return f"voltage_alert_{entity_id}", f"voltage_alert_type_{entity_id}"

    def _get_active_alert_type(
        self,
        entity_id: str,
        voltage: float,
        low: float,
        high: float,
    ) -> str | None:
        alert_key, alert_type_key = self._alert_keys(entity_id)
        if not self.state_get(alert_key, False):
            return None

        active_type = self.state_get(alert_type_key, "")
        if active_type in {"low", "high"}:
            return active_type

        # Compatibility with states written by older versions that only stored
        # the boolean active flag but not the alert direction.
        if voltage < low:
            return "low"
        if voltage > high:
            return "high"

        self._clear_alert_state(entity_id)
        return None

    def _has_recovered(self, alert_type: str, voltage: float) -> bool:
        hysteresis = self.config.voltage.hysteresis
        if alert_type == "low":
            return voltage >= (self.config.voltage.low + hysteresis)
        return voltage <= (self.config.voltage.high - hysteresis)

    def _clear_alert_state(self, entity_id: str) -> None:
        alert_key, alert_type_key = self._alert_keys(entity_id)
        self.state_set(alert_key, False)
        self.state_set(alert_type_key, "")

    @property
    def enabled(self) -> bool:
        return self.config.voltage.enabled and len(self.config.voltage.entities) > 0

    def get_watched_entities(self) -> list[str]:
        return [ve.entity for ve in self.config.voltage.entities]

    async def on_state_change(
        self, entity_id: str, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        new_val = self.get_state_value(new_state)
        if new_val in ("unavailable", "unknown", ""):
            return

        try:
            voltage = float(new_val)
        except (ValueError, TypeError):
            return

        label = self._entity_labels.get(entity_id, "")

        low = self.config.voltage.low
        high = self.config.voltage.high
        active_type = self._get_active_alert_type(entity_id, voltage, low, high)

        if active_type is not None:
            self._cancel_pending(entity_id)
            if self._has_recovered(active_type, voltage):
                await self._send_normalized(label, voltage)
                self._clear_alert_state(entity_id)
            else:
                return

        if voltage < low:
            self._schedule_alert(entity_id, label, voltage, "low")
        elif voltage > high:
            self._schedule_alert(entity_id, label, voltage, "high")
        else:
            self._cancel_pending(entity_id)

    def _schedule_alert(
        self, entity_id: str, label: str, voltage: float, alert_type: str
    ) -> None:
        """Schedule a delayed alert (voltage_delay seconds)."""
        self._cancel_pending(entity_id)

        async def _delayed_alert() -> None:
            await asyncio.sleep(self.config.voltage.delay)
            # Re-check current value
            state = await self.ha.get_state(entity_id)
            if state is None:
                return
            try:
                current = float(self.get_state_value(state))
            except (ValueError, TypeError):
                return

            low = self.config.voltage.low
            high = self.config.voltage.high
            still_bad = (alert_type == "low" and current < low) or (
                alert_type == "high" and current > high
            )

            if still_bad:
                alert_key, alert_type_key = self._alert_keys(entity_id)
                self.state_set(alert_key, True)
                self.state_set(alert_type_key, alert_type)
                await self._send_alert(label, current, alert_type)

        self._pending_alerts[entity_id] = asyncio.create_task(_delayed_alert())

    def _cancel_pending(self, entity_id: str) -> None:
        """Cancel a pending alert task if any."""
        task = self._pending_alerts.pop(entity_id, None)
        if task and not task.done():
            task.cancel()

    async def _send_alert(self, label: str, voltage: float, alert_type: str) -> None:
        """Send voltage alert message."""
        template = "voltage_low" if alert_type == "low" else "voltage_high"
        text = self.render(template, voltage=voltage, phase_label=label)
        await self.send_message(
            text,
            disable_notification=self.config.voltage.silent,
        )
        self.log.info(
            "Voltage %s alert: %.1f V (phase: %s)", alert_type, voltage, label or "—"
        )

    async def _send_normalized(self, label: str, voltage: float) -> None:
        """Send voltage normalized message."""
        text = self.render("voltage_normal", voltage=voltage, phase_label=label)
        await self.send_message(
            text,
            disable_notification=self.config.voltage.silent,
        )
        self.log.info("Voltage normalized: %.1f V (phase: %s)", voltage, label or "—")

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
        alert_key = f"voltage_alert_{entity_id}"
        is_alert_active = self.state_get(alert_key, False)

        low = self.config.voltage.low
        high = self.config.voltage.high

        if voltage < low:
            if not is_alert_active:
                self._schedule_alert(entity_id, label, voltage, "low")
        elif voltage > high:
            if not is_alert_active:
                self._schedule_alert(entity_id, label, voltage, "high")
        else:
            # Voltage is normal
            self._cancel_pending(entity_id)
            if is_alert_active:
                await self._send_normalized(label, voltage)
                self.state_set(alert_key, False)

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
                alert_key = f"voltage_alert_{entity_id}"
                self.state_set(alert_key, True)
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

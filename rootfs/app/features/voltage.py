"""Feature 4: Voltage quality monitoring."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from typing import Any
from uuid import uuid4

from features.base import Feature
from power_monitor import PowerMonitor
from utils import now_kyiv


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
        self._pending_types: dict[str, str] = {}
        self._pending_alerts: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        for ve in self.config.voltage.entities:
            self._entity_labels[ve.entity] = ve.label if len(self.config.voltage.entities) > 1 else ""

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

    def _phase_is_present(self, voltage: float) -> bool:
        """Return whether a voltage sample means the phase is electrically present."""
        return voltage >= getattr(self.config.voltage, "present_above", 50.0)

    def _clear_alert_state(self, entity_id: str) -> None:
        alert_key, alert_type_key = self._alert_keys(entity_id)
        self.state_set(alert_key, False)
        self.state_set(alert_type_key, "")
        self.state_set(f"voltage_incident_{entity_id}", None)

    def _incident(self, entity_id: str) -> dict:
        incident = self.state_get(f"voltage_incident_{entity_id}")
        if incident:
            return incident
        # Legacy active flags were written before delivery. Only the journal
        # can establish that their warning actually reached this recipient.
        legacy_key = f"{self.state_get('delivery_identity_voltage')}:{self.state_get('delivery_revision_voltage', 0)}"
        prior = self._delivery(legacy_key)
        context = {}
        if prior and prior[0] == "ok":
            digest = hashlib.sha256(f"{self.effective_telegram_chat_id}:{legacy_key}".encode()).hexdigest()
            row = self.tg.journal.db.execute("SELECT payload FROM deliveries WHERE key=?", (digest,)).fetchone()
            context = json.loads(row[0]).get("context", {}) if row else {}
        notified = bool(context.get("stream") == entity_id and
                        context.get("guards", {}).get(f"voltage_observation_{entity_id}") in ("low", "high"))
        incident = {"id": uuid4().hex, "notified": notified}
        self.state_set(f"voltage_incident_{entity_id}", incident)
        return incident

    def _incident_key(self, entity_id: str, stage: str) -> str:
        return f"voltage:{self.profile_key}:{entity_id}:{self._incident(entity_id)['id']}:{stage}"

    def _delivery(self, key):
        journal = getattr(self.tg, "journal", None)
        if journal is None:
            return None
        digest = hashlib.sha256(f"{self.effective_telegram_chat_id}:{key}".encode()).hexdigest()
        return journal.delivery(digest)

    def _alert_was_delivered(self, entity_id: str) -> bool:
        incident = self._incident(entity_id)
        if incident["notified"]:
            return True
        record = self._delivery(self._incident_key(entity_id, "alert"))
        return bool(record and record[0] == "ok")

    async def _save(self):
        save = getattr(self.state, "save", None)
        if save:
            await save()

    @property
    def enabled(self) -> bool:
        return self.config.voltage.enabled and len(self.config.voltage.entities) > 0

    def get_watched_entities(self) -> list[str]:
        return [ve.entity for ve in self.config.voltage.entities]

    async def on_state_change(
        self, entity_id: str, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        async with self._locks.setdefault(entity_id, asyncio.Lock()):
            await self._observe(entity_id, new_state)

    async def on_start(self):
        await self.on_tick()

    async def on_tick(self):
        for entity in self._entity_labels:
            async with self._locks.setdefault(entity, asyncio.Lock()):
                await self._observe(entity, await self.ha.get_state(entity) or {})

    async def _observe(self, entity_id, new_state):
        guard_key = f"voltage_observation_{entity_id}"
        if not self._is_fresh(new_state):
            self.state_set(guard_key, "unknown")
            self._cancel_pending(entity_id)
            return
        new_val = self.get_state_value(new_state)
        sample_time = PowerMonitor._parse_state_datetime(
            new_state.get("last_reported") or new_state.get("last_updated")
        )
        self.state_set(
            f"voltage_expires_{entity_id}",
            sample_time.timestamp()
            + getattr(self.config.voltage, "max_age_seconds", 600),
        )
        if new_val in ("unavailable", "unknown", ""):
            self.state_set(guard_key, "unknown")
            self._cancel_pending(entity_id)
            return

        try:
            voltage = float(new_val)
        except (ValueError, TypeError):
            self.state_set(guard_key, "unknown")
            self._cancel_pending(entity_id)
            return

        if not math.isfinite(voltage):
            self.state_set(guard_key, "unknown")
            self._cancel_pending(entity_id)
            return

        label = self._entity_labels.get(entity_id, "")

        if not self._phase_is_present(voltage):
            self.state_set(guard_key, "missing")
            self._cancel_pending(entity_id)
            self._clear_alert_state(entity_id)
            self.log.debug(
                "Voltage quality ignored because phase %s is missing: %.1f V",
                label or entity_id,
                voltage,
            )
            return

        low = self.config.voltage.low
        high = self.config.voltage.high
        active_type = self._get_active_alert_type(entity_id, voltage, low, high)
        quality = "low" if voltage < low else "high" if voltage > high else "normal"
        self.state_set(guard_key, quality)

        if active_type is not None:
            self._cancel_pending(entity_id)
            if quality == "normal" and self._has_recovered(active_type, voltage):
                if self._alert_was_delivered(entity_id):
                    if not await self._send_normalized(entity_id, label, voltage):
                        return
                self._clear_alert_state(entity_id)
                await self._save()
            elif quality in ("low", "high") and quality != active_type:
                # Jumping from one extreme to the other is not normalization.
                self._clear_alert_state(entity_id)
            else:
                if quality == active_type and not self._alert_was_delivered(entity_id):
                    await self._send_alert(entity_id, label, voltage, active_type)
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
        pending = self._pending_alerts.get(entity_id)
        if (
            pending
            and not pending.done()
            and self._pending_types.get(entity_id) == alert_type
        ):
            return
        self._cancel_pending(entity_id)
        self._pending_types[entity_id] = alert_type

        async def _delayed_alert() -> None:
            try:
                await asyncio.sleep(self.config.voltage.delay)
                async with self._locks.setdefault(entity_id, asyncio.Lock()):
                    state = await self.ha.get_state(entity_id)
                    current = self._valid_voltage(state)
                    if current is None:
                        return
                    if (alert_type == "low" and current < self.config.voltage.low) or (
                        alert_type == "high" and current > self.config.voltage.high
                    ):
                        sample_time = PowerMonitor._parse_state_datetime(state.get("last_reported") or state.get("last_updated"))
                        self.state_set(f"voltage_expires_{entity_id}", sample_time.timestamp() + getattr(self.config.voltage, "max_age_seconds", 600))
                        self.state_set(f"voltage_observation_{entity_id}", alert_type)
                        alert_key, alert_type_key = self._alert_keys(entity_id)
                        self.state_set(f"voltage_incident_{entity_id}", {"id": uuid4().hex, "notified": False})
                        self.state_set(alert_key, True)
                        self.state_set(alert_type_key, alert_type)
                        await self._send_alert(entity_id, label, current, alert_type)
            finally:
                if self._pending_alerts.get(entity_id) is asyncio.current_task():
                    self._pending_alerts.pop(entity_id, None)
                    self._pending_types.pop(entity_id, None)

        self._pending_alerts[entity_id] = asyncio.create_task(_delayed_alert())

    async def on_stop(self) -> None:
        tasks = list(self._pending_alerts.values())
        for entity in list(self._pending_alerts):
            self._cancel_pending(entity)
        await asyncio.gather(*tasks, return_exceptions=True)

    def _cancel_pending(self, entity_id: str) -> None:
        """Cancel a pending alert task if any."""
        task = self._pending_alerts.pop(entity_id, None)
        self._pending_types.pop(entity_id, None)
        if task and not task.done():
            task.cancel()

    def _is_fresh(self, state: dict) -> bool:
        if state.get("attributes", {}).get("restored"):
            return False
        timestamp = PowerMonitor._parse_state_datetime(
            state.get("last_reported") or state.get("last_updated")
        )
        return timestamp is not None and 0 <= (
            now_kyiv() - timestamp
        ).total_seconds() <= getattr(self.config.voltage, "max_age_seconds", 600)

    def _valid_voltage(self, state) -> float | None:
        if not state or not self._is_fresh(state):
            return None
        try:
            voltage = float(self.get_state_value(state))
        except (ValueError, TypeError):
            return None
        return voltage if math.isfinite(voltage) and self._phase_is_present(voltage) else None

    async def _still_relevant(self, entity_id, stage):
        state = await self.ha.get_state(entity_id)
        voltage = self._valid_voltage(state)
        if voltage is None:
            return False
        if stage == "normal":
            active = self.state_get(self._alert_keys(entity_id)[1])
            relevant = (self.config.voltage.low <= voltage <= self.config.voltage.high
                        and active in ("low", "high") and self._has_recovered(active, voltage))
        else:
            relevant = voltage < self.config.voltage.low if stage == "low" else voltage > self.config.voltage.high
        if relevant:
            self.state_set(f"voltage_observation_{entity_id}", stage)
        return relevant

    def _delivery_context(self, entity_id: str) -> dict:
        key = f"voltage_observation_{entity_id}"
        return {
            "stream": entity_id,
            "guards": {key: self.state_get(key)},
            "expires": self.state_get(f"voltage_expires_{entity_id}"),
        }

    async def _send_alert(
        self, entity_id: str, label: str, voltage: float, alert_type: str
    ) -> int | None:
        """Send voltage alert message."""
        template = "voltage_low" if alert_type == "low" else "voltage_high"
        text = await self.async_render(template, voltage=voltage, phase_label=label)
        delivered = await self.send_message(
            text,
            delivery_key=self._incident_key(entity_id, "alert"),
            before_send=lambda: self._still_relevant(entity_id, alert_type),
            delivery_context=self._delivery_context(entity_id),
            disable_notification=self.config.voltage.silent,
        )
        if delivered:
            self.state_set(f"voltage_incident_{entity_id}", {**self._incident(entity_id), "notified": True})
            await self._save()
            self.log.info("Voltage %s alert: %.1f V (phase: %s)", alert_type, voltage, label or "—")
        return delivered

    async def _send_normalized(
        self, entity_id: str, label: str, voltage: float
    ) -> bool:
        """Send voltage normalized message."""
        text = await self.async_render("voltage_normal", voltage=voltage, phase_label=label)
        key = self._incident_key(entity_id, "normal")
        delivered = await self.send_message(
            text,
            delivery_key=key,
            before_send=lambda: self._still_relevant(entity_id, "normal"),
            delivery_context=self._delivery_context(entity_id),
            disable_notification=getattr(self.config.voltage, "normal_silent", True),
        )
        if delivered:
            self.log.info("Voltage normalized: %.1f V (phase: %s)", voltage, label or "—")
        record = self._delivery(key)
        return bool(delivered or (record and record[0] in ("unknown", "rejected", "expired", "superseded")))

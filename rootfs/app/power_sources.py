"""Normalize independent observations of the same grid supply (not phases)."""

from __future__ import annotations

import math
from time import monotonic
from datetime import datetime
from typing import Any

from config import PowerConfig, PowerSourceConfig
from utils import now_kyiv


def timestamp(value: Any) -> datetime | None:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return result if result.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def observation(source: PowerSourceConfig, state: dict | None, now: datetime) -> dict:
    """Unknown/absent telemetry is never a numeric zero or a binary off."""
    stamp = timestamp((state or {}).get("last_reported") or (state or {}).get("last_updated"))
    raw = str((state or {}).get("state", ""))
    result = {"entity_id": source.entity, "kind": source.kind, "state": "unknown",
              "reason": "unavailable", "raw_state": raw,
              "reported_at": stamp.isoformat() if stamp else None}
    if not state or state.get("attributes", {}).get("restored"):
        return result
    age = (now - stamp).total_seconds() if stamp else None
    if age is None or age < -5:
        result["reason"] = "invalid_timestamp"
        return result
    if (source.freshness == "heartbeat" or source.mqtt_topic) and age > source.max_age_seconds:
        result["reason"] = "stale"
        return result
    if raw in ("unknown", "unavailable", "", "None"):
        return result
    if source.kind == "binary":
        if raw not in ("on", "off"):
            result["reason"] = "invalid_value"
            return result
        present = raw == source.present_state
    else:
        try:
            value = float(raw)
        except (ValueError, TypeError):
            value = math.nan
        if not math.isfinite(value) or value < 0:
            result["reason"] = "invalid_value"
            return result
        attrs = state.get("attributes", {})
        unit = attrs.get("unit_of_measurement")
        # HA state values are already expressed in their declared unit. Do not
        # mistake kV/mV for V; let the user choose a correctly converted entity.
        expected = "V" if source.kind == "voltage" else "Hz"
        if unit and str(unit).lower() != expected.lower():
            result["reason"] = "unit_mismatch"
            return result
        present = value >= source.present_above
    result.update(state="on" if present else "off", reason="reported")
    return result


class PowerSources:
    """Combine explicit sources; keep grace windows in memory across polls only."""

    def __init__(self, config: PowerConfig, ha: Any) -> None:
        self.config = config
        self.ha = ha
        self._loss_started: tuple[datetime, float] | None = None
        self._last_on: dict[str, datetime] = {}
        self._connection: Any = None
        self._can_sleep = False
        self._secondary_off: datetime | None = None

    def reset(self) -> None:
        self._loss_started = None
        self._last_on.clear()
        self._can_sleep = False
        self._secondary_off = None

    async def _read(self, source: PowerSourceConfig) -> dict | None:
        raw = await self.ha.get_state(source.entity)
        if not source.mqtt_topic:
            return raw
        live = self.ha.mqtt_observation(source.mqtt_topic)
        if live is None or raw is None:
            return None
        # Retained/replayed MQTT messages are filtered by HAClient. HA can
        # serialize the same numeric report as 230 while MQTT sends 230.0.
        try:
            agrees = float(live.get("state")) == float(raw.get("state"))
        except (ValueError, TypeError):
            agrees = live.get("state") == raw.get("state")
        if not agrees:
            return None
        return {**live, "attributes": raw.get("attributes", {})}

    async def snapshot(self) -> dict:
        now = now_kyiv()
        connection = getattr(self.ha, "connected_at", None)
        if connection != self._connection:
            self.reset()
            self._connection = connection
        if not getattr(self.ha, "is_connected", True):
            self.reset()
            return {"state": "unknown", "reason": "ha_disconnected", "sources": []}
        observations = []
        states = []
        for source in self.config.sources:
            raw = await self._read(source)
            item = observation(source, raw, now)
            floor = timestamp(connection)
            if source.freshness == "heartbeat" and floor and timestamp(item["reported_at"]) and timestamp(item["reported_at"]) < floor:
                item.update(state="unknown", reason="awaiting_report")
            if item["state"] == "on":
                self._last_on[source.entity] = timestamp(item["reported_at"])
            observations.append(item)
            states.append(raw)

        result = {"state": "unknown", "reason": "insufficient_data", "sources": observations}
        if len(observations) > 1 and observations[0]["state"] in ("on", "off"):
            # REST requests are not atomic. A witness which changed while we
            # fetched the secondary must not confirm an obsolete transition.
            current = observation(self.config.sources[0], await self._read(self.config.sources[0]), now_kyiv())
            if current["state"] != observations[0]["state"]:
                self.reset()
                result["reason"] = "sources_changed"
                return result
        if not getattr(self.ha, "is_connected", True) or connection != getattr(self.ha, "connected_at", None):
            self.reset()
            result["reason"] = "ha_disconnected"
            return result
        if not observations:
            self.reset()
            return result
        primary = observations[0]
        if primary["state"] != "off":
            self._loss_started = None
            self._can_sleep = False
            self._secondary_off = None
        elif self._loss_started is None:
            self._loss_started = (now, monotonic())
            secondary = observations[1] if len(observations) == 2 else None
            self._can_sleep = bool(secondary and (
                secondary["state"] == "on" or (
                    secondary["reason"] == "unavailable"
                    and timestamp(secondary["reported_at"]) is not None
                    and timestamp(primary["reported_at"]) is not None
                    and timestamp(secondary["reported_at"]) >= timestamp(primary["reported_at"])
                )
            ))

        if len(observations) == 2 and self._loss_started:
            secondary = observations[1]
            source = self.config.sources[1]
            started, clock = self._loss_started
            last_on = self._last_on.get(source.entity)
            stamp = timestamp(secondary["reported_at"])
            if secondary["state"] == "off":
                self._secondary_off = stamp
            elif secondary["state"] == "on":
                self._secondary_off = None
            confirmed_before_sleep = (
                self._secondary_off is not None
                and secondary["reason"] in ("stale", "unavailable")
                and stamp is not None and stamp >= self._secondary_off
            )
            # Only a declared mains-powered device which was observed alive
            # before this loss can corroborate it by going silent. A newer
            # positive report contradicts the witness, even if it later ages.
            may_sleep = (
                source.availability == "mains_powered"
                and (confirmed_before_sleep or (
                    self._can_sleep and last_on is not None and last_on <= started
                ))
                and states[1] is not None
                and not states[1].get("attributes", {}).get("restored")
                and stamp is not None
                and (confirmed_before_sleep or stamp <= started or secondary["raw_state"] in ("unavailable", "unknown"))
                and secondary["reason"] not in ("invalid_value", "invalid_timestamp", "unit_mismatch", "awaiting_report")
            )
            elapsed = monotonic() - clock
            if may_sleep and elapsed >= self.config.confirm_timeout:
                secondary = {**secondary, "state": "off", "reason": "silent_after_confirmed_loss"}
                observations[1] = secondary
            elif may_sleep:
                result["pending_seconds"] = max(0, self.config.confirm_timeout - elapsed)

        values = {item["state"] for item in observations}
        if len(values) == 1 and "unknown" not in values:
            result.update(state=next(iter(values)), reason="agreement")
        elif "on" in values and "off" in values:
            result["reason"] = "conflicting_sources"
        return result

"""Centralized power, voltage, and phase topology service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Any

from config import AddressConfig
from ha_client import HAClient
from state_store import StateStore
from utils import now_kyiv, parse_condition
from power_sources import PowerSources

logger = logging.getLogger(__name__)


class PowerMonitor:
    """Provides derived house power state and voltage topology for one address."""

    def __init__(
        self,
        config: AddressConfig,
        ha: HAClient,
        state: StateStore,
    ) -> None:
        self._config = config
        self._ha = ha
        self._state = state
        self._prefix = getattr(config, "key", config.entity_prefix)
        self._loss_fresh_after = getattr(ha, "connected_at", None) or now_kyiv()
        self.sources = PowerSources(config.power, ha)

    async def get_power_state(self, *, prefer_stored: bool = True) -> str:
        """Return current house state: on, partial, or off."""
        if not getattr(self._ha, "is_connected", True):
            return "unknown"
        if prefer_stored and self._config.power.enabled:
            stored = self._state.get(self._prefix, "power_state")
            if stored in ("on", "partial", "off", "unknown"):
                return str(stored)
        return await self.detect_current_power()

    async def is_on(self) -> bool:
        """Check if the house has at least some power."""
        return await self.get_power_state() in ("on", "partial")

    async def detect_current_power(self) -> str:
        """Detect current power state directly from HA entities."""
        if not getattr(self._ha, "is_connected", True):
            return "unknown"
        mode = self._config.power.mode if self._config.power.enabled else "dtek_only"

        if mode == "dtek_only":
            return await self._detect_dtek_power()

        if mode == "sources":
            return (await self.sources.snapshot())["state"]

        if mode in ("voltage_only", "loss_plus_voltage"):
            loss_met = (
                await self.loss_condition_met()
                if mode == "loss_plus_voltage"
                else False
            )
            stale_reference = self._loss_stale_reference() if loss_met else None
            snapshot = await self.get_voltage_snapshot(
                stale_reference=(
                    stale_reference if stale_reference is not None else None
                ),
                stale_after_seconds=(
                    self._config.power.confirm_timeout
                    if mode == "loss_plus_voltage" and stale_reference is not None
                    else None
                ),
                unavailable_as_missing=False,
            )
            house_state = snapshot.get("house_state")
            if mode == "loss_plus_voltage" and house_state in ("on", "partial", "off"):
                # Recheck both loss AND restoration after the voltage fetch.
                # Missing/expired witness data cannot mean confirmed presence.
                witness = await self._loss_sample()
                if witness is None or parse_condition(
                    self._config.power.loss_state, str(witness.get("state", ""))
                ) != (house_state == "off"):
                    return "unknown"
            if house_state in ("on", "partial", "off"):
                return str(house_state)

        return "unknown"

    async def _detect_dtek_power(self) -> str:
        """Detect power state from the DTEK binary sensor."""
        entity_id = f"binary_sensor.{self._config.entity_prefix}_power"
        state = await self._ha.get_state(entity_id)
        value = str((state or {}).get("state", ""))
        return value if value in ("on", "off") else "unknown"

    async def get_voltage_snapshot(
        self,
        *,
        stale_reference: datetime | str | None = None,
        stale_after_seconds: int | None = None,
        unavailable_as_missing: bool | None = None,
    ) -> dict[str, Any]:
        """Return current voltage topology for this address."""
        entities = list(self._config.voltage.entities or [])
        phases: list[dict[str, Any]] = []
        total = len(entities)
        # Kept as a compatibility argument only. Availability is not voltage.
        treat_unavailable_as_missing = False
        reference_dt = self._parse_state_datetime(stale_reference)
        stale_timeout_elapsed = False
        if (
            reference_dt is not None
            and stale_after_seconds is not None
            and stale_after_seconds >= 0
        ):
            stale_timeout_elapsed = now_kyiv() >= (
                reference_dt + timedelta(seconds=stale_after_seconds)
            )

        for index, entity_cfg in enumerate(entities, start=1):
            label = self.normalize_phase_label(
                index=index,
                total=total,
                raw_label=entity_cfg.label,
            )
            state = (
                await self._ha.get_state(entity_cfg.entity)
                if getattr(self._ha, "is_connected", True)
                else None
            )
            state_missing = state is None
            restored = bool((state or {}).get("attributes", {}).get("restored"))
            raw_value = str(state.get("state", "")) if state is not None else ""
            last_updated = (
                (state.get("last_reported") or state.get("last_updated"))
                if state is not None
                else None
            )
            last_updated_dt = self._parse_state_datetime(last_updated)
            voltage: float | None = None
            available: bool | None = None
            counts_for_power = False
            stale_after_loss = False

            if not state_missing and raw_value not in ("unavailable", "unknown", ""):
                try:
                    voltage = float(raw_value)
                except (ValueError, TypeError):
                    pass
                else:
                    if math.isfinite(voltage) and voltage >= 0:
                        available = voltage >= self._config.voltage.present_above
                        counts_for_power = True
            age = (
                (now_kyiv() - last_updated_dt).total_seconds()
                if last_updated_dt
                else None
            )
            stale = (
                age is None
                or age < -5
                or age > getattr(self._config.voltage, "max_age_seconds", 600)
            )
            if stale or restored or not counts_for_power:
                voltage, available, counts_for_power = None, None, False

            # Only a fresh independent loss signal may invalidate an older
            # cached voltage. Missing REST results never prove a power failure.
            if (
                stale_timeout_elapsed
                and not state_missing
                and not restored
                and reference_dt is not None
                and last_updated_dt is not None
                and (stale or raw_value in ("unavailable", "unknown"))
                and (
                    last_updated_dt < reference_dt
                    or raw_value in ("unavailable", "unknown")
                )
                and await self.loss_condition_met()
            ):
                voltage, available, counts_for_power = None, False, True
                stale_after_loss = True

            phases.append(
                {
                    "entity_id": entity_cfg.entity,
                    "label": label,
                    "voltage": voltage,
                    "available": available,
                    "counts_for_power": counts_for_power,
                    "raw_state": raw_value,
                    "last_updated": last_updated,
                    "stale_after_loss": stale_after_loss,
                    "stale": stale,
                    "restored": restored,
                }
            )

        present_phase_count = sum(1 for phase in phases if phase["available"] is True)
        known_phase_count = sum(1 for phase in phases if phase["counts_for_power"])
        phase_count = len(phases)
        if not phases:
            house_state: str | None = None
        elif known_phase_count == 0 or (
            present_phase_count == 0 and known_phase_count < phase_count
        ):
            house_state = None
        elif present_phase_count == phase_count and known_phase_count == phase_count:
            house_state = "on"
        elif present_phase_count == 0 and known_phase_count == phase_count:
            house_state = "off"
        else:
            house_state = "partial"

        single_voltage = phases[0]["voltage"] if phase_count == 1 else None
        missing_phases = [
            phase["label"]
            for phase in phases
            if phase["counts_for_power"] and phase["available"] is False
        ]
        unknown_phases = [
            phase["label"] for phase in phases if not phase["counts_for_power"]
        ]

        return {
            "phase_count": phase_count,
            "present_phase_count": present_phase_count,
            "known_phase_count": known_phase_count,
            "house_state": house_state,
            "single_voltage": single_voltage,
            "phases": phases,
            "missing_phases": missing_phases,
            "unknown_phases": unknown_phases,
            "present_above": self._config.voltage.present_above,
            "unavailable_as_missing": treat_unavailable_as_missing,
            "stale_reference": reference_dt.isoformat() if reference_dt else None,
            "stale_timeout_elapsed": stale_timeout_elapsed,
        }

    async def get_voltage(self) -> float | None:
        """Return single-phase voltage for one-entity configurations."""
        snapshot = await self.get_voltage_snapshot()
        return snapshot.get("single_voltage")

    async def get_phases(self) -> list[dict[str, Any]] | None:
        """Return multi-phase voltage details for two- or three-phase setups."""
        snapshot = await self.get_voltage_snapshot()
        phases = snapshot.get("phases") or []
        if len(phases) <= 1:
            return None
        return phases

    async def get_restore_ready_state(self) -> str | None:
        """Return current voltage-based house state used during restore checks."""
        snapshot = await self.get_voltage_snapshot()
        house_state = snapshot.get("house_state")
        if house_state in ("on", "partial", "off"):
            return str(house_state)
        return None

    async def loss_condition_met(self) -> bool:
        """Check whether the configured loss entity currently indicates a loss."""
        sample = await self._loss_sample()
        return sample is not None and parse_condition(
            self._config.power.loss_state, str(sample.get("state", ""))
        )

    async def _loss_sample(self) -> dict | None:
        """Return a live independent report; missing data is not grid presence."""
        loss_entity = self._config.power.loss_entity
        if not loss_entity:
            return None

        if not getattr(self._ha, "is_connected", True):
            return None
        state = await self._ha.get_state(loss_entity)
        if not state or state.get("attributes", {}).get("restored"):
            return None
        topic = getattr(self._config.power, "loss_mqtt_topic", "")
        if topic:
            live = self._ha.mqtt_observation(topic)
            if live is None:
                return None
            try:
                agrees = float(live["state"]) == float(state["state"])
            except (ValueError, TypeError):
                agrees = live["state"] == state["state"]
            if not agrees:
                return None
            state = live
        value = str(state.get("state", ""))
        stamp = self._parse_state_datetime(
            state.get("last_reported") or state.get("last_updated")
        )
        connection_time = self._parse_state_datetime(
            getattr(self._ha, "connected_at", None)
        )
        floor = (
            max(self._loss_fresh_after, connection_time)
            if connection_time
            else self._loss_fresh_after
        )
        if (
            stamp is None
            or stamp <= floor
            or not -5
            <= (now_kyiv() - stamp).total_seconds()
            <= getattr(self._config.voltage, "max_age_seconds", 600)
        ):
            return None
        if value in ("unavailable", "unknown", ""):
            return None
        try:
            if not math.isfinite(float(value)):
                return None
        except ValueError:
            pass  # Binary sensors (off/on) may also be independent witnesses.
        return state

    async def restoration_confirmed_since(self, deadline: datetime) -> bool:
        """Confirm recovery with new reports, not repeated reads of one sample.

        Event-driven binary sources remain valid without periodic reports. For
        paired measurements, each source must report presence at/after the end
        of the restore delay. This prevents a single positive polling cycle
        from being announced as a stable recovery.
        """
        mode = self._config.power.mode
        if mode == "sources":
            evidence = await self.sources.snapshot()
            if evidence["state"] != "on":
                return False
            for source, item in zip(self._config.power.sources, evidence["sources"]):
                if source.freshness != "heartbeat" and not source.mqtt_topic:
                    continue
                stamp = self._parse_state_datetime(item["reported_at"])
                if stamp is None or stamp < deadline:
                    return False
            return True
        if mode not in ("loss_plus_voltage", "voltage_only"):
            return True
        voltage = await self.get_voltage_snapshot()
        if voltage["house_state"] not in ("on", "partial"):
            return False
        for phase in voltage["phases"]:
            if phase["available"] is True:
                stamp = self._parse_state_datetime(phase["last_updated"])
                if stamp is None or stamp < deadline:
                    return False
        if mode == "voltage_only":
            return True
        witness = await self._loss_sample()
        if witness is None or parse_condition(self._config.power.loss_state, str(witness["state"])):
            return False
        if str(witness["state"]) in ("on", "off") and not getattr(self._config.power, "loss_mqtt_topic", ""):
            return True
        stamp = self._parse_state_datetime(witness.get("last_reported") or witness.get("last_updated"))
        return stamp is not None and stamp >= deadline

    def phase_label_for_entity(self, entity_id: str) -> str:
        """Return the display label for a configured voltage entity."""
        entities = list(self._config.voltage.entities or [])
        total = len(entities)
        for index, entity_cfg in enumerate(entities, start=1):
            if entity_cfg.entity == entity_id:
                return self._phase_label(
                    index=index,
                    total=total,
                    raw_label=entity_cfg.label,
                )
        return entity_id

    def _loss_stale_reference(self) -> datetime | None:
        return self._parse_state_datetime(
            self._state.get(self._prefix, "loss_stale_reference_at")
        )

    @staticmethod
    def _parse_state_datetime(value: datetime | str | None) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            try:
                dt = datetime.fromisoformat(str(value))
            except (TypeError, ValueError):
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=now_kyiv().tzinfo)
        # Durations and freshness must use elapsed time across DST transitions.
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _phase_label(index: int, total: int, raw_label: str) -> str:
        return PowerMonitor.normalize_phase_label(
            index=index,
            total=total,
            raw_label=raw_label,
        )

    @staticmethod
    def normalize_phase_label(index: int, total: int, raw_label: str) -> str:
        label = str(raw_label or "").strip()
        if label and label.casefold() not in {
            "основний",
            "основна",
            "main",
            "primary",
            "основное",
        }:
            return label
        if total >= 1:
            return f"L{index}"
        return "L1"

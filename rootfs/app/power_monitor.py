"""Centralized power, voltage, and phase topology service."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from config import AddressConfig
from ha_client import HAClient
from state_store import StateStore
from utils import now_kyiv, parse_condition

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
        self._prefix = config.entity_prefix

    async def get_power_state(self, *, prefer_stored: bool = True) -> str:
        """Return current house state: on, partial, or off."""
        if prefer_stored and self._config.power.enabled:
            stored = self._state.get(self._prefix, "power_state")
            if stored in ("on", "partial", "off"):
                return str(stored)
        return await self.detect_current_power()

    async def is_on(self) -> bool:
        """Check if the house has at least some power."""
        return await self.get_power_state() != "off"

    async def detect_current_power(self) -> str:
        """Detect current power state directly from HA entities."""
        mode = self._config.power.mode if self._config.power.enabled else "dtek_only"

        if mode == "dtek_only":
            return await self._detect_dtek_power()

        if mode in ("voltage_only", "loss_plus_voltage"):
            loss_met = await self.loss_condition_met() if mode == "loss_plus_voltage" else False
            stale_reference = (
                self._loss_stale_reference() if mode == "loss_plus_voltage" else None
            )
            snapshot = await self.get_voltage_snapshot(
                stale_reference=stale_reference if stale_reference is not None else None,
                stale_after_seconds=self._config.power.confirm_timeout
                if mode == "loss_plus_voltage" and stale_reference is not None
                else None,
                unavailable_as_missing=(
                    self._unavailable_as_missing()
                    if mode == "voltage_only"
                    else self._unavailable_as_missing() and (loss_met or stale_reference is not None)
                ),
            )
            house_state = snapshot.get("house_state")
            if house_state in ("on", "partial", "off"):
                return str(house_state)

        return await self._detect_dtek_power()

    async def _detect_dtek_power(self) -> str:
        """Detect power state from the DTEK binary sensor."""
        entity_id = f"binary_sensor.{self._prefix}_power"
        state = await self._ha.get_state(entity_id)
        if state and str(state.get("state", "")) == "off":
            return "off"
        return "on"

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
        treat_unavailable_as_missing = (
            self._unavailable_as_missing()
            if unavailable_as_missing is None
            else bool(unavailable_as_missing)
        )
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
            state = await self._ha.get_state(entity_cfg.entity)
            state_missing = state is None
            raw_value = str(state.get("state", "")) if state is not None else ""
            last_updated = state.get("last_updated") if state is not None else None
            last_updated_dt = self._parse_state_datetime(last_updated)
            voltage: float | None = None
            available: bool | None = None
            counts_for_power = False
            stale_after_loss = False

            if state_missing:
                # A transient REST miss is not evidence of a missing phase.
                # Treat it as unknown unless a loss-trigger timeout later
                # explicitly marks the cached reading as stale.
                available = None
                counts_for_power = False
            elif raw_value not in ("unavailable", "unknown", ""):
                try:
                    voltage = float(raw_value)
                except (ValueError, TypeError):
                    if treat_unavailable_as_missing:
                        available = False
                        counts_for_power = True
                else:
                    available = voltage >= self._config.voltage.present_above
                    counts_for_power = True
            elif treat_unavailable_as_missing:
                available = False
                counts_for_power = True

            if (
                stale_timeout_elapsed
                and reference_dt is not None
                and (last_updated_dt is None or last_updated_dt < reference_dt)
            ):
                voltage = None
                available = False
                counts_for_power = True
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
                }
            )

        present_phase_count = sum(1 for phase in phases if phase["available"] is True)
        known_phase_count = sum(1 for phase in phases if phase["counts_for_power"])
        phase_count = len(phases)
        if not phases:
            house_state: str | None = None
        elif known_phase_count == 0:
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
            phase["label"]
            for phase in phases
            if not phase["counts_for_power"]
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
        loss_entity = self._config.power.loss_entity
        if not loss_entity:
            return False

        state = await self._ha.get_state(loss_entity)
        if state is None:
            return False

        value = str(state.get("state", ""))
        return parse_condition(self._config.power.loss_state, value) or (
            value in ("unavailable", "unknown")
        )

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

    def _unavailable_as_missing(self) -> bool:
        return bool(getattr(self._config.voltage, "unavailable_as_missing", True))

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
            return dt.replace(tzinfo=now_kyiv().tzinfo)
        return dt.astimezone(now_kyiv().tzinfo)

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

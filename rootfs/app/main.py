"""DTEK Telegram Bot — main entry point and orchestrator."""

from __future__ import annotations

import asyncio
import logging
import math
import signal
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any

from config import (
    AddressConfig,
    FullConfig,
    build_runtime_config_payload,
    get_runtime_config_path,
    load_config,
    load_runtime_config_payload,
    validate_runtime_config_payload,
)
from features import ALL_FEATURES
from features.base import Feature
from features.emergency import EmergencyFeature
from features.group_change import GroupChangeFeature
from features.power_presence import PowerPresenceFeature
from features.status_message import StatusMessageFeature
from ha_client import HAClient
from groups import state_groups, select_events, presentation_context, display_labels
from messages import DEFAULT_TEMPLATES
from outage_calendar import build_schedule_lines, extract_calendar_events, next_planned_outage, format_next_outage
from power_monitor import PowerMonitor
from delivery_journal import DeliveryJournal
from state_store import StateStore
import hashlib
from telegram_service import TelegramService
from template_engine import TemplateEngine
from ui_server import UIServer
from utils import OUTAGE_TYPE_UA, TZ_KYIV, format_datetime, format_duration, now_kyiv, parse_condition

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("dtek_telegram_bot")

TICK_INTERVAL = 60  # seconds
ENTITY_SUFFIX_MAP = {
    "status": "outage_status",
    "possible_schedule": "possible_outage_schedule",
}
SCHEDULE_GROUP_ENTITY_SUFFIXES = (
    "primary_schedule_group",
    "schedule_group",
)
TEMPLATE_GROUPS = {
    "schedule_change": "Schedule",
    "schedule_empty": "Schedule",
    "emergency_start": "Emergency",
    "emergency_update": "Emergency",
    "emergency_end": "Emergency",
    "group_change": "Group",
    "voltage_low": "Voltage",
    "voltage_high": "Voltage",
    "voltage_normal": "Voltage",
    "power_lost": "Power",
    "power_restored": "Power",
    "power_telemetry_lost": "Power",
    "power_telemetry_restored": "Power",
    "phase_lost": "Power",
    "phase_restored": "Power",
    "upcoming_outage": "Upcoming",
    "status_on": "Status",
    "status_partial": "Status",
    "status_off": "Status",
}


class Orchestrator:
    """Manages features, dispatches events, runs periodic ticks."""

    def __init__(self, config: FullConfig) -> None:
        self.config = config
        self.ha = HAClient(ha_token=config.ha_token)
        identity = config.telegram_bot_token.split(":", 1)[0]
        namespace = hashlib.sha256(f"{identity}:{config.test_chat_id}".encode()).hexdigest()[:16]
        data_dir = Path("/data") / (f"test-{namespace}" if config.test_chat_id else "production")
        self._journal_path = data_dir / "telegram.sqlite"
        self.tg = TelegramService(config.telegram_bot_token, config.telegram_chat_id,
            test_chat_id=config.test_chat_id)
        self._feedback_lock_path = Path("/data") / f"feedback-{identity}.lock"
        self.state = StateStore(data_dir / "state.json") if config.test_chat_id else StateStore()
        self.templates = TemplateEngine()
        self.features: list[Feature] = []
        self._features_by_entity: dict[str, list[Feature]] = {}
        self._features_by_address: dict[str, list[Feature]] = {}
        self._power_monitors: dict[str, PowerMonitor] = {}
        self._running = False
        self._shutdown_started = False
        self._run_task: asyncio.Task | None = None
        self._ha_task: asyncio.Task[None] | None = None
        self._tick_task: asyncio.Task[None] | None = None

    def _init_features(self) -> None:
        """Instantiate enabled features for each address and wire dependencies."""
        for addr in self.config.addresses:
            # Create PowerMonitor for this address
            power_monitor = PowerMonitor(
                config=addr,
                ha=self.ha,
                state=self.state,
            )
            self._power_monitors[addr.entity_prefix] = power_monitor

            # Create all features
            addr_features: list[Feature] = []
            for feature_cls in ALL_FEATURES:
                feature = feature_cls(
                    address_config=addr,
                    ha_client=self.ha,
                    telegram=self.tg,
                    state_store=self.state,
                    templates=self.templates,
                )
                if feature.enabled:
                    addr_features.append(feature)
                    logger.info(
                        "Enabled feature '%s' for %s",
                        feature.name,
                        addr.display_name,
                    )

            # Wire dependencies
            status_message: StatusMessageFeature | None = None
            for f in addr_features:
                if isinstance(f, StatusMessageFeature):
                    status_message = f
                    break

            for f in addr_features:
                if isinstance(f, StatusMessageFeature):
                    f.set_power_monitor(power_monitor)
                elif isinstance(f, GroupChangeFeature):
                    if status_message:
                        f.set_status_message(status_message)
                elif isinstance(f, PowerPresenceFeature):
                    f.set_power_monitor(power_monitor)
                    if status_message:
                        f.set_status_message(status_message)
                elif isinstance(f, EmergencyFeature):
                    if status_message:
                        f.set_status_message(status_message)

            self._features_by_address[addr.entity_prefix] = addr_features
            self.features.extend(addr_features)

    def _collect_watched_entities(self) -> set[str]:
        """Collect all entity IDs that features want to monitor."""
        entities: set[str] = set()
        self._features_by_entity = {}
        for feature in self.features:
            watched = feature.get_watched_entities()
            entities.update(watched)
            for entity_id in watched:
                self._features_by_entity.setdefault(entity_id, []).append(feature)
        return entities

    async def _dispatch_state_change(
        self, entity_id: str, old_state: dict[str, Any], new_state: dict[str, Any]
    ) -> None:
        """Dispatch a state change event to all features watching this entity."""
        old_val = old_state.get("state", "")
        new_val = new_state.get("state", "")
        logger.info("State changed: %s: %s -> %s", entity_id, old_val, new_val)
        for feature in self._features_by_entity.get(entity_id, []):
            try:
                await feature.on_state_change(entity_id, old_state, new_state)
            except Exception:
                logger.exception(
                    "Error in %s.on_state_change for %s",
                    feature.name,
                    entity_id,
                )

    async def _tick_loop(self) -> None:
        """Run periodic ticks for features that need them."""
        while self._running:
            await asyncio.sleep(TICK_INTERVAL)
            if not self.ha.is_connected:
                continue
            for feature in self.features:
                try:
                    await feature.on_tick()
                except Exception:
                    logger.exception("Error in %s.on_tick", feature.name)
            await self.tg.retry_pending(self.state)

    async def run(self) -> None:
        """Main run loop."""
        self._running = True
        self._run_task = asyncio.current_task()

        # Load persistent state
        self.state.load()
        self.tg.journal = DeliveryJournal(self._journal_path)
        if self.config.feedback_enabled:
            await self.tg.start_feedback(self._feedback_lock_path)

        # Export default templates if configured
        if self.config.export_default_templates:
            self.templates.export_defaults()

        # Initialize features
        self._init_features()

        if not self.features:
            logger.warning("No features enabled. Check your config.yaml.")

        # Collect watched entities and register with HA client
        watched = self._collect_watched_entities()
        self.ha.watch_entities(watched)
        self.ha.watch_mqtt_topics({
            a.power.loss_mqtt_topic for a in self.config.addresses
            if a.power.enabled and a.power.mode == "loss_plus_voltage" and a.power.loss_mqtt_topic
        })
        self.ha.on_state_change(self._dispatch_state_change)
        logger.info("Watching %d entities", len(watched))

        # Start tasks
        self._ha_task = asyncio.create_task(self.ha.start())
        self._tick_task = asyncio.create_task(self._tick_loop())

        # Run on_start for all features (after HA is connected)
        await self.ha.wait_connected(timeout=None)

        # Resolve display names from HA device registry
        await self._resolve_display_names()

        for feature in self.features:
            try:
                await feature.on_start()
            except Exception:
                logger.exception("Error in %s.on_start", feature.name)

        logger.info("DTEK Telegram Bot started successfully")

        # Wait for shutdown
        try:
            await asyncio.gather(self._ha_task, self._tick_task)
        except asyncio.CancelledError:
            pass
        finally:
            if not self._shutdown_started:
                await self.shutdown()

    async def _resolve_display_names(self) -> None:
        """Fetch display names from HA device registry for addresses without one."""
        for addr in self.config.addresses:
            if addr.display_name:
                logger.info("Using configured display_name for %s: %s", addr.entity_prefix, addr.display_name)
                continue
            # Use any entity from this prefix to look up the device
            entity_id = f"binary_sensor.{addr.entity_prefix}_power"
            tpl = (
                '{{ device_attr("' + entity_id + '", "name") }}'
                "|"
                '{{ device_attr("' + entity_id + '", "model") }}'
            )
            result = await self.ha.render_template(tpl)
            if result:
                parts = result.split("|", 1)
                device_name = parts[0].strip() if parts[0] else ""
                city = parts[1].strip() if len(parts) > 1 else ""

                # Jinja renders None as the string "None"
                if device_name and device_name != "None":
                    address = device_name
                    city_clean = city.removeprefix("м. ") if city and city not in ("None", "") else ""
                    if city_clean:
                        addr.display_name = f"{city_clean} • {address}"
                    else:
                        addr.display_name = address
                    logger.info(
                        "Resolved display_name for %s: %s",
                        addr.entity_prefix, addr.display_name,
                    )
                else:
                    addr.display_name = addr.entity_prefix
                    logger.warning(
                        "Could not resolve device name for %s, using prefix",
                        addr.entity_prefix,
                    )
            else:
                addr.display_name = addr.entity_prefix
                logger.warning(
                    "Could not fetch device info for %s, using prefix",
                    addr.entity_prefix,
                )

    def _get_editor_runtime_config(self, payload: dict | None = None) -> FullConfig:
        """Return the latest saved editor config without mutating active runtime state."""
        try:
            editor_config = validate_runtime_config_payload(
                load_runtime_config_payload() if payload is None else payload,
                options={
                    "telegram_bot_token": self.config.telegram_bot_token,
                    "telegram_chat_id": self.config.telegram_chat_id,
                },
            )
        except Exception:
            logger.exception(
                "Failed to reload persisted runtime config for ingress editor"
            )
            return self.config

        active_by_prefix = {
            address.entity_prefix: address for address in self.config.addresses
        }
        for address in editor_config.addresses:
            if address.display_name:
                continue
            active = active_by_prefix.get(address.entity_prefix)
            if active and active.display_name:
                address.display_name = active.display_name
        return editor_config

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        if self._shutdown_started:
            return
        self._shutdown_started = True
        logger.info("Shutting down...")
        self._running = False
        if self._tick_task and not self._tick_task.done():
            self._tick_task.cancel()
            await asyncio.gather(self._tick_task, return_exceptions=True)
        for feature in self.features:
            try:
                await feature.on_stop()
            except Exception:
                logger.exception("Error in %s.on_stop", feature.name)
        await self.ha.stop()
        if self._ha_task:
            self._ha_task.cancel()
        await self.state.flush()
        await self.tg.close()
        tasks = [
            task
            for task in (self._ha_task, self._tick_task)
            if task and task is not asyncio.current_task()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._run_task and self._run_task is not asyncio.current_task():
            self._run_task.cancel()
        logger.info("Shutdown complete")

    async def get_overview(self) -> dict[str, Any]:
        """Build a UI-friendly read-only overview snapshot."""
        addresses = await asyncio.gather(
            *[
                self._build_address_overview(addr)
                for addr in self.config.addresses
            ]
        )

        return {
            "runtime": {
                "running": self._running and not self._shutdown_started,
                "ha_connected": self.ha.is_connected,
                "queued_events": self.ha.queued_events,
                "watched_entities": len(self._features_by_entity),
                "feature_count": len(self.features),
            },
            "global": {
                "default_chat_id": self.tg.default_chat_id,
                "has_telegram_token": bool(self.config.telegram_bot_token),
                "address_count": len(self.config.addresses),
            },
            "addresses": addresses,
        }

    async def _build_address_overview(self, addr: AddressConfig) -> dict[str, Any]:
        """Build a summary card for a configured address."""
        features = self._features_by_address.get(addr.entity_prefix, [])
        watched_entities = sorted(
            {
                entity_id
                for feature in features
                for entity_id in feature.get_watched_entities()
            }
        )
        live_entities: dict[str, dict[str, Any]] = {}
        live_power: str | None = None

        if self.ha.is_connected:
            states = await asyncio.gather(
                *[self.ha.get_state(entity_id) for entity_id in watched_entities]
            )
            for entity_id, state in zip(watched_entities, states, strict=False):
                if state is None:
                    continue
                live_entities[entity_id] = {
                    "state": state.get("state"),
                    "friendly_name": state.get("attributes", {}).get("friendly_name", ""),
                    "last_updated": state.get("last_updated", ""),
                }

            power_monitor = self._power_monitors.get(addr.entity_prefix)
            if power_monitor:
                live_power = await power_monitor.detect_current_power()

        return {
            "entity_prefix": addr.entity_prefix,
            "display_name": addr.display_name or addr.entity_prefix,
            "chat_id": self.tg.resolve_chat_id(addr.telegram_chat_id),
            "power_mode": addr.power.mode if addr.power.enabled else "disabled",
            "enabled_features": [feature.name for feature in features],
            "watched_entities": watched_entities,
            "stored_state": self.state.get_all(addr.entity_prefix),
            "live_power": live_power,
            "live_entities": live_entities,
            "loss_mqtt_observation": self.ha.mqtt_observation(addr.power.loss_mqtt_topic) if addr.power.loss_mqtt_topic else None,
        }

    async def get_diagnostics(self) -> dict[str, Any]:
        """Return an expanded diagnostics snapshot for the UI."""
        overview = await self.get_overview()
        return {
            "runtime": overview["runtime"],
            "global": overview["global"],
            "configured_addresses": [
                {
                    "config": addr.model_dump(),
                    "features": [feature.name for feature in self._features_by_address.get(addr.entity_prefix, [])],
                    "state": self.state.get_all(addr.entity_prefix),
                }
                for addr in self.config.addresses
            ],
        }

    async def discover_addresses(self) -> dict[str, Any]:
        """Discover DTEK-style address candidates from current HA entity states."""
        if not self.ha.is_connected:
            return {
                "ha_connected": False,
                "candidates": [],
                "catalog": self._empty_catalog(),
            }

        states = await self.ha.get_states()
        entity_map = {state.get("entity_id", ""): state for state in states}
        configured = {addr.entity_prefix for addr in self.config.addresses}
        candidates: list[dict[str, Any]] = []

        for entity_id, state in entity_map.items():
            if not entity_id.startswith("binary_sensor.") or not entity_id.endswith("_power"):
                continue

            prefix = entity_id.removeprefix("binary_sensor.").removesuffix("_power")
            status_entity = f"sensor.{prefix}_outage_status"
            schedule_group_entity = next(
                (
                    f"sensor.{prefix}_{suffix}"
                    for suffix in SCHEDULE_GROUP_ENTITY_SUFFIXES
                    if f"sensor.{prefix}_{suffix}" in entity_map
                ),
                "",
            )
            calendar_entity = f"calendar.{prefix}_outage_schedule"
            if status_entity not in entity_map and not schedule_group_entity:
                continue

            candidates.append(
                {
                    "entity_prefix": prefix,
                    "configured": prefix in configured,
                    "friendly_name": state.get("attributes", {}).get("friendly_name", "") or prefix,
                    "entities": {
                        "power": entity_id,
                        "status": status_entity if status_entity in entity_map else "",
                        "schedule_group": schedule_group_entity,
                        "outage_schedule": calendar_entity if calendar_entity in entity_map else "",
                    },
                }
            )

        candidates.sort(key=lambda item: (item["configured"], item["friendly_name"].lower()))
        return {
            "ha_connected": True,
            "candidates": candidates,
            "catalog": self._build_entity_catalog(entity_map),
        }

    async def preview_runtime_config(self, config: FullConfig) -> dict[str, Any]:
        """Preview how the supplied config is interpreted against current HA states."""
        if not self.ha.is_connected:
            return {"ha_connected": False, "generated_at": None, "addresses": []}

        previews = await asyncio.gather(
            *[self._build_address_preview(addr) for addr in config.addresses]
        )
        return {
            "ha_connected": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "addresses": previews,
        }

    def _empty_catalog(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "binary_power": [],
            "frequency_sensors": [],
            "voltage_sensors": [],
            "status_sensors": [],
            "schedule_group_sensors": [],
            "outage_calendars": [],
        }

    def _build_entity_catalog(
        self,
        entity_map: dict[str, dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """Build grouped entity suggestions for the ingress editor."""
        catalog: dict[str, dict[str, dict[str, Any]]] = {
            key: {} for key in self._empty_catalog()
        }

        for entity_id, state in entity_map.items():
            if not entity_id:
                continue

            attrs = state.get("attributes", {})
            domain = entity_id.split(".", 1)[0]
            unit = str(attrs.get("unit_of_measurement", "")).lower()
            device_class = str(attrs.get("device_class", "")).lower()
            lowered = entity_id.lower()
            snapshot = self._state_snapshot(entity_id, state)

            if domain == "binary_sensor" and entity_id.endswith("_power"):
                catalog["binary_power"][entity_id] = snapshot

            if domain == "sensor":
                if entity_id.endswith("_outage_status"):
                    catalog["status_sensors"][entity_id] = snapshot
                if entity_id.endswith("_schedule_group"):
                    catalog["schedule_group_sensors"][entity_id] = snapshot
                if device_class == "voltage" or unit == "v":
                    catalog["voltage_sensors"][entity_id] = snapshot
                if unit == "hz" or "frequenz" in lowered or "frequency" in lowered or "_freq" in lowered:
                    catalog["frequency_sensors"][entity_id] = snapshot

            if domain == "calendar" and entity_id.endswith("_outage_schedule"):
                catalog["outage_calendars"][entity_id] = snapshot

        return {
            key: sorted(
                group.values(),
                key=lambda item: (
                    item.get("friendly_name", "").lower(),
                    item.get("entity_id", ""),
                ),
            )
            for key, group in catalog.items()
        }

    async def _build_address_preview(self, addr: AddressConfig) -> dict[str, Any]:
        """Preview the current live inputs that drive this address config."""
        entity_ids: list[str] = []
        dtek_power_entity = f"binary_sensor.{addr.entity_prefix}_power" if addr.entity_prefix else ""
        if dtek_power_entity:
            entity_ids.append(dtek_power_entity)
        if addr.power.loss_entity:
            entity_ids.append(addr.power.loss_entity)
        entity_ids.extend(entity.entity for entity in addr.voltage.entities if entity.entity)

        snapshots: dict[str, dict[str, Any]] = {}
        if entity_ids:
            unique_ids = list(dict.fromkeys(entity_ids))
            states = await asyncio.gather(
                *[self.ha.get_state(entity_id) for entity_id in unique_ids]
            )
            for entity_id, state in zip(unique_ids, states, strict=False):
                snapshots[entity_id] = self._state_snapshot(entity_id, state)

        dtek_snapshot = snapshots.get(dtek_power_entity) if dtek_power_entity else None
        loss_snapshot = snapshots.get(addr.power.loss_entity) if addr.power.loss_entity else None
        voltage_snapshots = [
            snapshots[entity.entity]
            for entity in addr.voltage.entities
            if entity.entity in snapshots
        ]

        detected_power = None
        reason = "Home Assistant ще не підключений."
        voltage_topology: dict[str, Any] = {
            "house_state": None,
            "phase_count": 0,
            "present_phase_count": 0,
            "known_phase_count": 0,
            "missing_phases": [],
            "unknown_phases": [],
            "present_above": addr.voltage.present_above,
            "unavailable_as_missing": getattr(addr.voltage, "unavailable_as_missing", True),
        }
        if addr.entity_prefix:
            power_monitor = PowerMonitor(addr, self.ha, self.state)
            detected_power = await power_monitor.detect_current_power()
            voltage_topology = await power_monitor.get_voltage_snapshot()
            if loss_snapshot is not None:
                loss_snapshot["matches_loss_state"] = await power_monitor.loss_condition_met()
            reason = self._preview_reason(
                addr,
                dtek_snapshot,
                loss_snapshot,
                voltage_topology,
            )

        return {
            "entity_prefix": addr.entity_prefix,
            "display_name": addr.display_name or addr.entity_prefix or "New address",
            "power_enabled": addr.power.enabled,
            "mode": addr.power.mode if addr.power.enabled else "dtek_only",
            "detected_power": detected_power,
            "reason": reason,
            "signals": {
                "dtek_power": dtek_snapshot,
                "loss_entity": self._augment_loss_snapshot(addr, loss_snapshot),
                "voltage_entities": self._augment_voltage_snapshots(addr, voltage_snapshots, voltage_topology),
                "voltage_topology": voltage_topology,
            },
        }

    def _preview_reason(
        self,
        addr: AddressConfig,
        dtek_snapshot: dict[str, Any] | None,
        loss_snapshot: dict[str, Any] | None,
        voltage_topology: dict[str, Any],
    ) -> str:
        """Explain the current live preview for the selected power mode."""
        if not addr.power.enabled:
            return "Визначення живлення вимкнене, тому runtime орієнтується на DTEK binary sensor."

        dtek_state = dtek_snapshot.get("state") if dtek_snapshot else "unknown"
        loss_value = (loss_snapshot or {}).get("state")
        house_state = voltage_topology.get("house_state")
        phase_count = int(voltage_topology.get("phase_count") or 0)
        missing_phases = voltage_topology.get("missing_phases") or []
        unknown_phases = voltage_topology.get("unknown_phases") or []
        unavailable_as_missing = bool(
            voltage_topology.get("unavailable_as_missing", True)
        )

        if addr.power.mode == "dtek_only":
            return "Для цієї адреси єдиним джерелом істини є DTEK binary sensor."

        if phase_count == 0:
            return f"Сенсори напруги не налаштовані; стан живлення невідомий."

        if addr.power.mode == "voltage_only":
            if house_state is None:
                return "Немає достатніх актуальних вимірювань напруги; стан живлення невідомий."
            if house_state == "off":
                if unavailable_as_missing:
                    return "Усі сенсори напруги зараз нижче порога або недоступні, тому runtime вважає будинок знеструмленим."
                return "Усі відомі сенсори напруги зараз нижче порога, тому runtime вважає будинок знеструмленим."
            if house_state == "partial":
                details: list[str] = []
                if missing_phases:
                    details.append(f"{', '.join(missing_phases)} відсутні")
                if unknown_phases:
                    details.append(f"{', '.join(unknown_phases)} невизначені")
                suffix = f" ({'; '.join(details)})" if details else ""
                return f"Напруга є не на всіх фазах, тому runtime показує часткове живлення{suffix}."
            if house_state == "on":
                return "Усі налаштовані сенсори напруги вище порога, тому runtime вважає живлення наявним."

        if addr.power.mode == "loss_plus_voltage":
            if (loss_snapshot or {}).get("matches_loss_state"):
                return "Свіжий незалежний сенсор повідомляє про втрату мережі. Бот перевіряє напругу та застосовує затримку підтвердження."
            if house_state == "off":
                return "Свіжі вимірювання напруги нижче порога. Повідомлення надходить після затримки підтвердження."
            if house_state is None:
                return "Немає достатніх свіжих вимірювань; відключення не підтверджено."
            return "Напруга присутня принаймні на частині фаз. Незалежний сигнал втрати мережі не підтверджений."

        return f"Режим невідомий; перевірте налаштування."

    def _augment_loss_snapshot(
        self,
        addr: AddressConfig,
        snapshot: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not snapshot:
            return None
        augmented = dict(snapshot)
        augmented["matches_loss_state"] = snapshot.get("matches_loss_state", False)
        augmented["loss_state"] = addr.power.loss_state
        return augmented

    def _augment_voltage_snapshots(
        self,
        addr: AddressConfig,
        snapshots: list[dict[str, Any]],
        topology: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        augmented: list[dict[str, Any]] = []
        for index, snapshot in enumerate(snapshots, start=1):
            item = dict(snapshot)
            phase = next((p for p in (topology or {}).get("phases", []) if p["entity_id"] == snapshot["entity_id"]), {})
            item["available_for_power"] = phase.get("available")
            item["present_above"] = addr.voltage.present_above
            raw_label = (
                addr.voltage.entities[index - 1].label
                if len(addr.voltage.entities) >= index
                else ""
            )
            item["phase_label"] = PowerMonitor.normalize_phase_label(
                index=index,
                total=len(addr.voltage.entities),
                raw_label=raw_label,
            )
            augmented.append(item)
        return augmented

    @staticmethod
    def _matches_loss_condition(addr: AddressConfig, value: Any) -> bool | None:
        text = "" if value is None else str(value)
        if text in ("", "unknown", "unavailable"):
            return None
        return parse_condition(addr.power.loss_state, text)

    @staticmethod
    def _below_threshold(value: Any, threshold: float) -> bool | None:
        try:
            return float(value) < threshold
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _state_snapshot(entity_id: str, state: dict[str, Any] | None) -> dict[str, Any]:
        attributes = state.get("attributes", {}) if state else {}
        raw_state = state.get("state") if state else None
        return {
            "entity_id": entity_id,
            "friendly_name": attributes.get("friendly_name", "") or entity_id,
            "state": raw_state,
            "unit": attributes.get("unit_of_measurement", ""),
            "device_class": attributes.get("device_class", ""),
            "available": raw_state not in (None, "unknown", "unavailable"),
        }

    def get_templates_snapshot(self) -> dict[str, Any]:
        """Return template metadata for the ingress template workspace."""
        return {
            "templates_dir": str(self.templates.templates_dir),
            "templates": [
                {
                    **item,
                    "group": TEMPLATE_GROUPS.get(item["name"], "Custom"),
                    "built_in": item["name"] in DEFAULT_TEMPLATES,
                }
                for item in self.templates.list_templates()
            ],
        }

    async def preview_template(
        self,
        config: FullConfig,
        *,
        template_name: str,
        address_index: int,
        source_override: str | None = None,
    ) -> dict[str, Any]:
        """Render a template preview for a draft address config."""
        address = self._get_address_by_index(config, address_index)
        details = self.templates.get_template_details(template_name)
        context = await self._build_template_context(address, template_name)
        rendered = (
            self.templates.render_source(source_override, **context)
            if source_override is not None
            else self.templates.render(template_name, **context)
        )
        return {
            "template": {
                **details,
                "origin": "draft" if source_override is not None else details["origin"],
                "source": source_override if source_override is not None else details["source"],
                "group": TEMPLATE_GROUPS.get(template_name, "Custom"),
            },
            "address": {
                "index": address_index,
                "entity_prefix": address.entity_prefix,
                "display_name": address.display_name or address.entity_prefix,
                "chat_id": address.telegram_chat_id or self.config.telegram_chat_id,
            },
            "context": context,
            "rendered": rendered,
        }

    async def send_template_test(
        self,
        config: FullConfig,
        *,
        template_name: str,
        address_index: int,
        source_override: str | None = None,
    ) -> dict[str, Any]:
        """Send a rendered template preview to Telegram without saving config."""
        preview = await self.preview_template(
            config,
            template_name=template_name,
            address_index=address_index,
            source_override=source_override,
        )
        chat_id = self.tg.resolve_chat_id(preview["address"]["chat_id"])
        text = f"🧪 Тест шаблону: {template_name}\n\n{preview['rendered']}"
        message_id = await self.tg.send_message(
            text,
            chat_id=chat_id,
            disable_notification=True,
            context={"feature": "template_test", "template": template_name, "preview": preview["context"]},
        )
        preview["sent"] = {
            "ok": message_id is not None,
            "chat_id": chat_id,
            "message_id": message_id,
        }
        return preview

    def save_template_override(self, template_name: str, source: str) -> dict[str, Any]:
        """Persist a template override and return updated metadata."""
        path = self.templates.save_override(template_name, source)
        details = self.templates.get_template_details(template_name)
        return {
            **details,
            "group": TEMPLATE_GROUPS.get(template_name, "Custom"),
            "path": str(path),
        }

    def reset_template_override(self, template_name: str) -> dict[str, Any]:
        """Delete a template override and return updated metadata."""
        removed = self.templates.delete_override(template_name)
        details = self.templates.get_template_details(template_name)
        return {
            **details,
            "group": TEMPLATE_GROUPS.get(template_name, "Custom"),
            "removed": removed,
        }

    @staticmethod
    def _get_address_by_index(config: FullConfig, address_index: int) -> AddressConfig:
        try:
            return config.addresses[address_index]
        except IndexError as exc:
            raise ValueError(f"Invalid address index: {address_index}") from exc

    @staticmethod
    def _template_suffixes(suffix: str) -> tuple[str, ...]:
        if suffix == "schedule_group":
            return SCHEDULE_GROUP_ENTITY_SUFFIXES
        return (ENTITY_SUFFIX_MAP.get(suffix, suffix),)

    @classmethod
    def _template_entities(cls, address: AddressConfig, suffix: str) -> list[str]:
        if suffix.startswith("binary_sensor.") or suffix.startswith("sensor.") or suffix.startswith("calendar."):
            return [suffix]

        entity_ids: list[str] = []
        for resolved_suffix in cls._template_suffixes(suffix):
            if resolved_suffix == "power":
                entity_ids.append(f"binary_sensor.{address.entity_prefix}_{resolved_suffix}")
            elif resolved_suffix in ("outage_schedule", "possible_outage_schedule"):
                entity_ids.append(f"calendar.{address.entity_prefix}_{resolved_suffix}")
            else:
                entity_ids.append(f"sensor.{address.entity_prefix}_{resolved_suffix}")
        return entity_ids

    @classmethod
    def _template_entity(cls, address: AddressConfig, suffix: str) -> str:
        return cls._template_entities(address, suffix)[0]

    async def _groups_for_address(self, address: AddressConfig) -> list[str]:
        if self.ha.is_connected:
            for entity in self._template_entities(address, "schedule_group"):
                groups = state_groups(await self.ha.get_state(entity))
                if groups:
                    return groups
        return []

    async def _current_group_for_address(self, address: AddressConfig) -> str:
        groups = await self._groups_for_address(address)
        if address.selected_group and address.selected_group in groups:
            return address.selected_group
        return ", ".join(groups) or "—"

    async def _get_entity_value(self, address: AddressConfig, suffix: str) -> str | None:
        if not self.ha.is_connected or not address.entity_prefix:
            return None
        for entity_id in self._template_entities(address, suffix):
            state = await self.ha.get_state(entity_id)
            if state is None:
                continue
            value = str(state.get("state", ""))
            if value in ("", "unknown", "unavailable"):
                continue
            return value
        return None

    async def _fetch_calendar_events_for_address(
        self,
        address: AddressConfig,
        *,
        exclude_emergency: bool = False,
    ) -> list[dict[str, Any]]:
        if not self.ha.is_connected or not address.entity_prefix:
            return []
        calendar_entity = self._template_entity(address, "outage_schedule")
        now = now_kyiv()
        groups = await self._groups_for_address(address)
        result = await self.ha.call_service(
            domain="calendar",
            service="get_events",
            entity_id=calendar_entity,
            data={
                "start_date_time": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                "end_date_time": (now + timedelta(days=2)).isoformat(),
            },
            return_response=True,
        )
        events = extract_calendar_events(
            result,
            calendar_entity,
            exclude_emergency=exclude_emergency,
        )

        if groups != await self._groups_for_address(address):
            raise ValueError("Groups changed while fetching preview")
        return select_events(events, groups, address.selected_group)

    async def _build_template_context(
        self,
        address: AddressConfig,
        template_name: str,
    ) -> dict[str, Any]:
        """Build a preview context that resembles runtime data for one address."""
        display_name = address.display_name or address.entity_prefix or "New address"
        group = await self._current_group_for_address(address)
        group_state = None
        if self.ha.is_connected:
            for entity in self._template_entities(address, "schedule_group"):
                candidate = await self.ha.get_state(entity)
                if state_groups(candidate):
                    group_state = candidate
                    break
        timestamp = format_datetime(now_kyiv())
        power_monitor = PowerMonitor(address, self.ha, self.state)
        voltage_snapshot = await power_monitor.get_voltage_snapshot()
        voltage = voltage_snapshot.get("single_voltage")
        phases = voltage_snapshot.get("phases") or []
        preview = await self._build_address_preview(address) if address.entity_prefix else {}
        detected_power = preview.get("detected_power") if preview else None

        context: dict[str, Any] = {
            "display_name": display_name,
            "group": group,
            "groups": state_groups(group_state),
            "selected_group": address.selected_group,
            "timestamp": timestamp,
            "short_name": display_name,
        }

        if template_name in {"schedule_change", "schedule_empty", "group_change", "upcoming_outage", "status_on", "status_partial"}:
            events = await self._fetch_calendar_events_for_address(
                address,
                exclude_emergency=template_name in {"schedule_change", "schedule_empty", "group_change", "status_on", "status_partial"},
            )
        else:
            events = []

        if template_name == "schedule_change":
            context["schedule_lines"] = build_schedule_lines(events, header_group=group, group_labels=display_labels(group_state)) or ["📅 09.03 — 12:00–16:00"]
        elif template_name == "schedule_empty":
            pass
        elif template_name == "emergency_start":
            outage_type = await self._get_entity_value(address, "status") or "Аварійне відключення"
            description = await self._get_entity_value(address, "outage_description") or "Аварійне відключення"
            start_raw = await self._get_entity_value(address, "outage_start")
            end_raw = await self._get_entity_value(address, "outage_end")
            context.update(
                outage_type=OUTAGE_TYPE_UA.get(outage_type, outage_type),
                description=description,
                start=format_datetime(start_raw) if start_raw else timestamp,
                end=format_datetime(end_raw) if end_raw else "—",
            )
        elif template_name == "emergency_update":
            outage_type = await self._get_entity_value(address, "status") or "emergency"
            description = (
                await self._get_entity_value(address, "outage_description")
                or OUTAGE_TYPE_UA.get(outage_type, outage_type)
            )
            end_raw = await self._get_entity_value(address, "outage_end")
            context.update(
                reason_changed=True,
                old_reason="Аварійні ремонтні роботи",
                new_reason=description,
                end_changed=True,
                old_end="17.03.2026 13:25",
                new_end=format_datetime(end_raw) if end_raw else timestamp,
            )
        elif template_name == "emergency_end":
            outage_start_iso = self.state.get(address.entity_prefix, "outage_start_time")
            duration = "—"
            if outage_start_iso:
                try:
                    started = datetime.fromisoformat(str(outage_start_iso))
                    duration = format_duration((now_kyiv() - started).total_seconds())
                except (TypeError, ValueError):
                    pass
            context["duration"] = duration
        elif template_name == "group_change":
            schedule_lines = build_schedule_lines(events, header_group=group, group_labels=display_labels(group_state))
            context.update(
                old_group=self.state.get(address.entity_prefix, "current_group", "—"),
                new_group=group,
                schedule_lines=schedule_lines,
            )
        elif template_name in {"voltage_low", "voltage_high", "voltage_normal"}:
            label = phases[0]["label"] if phases else ""
            context.update(
                voltage=voltage if voltage is not None else 228.5,
                phase_label=label,
            )
        elif template_name in {"power_lost", "power_restored", "power_telemetry_lost", "power_telemetry_restored"}:
            context.update(
                duration="2 год 14 хв",
                voltage=voltage,
                phases=phases,
                house_state=preview.get("detected_power") if preview else "on",
                missing_phases=voltage_snapshot.get("missing_phases", []),
                unknown_phases=voltage_snapshot.get("unknown_phases", []),
            )
        elif template_name in {"phase_lost", "phase_restored"}:
            if not phases:
                phases = [
                    {"label": "L1", "voltage": 228.0, "available": True},
                    {"label": "L2", "voltage": 0.0, "available": False},
                    {"label": "L3", "voltage": 227.0, "available": True},
                ]
            context.update(
                phase_label=phases[0]["label"],
                phases=phases,
            )
        elif template_name == "upcoming_outage":
            upcoming = next_planned_outage(events, now=now_kyiv())
            if upcoming:
                start_dt, end_dt = upcoming
                context.update(
                    minutes=max(
                        math.ceil((start_dt - now_kyiv()).total_seconds() / 60),
                        1,
                    ),
                    start=format_datetime(start_dt),
                    end=format_datetime(end_dt),
                )
            else:
                context.update(
                    minutes=10,
                    start=timestamp,
                    end=timestamp,
                )
        elif template_name == "status_on":
            voltage, phases = self._coerce_status_preview_payload(
                address,
                template_name="status_on",
                voltage=voltage,
                phases=phases,
                detected_power=detected_power,
            )
            next_outage = format_next_outage(
                events, header_group=group, group_labels=display_labels(group_state)
            )
            context.update(
                short_name=display_name,
                voltage=voltage,
                phases=phases,
                next_outage=next_outage,
            )
        elif template_name == "status_partial":
            voltage, phases = self._coerce_status_preview_payload(
                address,
                template_name="status_partial",
                voltage=voltage,
                phases=phases,
                detected_power=detected_power,
            )
            missing_phases = [
                str(phase.get("label", "—"))
                for phase in phases
                if phase.get("available") is False
            ]
            unknown_phases = [
                str(phase.get("label", "—"))
                for phase in phases
                if phase.get("available") is None
            ]
            next_outage = format_next_outage(
                events, header_group=group, group_labels=display_labels(group_state)
            )
            context.update(
                short_name=display_name,
                phases=phases,
                missing_phases=missing_phases,
                unknown_phases=unknown_phases,
                next_outage=next_outage,
            )
        elif template_name == "status_off":
            outage_type_raw = await self._get_entity_value(address, "status")
            dtek_reports_ok = outage_type_raw == "ok"
            outage_description = None
            outage_start = None
            outage_end = None
            outage_duration = None
            power_last_change = self.state.get(address.entity_prefix, "power_last_change")
            if power_last_change:
                try:
                    outage_duration = format_duration(
                        (now_kyiv() - datetime.fromisoformat(str(power_last_change))).total_seconds()
                    )
                except (TypeError, ValueError):
                    outage_duration = None
            if not dtek_reports_ok:
                outage_description = await self._get_entity_value(address, "outage_description")
                outage_start = await self._get_entity_value(address, "outage_start")
                outage_end = await self._get_entity_value(address, "outage_end")
            context.update(
                short_name=display_name,
                outage_duration=outage_duration,
                dtek_reports_ok=dtek_reports_ok,
                outage_type=OUTAGE_TYPE_UA.get(outage_type_raw, outage_type_raw)
                if outage_type_raw not in (None, "", "ok")
                else None,
                outage_description=outage_description,
                outage_start=format_datetime(outage_start) if outage_start else None,
                outage_end=format_datetime(outage_end) if outage_end else None,
            )

        if preview:
            context.setdefault("detected_power", preview.get("detected_power"))

        return presentation_context(context, group_state)

    def _coerce_status_preview_payload(
        self,
        address: AddressConfig,
        *,
        template_name: str,
        voltage: float | None,
        phases: list[dict[str, Any]],
        detected_power: str | None,
    ) -> tuple[float | None, list[dict[str, Any]]]:
        """Keep template preview consistent with the selected status template."""
        phase_count = len(address.voltage.entities)
        live_phases = [dict(item) for item in phases]

        if template_name == "status_on":
            all_live_on = bool(live_phases) and all(
                phase.get("available") is True for phase in live_phases
            )
            if detected_power == "on" and (not live_phases or all_live_on):
                return voltage, live_phases
            return self._build_synthetic_status_phases(
                address,
                phase_count=phase_count,
                state="on",
            )

        if template_name == "status_partial":
            has_partial_live = bool(live_phases) and any(
                phase.get("available") is not True for phase in live_phases
            ) and any(phase.get("available") is True for phase in live_phases)
            if detected_power == "partial" and has_partial_live:
                return voltage, live_phases
            return self._build_synthetic_status_phases(
                address,
                phase_count=max(phase_count, 2),
                state="partial",
            )

        return voltage, live_phases

    def _build_synthetic_status_phases(
        self,
        address: AddressConfig,
        *,
        phase_count: int,
        state: str,
    ) -> tuple[float | None, list[dict[str, Any]]]:
        """Build a template-preview-friendly phase picture for status templates."""
        count = max(phase_count, 1)
        phases: list[dict[str, Any]] = []
        for index in range(1, count + 1):
            raw_label = (
                address.voltage.entities[index - 1].label
                if len(address.voltage.entities) >= index
                else ""
            )
            label = PowerMonitor.normalize_phase_label(
                index=index,
                total=count,
                raw_label=raw_label,
            )
            if state == "partial" and index == count:
                phases.append({"label": label, "voltage": None, "available": False})
            else:
                phases.append(
                    {
                        "label": label,
                        "voltage": 228.0 + ((index - 1) * 1.2),
                        "available": True,
                    }
                )

        single_voltage = phases[0]["voltage"] if len(phases) == 1 else None
        return single_voltage, phases

    async def get_editor_config(self) -> dict[str, Any]:
        """Return the editable runtime config payload for the ingress editor."""
        from config import runtime_config_revision

        stored = load_runtime_config_payload()
        editor_config = self._get_editor_runtime_config(stored)
        return {
            "revision": runtime_config_revision(stored),
            "restart_required": build_runtime_config_payload(editor_config) != build_runtime_config_payload(self.config),
            "test_chat_id": self.config.test_chat_id,
            "config_path": str(get_runtime_config_path()),
            "config": build_runtime_config_payload(editor_config),
            "preview": await self.preview_runtime_config(editor_config),
            "options": {
                "default_chat_id": self.tg.default_chat_id,
                "has_telegram_token": bool(self.config.telegram_bot_token),
            },
        }


async def main() -> None:
    """Entry point."""
    logger.info("Loading configuration...")
    config = load_config()

    has_chat_target = bool(config.telegram_chat_id) or any(
        addr.telegram_chat_id for addr in config.addresses
    )
    if not config.telegram_bot_token or not has_chat_target:
        logger.error(
            "telegram_bot_token and at least one telegram_chat_id must be configured"
        )
        sys.exit(1)

    if not config.addresses:
        logger.info("No addresses configured yet. Add an address in the web editor.")

    orchestrator = Orchestrator(config)
    ui_server = UIServer(orchestrator)

    async def shutdown_all() -> None:
        """Shut down UI and runtime services."""
        await ui_server.stop()
        await orchestrator.shutdown()

    # Handle signals for graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(shutdown_all()),
        )

    await ui_server.start()
    try:
        await orchestrator.run()
    finally:
        await ui_server.stop()
        await orchestrator.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

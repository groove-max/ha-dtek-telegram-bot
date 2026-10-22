"""Configuration models for DTEK Telegram Bot."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Literal

import yaml
from state_store import atomic_write
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

OPTIONS_PATH = Path("/data/options.json")
CONFIG_PATH = Path("/data/config.yaml")
CONFIG_PATH_HA = Path("/homeassistant/dtek_telegram_bot.yaml")  # accessible via File Editor / VS Code
DEFAULT_CONFIG_PATH = Path("/app/default_config.yaml")
RUNTIME_CONFIG_KEYS = ("receive_commands", "welcome_enabled", "ha_token", "export_default_templates", "addresses")


def runtime_config_revision(payload: dict | None = None) -> str:
    """Identify the persisted draft independently of live/resolved entity names."""
    if payload is None:
        payload = load_runtime_config_payload()
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class VoltageEntityConfig(BaseModel):
    """Single voltage sensor configuration."""

    entity: str
    label: str = ""


class PhaseEntityConfig(BaseModel):
    """Single phase sensor configuration."""

    entity: str
    label: str = ""


class ScheduleChangeConfig(BaseModel):
    enabled: bool = True
    silent: bool = True


class EmergencyConfig(BaseModel):
    enabled: bool = True
    silent: bool = False


class GroupChangeConfig(BaseModel):
    enabled: bool = True
    silent: bool = False


class VoltageConfig(BaseModel):
    enabled: bool = False
    entities: list[VoltageEntityConfig] = Field(default_factory=list)
    low: float = 195.0
    high: float = 245.0
    hysteresis: float = 5.0
    delay: int = 15
    max_age_seconds: int = Field(default=600, gt=0)
    present_above: float = Field(default=50.0, gt=0, allow_inf_nan=False)
    unavailable_as_missing: bool = False  # Deprecated: availability never proves loss.
    silent: bool = False

    @model_validator(mode="before")
    @classmethod
    def coerce_none_entities(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("entities") is None:
            data["entities"] = []
        return data

    @field_validator("delay")
    @classmethod
    def validate_delay(cls, v: int) -> int:
        if v < 0:
            raise ValueError("voltage.delay must be >= 0")
        return v

    @field_validator("hysteresis")
    @classmethod
    def validate_hysteresis(cls, v: float) -> float:
        if v < 0:
            raise ValueError("voltage.hysteresis must be >= 0")
        return v


class PowerSourceConfig(BaseModel):
    """A city-grid observation, independent of voltage quality monitoring."""

    entity: str
    kind: Literal["binary", "voltage", "frequency"]
    present_state: Literal["on", "off"] = "on"
    present_above: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    availability: Literal["always_on", "mains_powered"] = "always_on"
    freshness: Literal["state", "heartbeat"] = "heartbeat"
    max_age_seconds: int = Field(default=180, gt=0)
    mqtt_topic: str = ""

    @model_validator(mode="before")
    @classmethod
    def binary_freshness(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("kind") == "binary":
            return {"freshness": "state", **data}
        return data

    @model_validator(mode="after")
    def validate_source(self) -> "PowerSourceConfig":
        domain = "binary_sensor." if self.kind == "binary" else "sensor."
        if not self.entity.startswith(domain) or not self.entity.removeprefix(domain):
            raise ValueError(f"power source must use a {domain} entity")
        if self.kind != "binary" and self.freshness != "heartbeat":
            raise ValueError("Measurements require heartbeat freshness")
        if any(c in self.mqtt_topic for c in ("#", "+", "\x00")):
            raise ValueError("Power source MQTT topic must be exact")
        self.mqtt_topic = self.mqtt_topic.strip()
        if self.present_above is None:
            self.present_above = 50.0 if self.kind == "voltage" else 1.0
        return self


class PowerConfig(BaseModel):
    enabled: bool = False
    mode: str = "dtek_only"
    sources: list[PowerSourceConfig] = Field(default_factory=list, max_length=2)
    loss_entity: str = ""
    loss_mqtt_topic: str = ""
    loss_state: str = "0"
    loss_delay: int = 30
    confirm_timeout: int = 15
    restore_delay: int = 10
    silent: bool = False
    phase_notifications: bool = True
    telemetry_notifications: bool = False
    telemetry_loss_delay: int = Field(default=20, ge=0)

    @field_validator("loss_mqtt_topic")
    @classmethod
    def validate_loss_topic(cls, value: str) -> str:
        if any(c in value for c in ("#", "+", "\x00")):
            raise ValueError("power.loss_mqtt_topic must be an exact MQTT topic")
        return value.strip()

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        aliases = {
            "single_sensor": "loss_plus_voltage",
            "dual_sensor": "loss_plus_voltage",
        }
        v = aliases.get(v, v)
        allowed = ("dtek_only", "voltage_only", "loss_plus_voltage", "sources")
        if v not in allowed:
            msg = f"power.mode must be one of {allowed}, got '{v}'"
            raise ValueError(msg)
        return v

    @field_validator("loss_delay", "confirm_timeout", "restore_delay")
    @classmethod
    def validate_delays(cls, v: int, info: Any) -> int:
        if v < 0:
            raise ValueError(f"power.{info.field_name} must be >= 0")
        return v

    @model_validator(mode="after")
    def validate_entities(self) -> "PowerConfig":
        if not self.enabled:
            return self
        if self.mode == "loss_plus_voltage" and not self.loss_entity:
            raise ValueError(
                "power.loss_entity is required for power.mode='loss_plus_voltage'"
            )
        if self.mode == "sources":
            if not self.sources:
                raise ValueError("Choose at least one power source")
            if len({s.entity for s in self.sources}) != len(self.sources):
                raise ValueError("Power sources must use different entities")
            if self.sources[0].availability != "always_on":
                raise ValueError("The primary source must report grid loss while powered")
        return self


class UpcomingOutageConfig(BaseModel):
    enabled: bool = True
    minutes: int = 10
    silent: bool = False
    power_filter: str = "only_when_available"

    @field_validator("minutes")
    @classmethod
    def validate_minutes(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("upcoming_outage.minutes must be > 0")
        return v

    @field_validator("power_filter")
    @classmethod
    def validate_power_filter(cls, v: str) -> str:
        allowed = ("always", "only_when_available", "only_when_missing")
        if v not in allowed:
            raise ValueError(
                f"upcoming_outage.power_filter must be one of {allowed}"
            )
        return v


class StatusMessageConfig(BaseModel):
    enabled: bool = True
    update_interval: int = 300
    min_update_interval: int = 10
    delivery_mode: str = "pinned_edit"
    pin: bool = True
    silent: bool = True

    @field_validator("update_interval", "min_update_interval")
    @classmethod
    def validate_intervals(cls, v: int, info: Any) -> int:
        if v < 0:
            raise ValueError(f"status_message.{info.field_name} must be >= 0")
        return v

    @field_validator("delivery_mode")
    @classmethod
    def validate_delivery_mode(cls, v: str) -> str:
        allowed = ("pinned_edit", "send_new")
        if v not in allowed:
            raise ValueError(
                f"status_message.delivery_mode must be one of {allowed}"
            )
        return v


class AddressConfig(BaseModel):
    """Per-address configuration with nested feature settings."""

    entity_prefix: str = ""
    profile_id: str = ""
    mode: Literal["building", "personal"] = "building"
    account_entity: str = ""
    telegram_user_id: int = Field(default=0, ge=0)
    allow_readings: bool = False
    reading_reminders: bool = False
    document_notifications: bool = False

    @property
    def key(self) -> str:
        return self.profile_id or self.entity_prefix

    @model_validator(mode="after")
    def personal_identity(self):
        if self.profile_id and (len(self.profile_id) > 64 or not all(c.isalnum() or c in "_-" for c in self.profile_id)):
            raise ValueError("Invalid profile ID")
        if self.mode == "personal":
            if not self.account_entity.startswith("sensor.") or self.telegram_user_id <= 0:
                raise ValueError("Personal profiles require an account entity and Telegram user ID")
            if self.selected_group:
                raise ValueError("Personal groups are supplied by the cabinet")
            if self.telegram_chat_id and self.telegram_chat_id != str(self.telegram_user_id):
                raise ValueError("Personal account data can only be sent to its authorized private chat")
            self.telegram_chat_id = str(self.telegram_user_id)
            if not self.profile_id:
                self.profile_id = "personal_" + hashlib.sha256(f"{self.account_entity}:{self.telegram_bot_id}:{self.telegram_user_id}".encode()).hexdigest()[:20]
            self.entity_prefix = self.profile_id
        elif not self.entity_prefix:
            raise ValueError("Building profiles require an entity prefix")
        return self

    display_name: str = ""
    telegram_chat_id: str = ""
    telegram_bot_id: str = "default"
    selected_group: str = ""

    schedule_change: ScheduleChangeConfig = Field(default_factory=ScheduleChangeConfig)
    emergency: EmergencyConfig = Field(default_factory=EmergencyConfig)
    group_change: GroupChangeConfig = Field(default_factory=GroupChangeConfig)
    voltage: VoltageConfig = Field(default_factory=VoltageConfig)
    power: PowerConfig = Field(default_factory=PowerConfig)
    upcoming_outage: UpcomingOutageConfig = Field(default_factory=UpcomingOutageConfig)
    status_message: StatusMessageConfig = Field(default_factory=StatusMessageConfig)

    @field_validator("telegram_bot_id")
    @classmethod
    def normalize_bot_id(cls, value: str) -> str:
        return value.strip() or "default"

    @model_validator(mode="before")
    @classmethod
    def coerce_shortcuts(cls, data: Any) -> Any:
        """Support shorthand: `schedule_change: true` -> `{enabled: true}`."""
        if isinstance(data, dict):
            for key in ("schedule_change", "emergency", "group_change"):
                val = data.get(key)
                if isinstance(val, bool):
                    data[key] = {"enabled": val}
            cls._migrate_legacy_power_voltage(data)
        return data

    @staticmethod
    def _migrate_legacy_power_voltage(data: dict[str, Any]) -> None:
        """Fold legacy power confirm/phase fields into shared voltage topology."""
        voltage = dict(data.get("voltage") or {})
        power = dict(data.get("power") or {})

        entities: list[dict[str, str]] = list(voltage.get("entities") or [])

        def append_entity(entity_id: str, label: str = "") -> None:
            entity_id = str(entity_id or "").strip()
            if not entity_id:
                return
            for item in entities:
                if str(item.get("entity", "")).strip() == entity_id:
                    if label and not str(item.get("label", "")).strip():
                        item["label"] = label
                    return
            entities.append({"entity": entity_id, "label": label})

        legacy_confirm = str(power.get("confirm_entity", "")).strip()
        if legacy_confirm:
            append_entity(legacy_confirm, "L1")

        legacy_phases = power.get("phase_entities") or []
        for index, item in enumerate(legacy_phases, start=1):
            if not isinstance(item, dict):
                continue
            append_entity(
                str(item.get("entity", "")).strip(),
                str(item.get("label", "")).strip() or f"L{index}",
            )

        if entities:
            voltage["entities"] = entities
            voltage["enabled"] = bool(voltage.get("enabled", True))

        if "phase_threshold" in power and "present_above" not in voltage:
            voltage["present_above"] = power.get("phase_threshold")

        if power.get("mode") == "single_sensor":
            power["mode"] = "loss_plus_voltage"
        elif power.get("mode") == "dual_sensor":
            power["mode"] = "loss_plus_voltage"
        elif power.get("mode") == "dtek_only":
            power["mode"] = "dtek_only"

        data["voltage"] = voltage
        data["power"] = power

    @model_validator(mode="after")
    def validate_power_voltage_relationship(self) -> "AddressConfig":
        if self.power.enabled and self.power.mode in (
            "voltage_only",
            "loss_plus_voltage",
        ):
            if not self.voltage.entities:
                raise ValueError(
                    "voltage.entities is required for voltage-based power modes"
                )
        return self


class FullConfig(BaseModel):
    """Complete add-on configuration."""

    model_config = ConfigDict(hide_input_in_errors=True)

    # From options.json
    telegram_bot_token: str = Field(default="", repr=False)
    telegram_chat_id: str = ""
    ha_token: str = Field(default="", repr=False)
    test_chat_id: str = ""
    feedback_enabled: bool = False

    # From config.yaml
    receive_commands: bool = True
    welcome_enabled: bool = True
    export_default_templates: bool = False
    addresses: list[AddressConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_addresses(self) -> "FullConfig":
        prefixes = [address.key for address in self.addresses]
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("Each profile ID (or legacy entity_prefix) must appear only once")
        return self


def load_options() -> dict[str, Any]:
    """Load Supervisor-managed add-on options."""
    data: dict[str, Any] = {}
    if not OPTIONS_PATH.exists():
        return data

    try:
        with open(OPTIONS_PATH, encoding="utf-8") as f:
            options = json.load(f)
        data["telegram_bot_token"] = options.get("telegram_bot_token", "")
        data["telegram_chat_id"] = options.get("telegram_chat_id", "")
        data["test_chat_id"] = options.get("test_chat_id", "")
        data["feedback_enabled"] = options.get("feedback_enabled", False)
        logger.info("Loaded options.json")
    except Exception:
        logger.exception("Failed to load options.json")

    return data


def load_runtime_config_payload() -> dict[str, Any]:
    """Load only the editable runtime YAML payload."""
    config_path = _resolve_config_path()
    if not config_path or not config_path.exists():
        return {
            "ha_token": "",
            "export_default_templates": False,
            "addresses": [],
        }

    try:
        with open(config_path, encoding="utf-8") as f:
            config_yaml = yaml.safe_load(f) or {}
        return {
            "ha_token": config_yaml.get("ha_token", ""),
            "receive_commands": config_yaml.get("receive_commands", True),
            "welcome_enabled": config_yaml.get("welcome_enabled", True),
            "export_default_templates": config_yaml.get(
                "export_default_templates", False
            ),
            "addresses": config_yaml.get("addresses", []),
        }
    except Exception:
        logger.exception("Failed to load %s", config_path)
        return {
            "ha_token": "",
            "export_default_templates": False,
            "addresses": [],
        }


def load_config() -> FullConfig:
    """Load and merge configuration from options.json and config.yaml."""
    data: dict[str, Any] = {}

    # Load options.json (managed by HA Supervisor)
    data.update(load_options())

    # Load config.yaml — try HA config dir first, then /data/
    config_path = _resolve_config_path()
    if config_path and config_path.exists():
        data.update(load_runtime_config_payload())
        logger.info(
            "Loaded config from %s with %d addresses",
            config_path,
            len(data.get("addresses", [])),
        )
    else:
        # Create default in both locations
        _create_default_config()

    return FullConfig(**data)


def build_runtime_config_payload(config: FullConfig) -> dict[str, Any]:
    """Serialize only the editable YAML subset of the full config."""
    return {
        "ha_token": config.ha_token,
        "export_default_templates": config.export_default_templates,
        "receive_commands": config.receive_commands,
        "welcome_enabled": config.welcome_enabled,
        "addresses": [address.model_dump() for address in config.addresses],
    }


def validate_runtime_config_payload(
    payload: dict[str, Any],
    *,
    options: dict[str, Any] | None = None,
) -> FullConfig:
    """Validate runtime YAML payload by merging it with add-on options."""
    merged = dict(options or load_options())
    for key in RUNTIME_CONFIG_KEYS:
        if key in payload:
            merged[key] = payload[key]
    merged.setdefault("ha_token", "")
    merged.setdefault("export_default_templates", False)
    merged.setdefault("addresses", [])
    return FullConfig(**merged)


def get_runtime_config_path() -> Path:
    """Return the preferred writable runtime config path."""
    if CONFIG_PATH_HA.parent.exists():
        return CONFIG_PATH_HA
    return CONFIG_PATH


def save_runtime_config_payload(payload: dict[str, Any], path: Path | None = None) -> Path:
    """Write the editable runtime YAML payload to disk."""
    target = path or get_runtime_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = {
        "ha_token": payload.get("ha_token", ""),
        "export_default_templates": bool(payload.get("export_default_templates", False)),
        "receive_commands": payload.get("receive_commands", True),
        "welcome_enabled": payload.get("welcome_enabled", True),
        "addresses": payload.get("addresses", []),
    }
    atomic_write(target, yaml.safe_dump(normalized, sort_keys=False, allow_unicode=True,
                                        default_flow_style=False))
    logger.info("Saved runtime config to %s", target)
    return target


def _resolve_config_path() -> Path | None:
    """Find config.yaml: prefer HA config dir, fallback to /data/."""
    if CONFIG_PATH_HA.exists():
        return CONFIG_PATH_HA
    if CONFIG_PATH.exists():
        if CONFIG_PATH_HA.parent.exists():
            # Preserve the original fallback; publish the complete existing file atomically.
            atomic_write(CONFIG_PATH_HA, CONFIG_PATH.read_text(encoding="utf-8"))
            return CONFIG_PATH_HA
        return CONFIG_PATH
    return None


def _create_default_config() -> None:
    """Copy default config to HA config dir and /data/ on first run."""
    if not DEFAULT_CONFIG_PATH.exists():
        return
    # Copy to HA config dir (accessible via File Editor / VS Code)
    if CONFIG_PATH_HA.parent.exists():
        try:
            shutil.copy2(DEFAULT_CONFIG_PATH, CONFIG_PATH_HA)
            logger.info("Created default config at %s", CONFIG_PATH_HA)
            logger.info(
                "Edit this file via File Editor or VS Code Server add-on"
            )
        except Exception:
            logger.exception("Failed to create %s", CONFIG_PATH_HA)
    # Also copy to /data/ as fallback
    try:
        shutil.copy2(DEFAULT_CONFIG_PATH, CONFIG_PATH)
        logger.info("Created default config at %s", CONFIG_PATH)
    except Exception:
        logger.exception("Failed to create %s", CONFIG_PATH)

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from datetime import timedelta
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace

HAS_RUNTIME_DEPS = find_spec("pydantic") is not None and find_spec("yaml") is not None

APP_ROOT = Path(__file__).resolve().parents[1] / "rootfs" / "app"
sys.path.insert(0, str(APP_ROOT))

if "aiogram" not in sys.modules:
    aiogram = types.ModuleType("aiogram")
    aiogram.Bot = object
    aiogram_enums = types.ModuleType("aiogram.enums")
    aiogram_enums.ParseMode = object
    aiogram_exceptions = types.ModuleType("aiogram.exceptions")
    aiogram_exceptions.TelegramBadRequest = Exception
    aiogram_exceptions.TelegramRetryAfter = Exception
    sys.modules["aiogram"] = aiogram
    sys.modules["aiogram.enums"] = aiogram_enums
    sys.modules["aiogram.exceptions"] = aiogram_exceptions

if "jinja2" not in sys.modules:
    jinja2 = types.ModuleType("jinja2")

    class _DummyBaseLoader:
        pass

    class _DummyDictLoader:
        def __init__(self, defaults: dict[str, str]) -> None:
            self._defaults = defaults

        def get_source(self, environment: object, template: str) -> tuple[str, str | None, object]:
            return self._defaults.get(template, ""), None, lambda: True

    class _DummyTemplate:
        def render(self, **context: object) -> str:
            return ""

    class _DummyEnvironment:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.filters: dict[str, object] = {}
            self.cache: dict[str, object] = {}

        def get_template(self, template_name: str) -> _DummyTemplate:
            return _DummyTemplate()

        def from_string(self, source: str) -> _DummyTemplate:
            return _DummyTemplate()

    class _DummyTemplateNotFound(Exception):
        pass

    jinja2.BaseLoader = _DummyBaseLoader
    jinja2.DictLoader = _DummyDictLoader
    jinja2.Environment = _DummyEnvironment
    jinja2.StrictUndefined = object
    jinja2.TemplateNotFound = _DummyTemplateNotFound
    sys.modules["jinja2"] = jinja2

if HAS_RUNTIME_DEPS:
    from features.power_presence import PowerPresenceFeature
    from power_monitor import PowerMonitor
    from utils import now_kyiv


class DummyHA:
    def __init__(self, states: dict[str, dict[str, str]]) -> None:
        self.states = states

    async def get_state(self, entity_id: str) -> dict[str, str] | None:
        return self.states.get(entity_id)


class DummyTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, text: str, **kwargs: object) -> int:
        self.messages.append(text)
        return len(self.messages)


class DummyState:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], object] = {}

    def get(self, prefix: str, key: str, default: object = None) -> object:
        return self._data.get((prefix, key), default)

    def set(self, prefix: str, key: str, value: object) -> None:
        self._data[(prefix, key)] = value


class DummyTemplates:
    def render(self, template_name: str, **context: object) -> str:
        return f"{template_name}|duration={context.get('duration')}|state={context.get('house_state')}"


@unittest.skipUnless(HAS_RUNTIME_DEPS, "PyYAML and pydantic are not installed in the local test environment")
class PowerPresenceFeatureTest(unittest.IsolatedAsyncioTestCase):
    prefix = "doroga_liustdorfska_56v"
    loss_entity = "sensor.solar2mqtt_ac_in_frequenz"
    voltage_entity = "sensor.atorch_smart_energy_meter_at2pl_voltage"

    def _build_feature(
        self,
        *,
        confirm_timeout: float = 0.02,
        loss_delay: float = 0.0,
    ) -> tuple[PowerPresenceFeature, PowerMonitor, DummyHA, DummyTelegram, DummyState]:
        old_voltage_time = (now_kyiv() - timedelta(minutes=2)).isoformat()
        states = {
            self.loss_entity: {"state": "50", "last_updated": now_kyiv().isoformat()},
            self.voltage_entity: {"state": "228.4", "last_updated": old_voltage_time},
            f"binary_sensor.{self.prefix}_power": {
                "state": "on",
                "last_updated": now_kyiv().isoformat(),
            },
        }
        ha = DummyHA(states)
        telegram = DummyTelegram()
        state = DummyState()
        config = SimpleNamespace(
            entity_prefix=self.prefix,
            display_name="Одеса • дорога Люстдорфська, 56В",
            telegram_chat_id="",
            power=SimpleNamespace(
                enabled=True,
                mode="loss_plus_voltage",
                loss_entity=self.loss_entity,
                loss_state="0",
                loss_delay=loss_delay,
                confirm_timeout=confirm_timeout,
                restore_delay=0,
                silent=False,
                phase_notifications=True,
            ),
            voltage=SimpleNamespace(
                enabled=True,
                entities=[SimpleNamespace(entity=self.voltage_entity, label="L1")],
                present_above=50,
                unavailable_as_missing=True,
            ),
        )
        feature = PowerPresenceFeature(
            address_config=config,
            ha_client=ha,
            telegram=telegram,
            state_store=state,
            templates=DummyTemplates(),
        )
        monitor = PowerMonitor(config, ha, state)
        feature.set_power_monitor(monitor)
        return feature, monitor, ha, telegram, state

    async def test_loss_plus_voltage_confirms_outage_when_voltage_never_refreshes(self) -> None:
        feature, _monitor, ha, telegram, state = self._build_feature()

        await feature.on_start()
        loss_state = {"state": "0", "last_updated": now_kyiv().isoformat()}
        ha.states[self.loss_entity] = loss_state

        await feature.on_state_change(
            self.loss_entity,
            {"state": "50", "last_updated": now_kyiv().isoformat()},
            loss_state,
        )
        await asyncio.sleep(0.08)

        self.assertEqual(state.get(self.prefix, "power_state"), "off")
        self.assertIsNotNone(state.get(self.prefix, "loss_stale_reference_at"))
        self.assertEqual(len(telegram.messages), 1)
        self.assertIn("power_lost", telegram.messages[0])

    async def test_loss_plus_voltage_cancels_confirmation_on_fresh_voltage_update(self) -> None:
        feature, _monitor, ha, telegram, state = self._build_feature(confirm_timeout=0.05)

        await feature.on_start()
        loss_state = {"state": "0", "last_updated": now_kyiv().isoformat()}
        ha.states[self.loss_entity] = loss_state

        await feature.on_state_change(
            self.loss_entity,
            {"state": "50", "last_updated": now_kyiv().isoformat()},
            loss_state,
        )

        await asyncio.sleep(0.01)
        fresh_voltage = {"state": "227.9", "last_updated": now_kyiv().isoformat()}
        ha.states[self.voltage_entity] = fresh_voltage
        await feature.on_state_change(
            self.voltage_entity,
            {"state": "228.4", "last_updated": (now_kyiv() - timedelta(minutes=2)).isoformat()},
            fresh_voltage,
        )
        await asyncio.sleep(0.08)

        self.assertEqual(state.get(self.prefix, "power_state"), "on")
        self.assertIsNone(state.get(self.prefix, "loss_stale_reference_at"))
        self.assertEqual(telegram.messages, [])

    async def test_voltage_unavailable_without_loss_trigger_does_not_send_false_outage(self) -> None:
        feature, _monitor, ha, telegram, state = self._build_feature(loss_delay=0.0)

        await feature.on_start()
        unavailable_voltage = {"state": "unavailable", "last_updated": now_kyiv().isoformat()}
        ha.states[self.voltage_entity] = unavailable_voltage

        await feature.on_state_change(
            self.voltage_entity,
            {"state": "228.4", "last_updated": (now_kyiv() - timedelta(seconds=5)).isoformat()},
            unavailable_voltage,
        )
        await asyncio.sleep(0.03)
        await feature.on_tick()

        self.assertEqual(state.get(self.prefix, "power_state"), "on")
        self.assertEqual(telegram.messages, [])


if __name__ == "__main__":
    unittest.main()

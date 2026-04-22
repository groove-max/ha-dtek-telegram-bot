from __future__ import annotations

import asyncio
import sys
import types
import unittest
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
    from features.voltage import VoltageFeature


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
        return (
            f"{template_name}|voltage={context.get('voltage')}"
            f"|phase={context.get('phase_label')}"
        )


@unittest.skipUnless(HAS_RUNTIME_DEPS, "PyYAML and pydantic are not installed in the local test environment")
class VoltageFeatureTest(unittest.IsolatedAsyncioTestCase):
    prefix = "demo_prefix"
    voltage_entity = "sensor.demo_voltage"

    def _build_feature(
        self,
        *,
        low: float = 195.0,
        high: float = 250.0,
        hysteresis: float = 5.0,
        delay: float = 0.0,
    ) -> tuple[VoltageFeature, DummyHA, DummyTelegram, DummyState]:
        ha = DummyHA({self.voltage_entity: {"state": "228.0"}})
        telegram = DummyTelegram()
        state = DummyState()
        config = SimpleNamespace(
            entity_prefix=self.prefix,
            display_name="Одеса • дорога Люстдорфська, 56В",
            telegram_chat_id="",
            voltage=SimpleNamespace(
                enabled=True,
                entities=[SimpleNamespace(entity=self.voltage_entity, label="L1")],
                low=low,
                high=high,
                hysteresis=hysteresis,
                delay=delay,
                present_above=50.0,
                silent=False,
            ),
        )
        feature = VoltageFeature(
            address_config=config,
            ha_client=ha,
            telegram=telegram,
            state_store=state,
            templates=DummyTemplates(),
        )
        return feature, ha, telegram, state

    async def test_high_voltage_normalizes_only_after_hysteresis_margin(self) -> None:
        feature, ha, telegram, state = self._build_feature(high=250.0, hysteresis=5.0)

        high_state = {"state": "250.3"}
        ha.states[self.voltage_entity] = high_state
        await feature.on_state_change(self.voltage_entity, {"state": "249.0"}, high_state)
        await asyncio.sleep(0.01)

        self.assertEqual(telegram.messages, ["voltage_high|voltage=250.3|phase=L1"])
        self.assertTrue(state.get(self.prefix, f"voltage_alert_{self.voltage_entity}"))
        self.assertEqual(
            state.get(self.prefix, f"voltage_alert_type_{self.voltage_entity}"),
            "high",
        )

        near_threshold = {"state": "249.1"}
        ha.states[self.voltage_entity] = near_threshold
        await feature.on_state_change(self.voltage_entity, high_state, near_threshold)
        await asyncio.sleep(0.01)

        self.assertEqual(len(telegram.messages), 1)
        self.assertTrue(state.get(self.prefix, f"voltage_alert_{self.voltage_entity}"))

        recovered = {"state": "244.8"}
        ha.states[self.voltage_entity] = recovered
        await feature.on_state_change(self.voltage_entity, near_threshold, recovered)

        self.assertEqual(
            telegram.messages,
            [
                "voltage_high|voltage=250.3|phase=L1",
                "voltage_normal|voltage=244.8|phase=L1",
            ],
        )
        self.assertFalse(state.get(self.prefix, f"voltage_alert_{self.voltage_entity}"))

    async def test_low_voltage_normalizes_only_after_hysteresis_margin(self) -> None:
        feature, ha, telegram, state = self._build_feature(low=195.0, hysteresis=5.0)

        low_state = {"state": "190.2"}
        ha.states[self.voltage_entity] = low_state
        await feature.on_state_change(self.voltage_entity, {"state": "196.0"}, low_state)
        await asyncio.sleep(0.01)

        self.assertEqual(telegram.messages, ["voltage_low|voltage=190.2|phase=L1"])
        self.assertTrue(state.get(self.prefix, f"voltage_alert_{self.voltage_entity}"))

        near_threshold = {"state": "197.0"}
        ha.states[self.voltage_entity] = near_threshold
        await feature.on_state_change(self.voltage_entity, low_state, near_threshold)

        self.assertEqual(len(telegram.messages), 1)
        self.assertTrue(state.get(self.prefix, f"voltage_alert_{self.voltage_entity}"))

        recovered = {"state": "200.2"}
        ha.states[self.voltage_entity] = recovered
        await feature.on_state_change(self.voltage_entity, near_threshold, recovered)

        self.assertEqual(
            telegram.messages,
            [
                "voltage_low|voltage=190.2|phase=L1",
                "voltage_normal|voltage=200.2|phase=L1",
            ],
        )
        self.assertFalse(state.get(self.prefix, f"voltage_alert_{self.voltage_entity}"))

    async def test_zero_hysteresis_keeps_legacy_normalization_behavior(self) -> None:
        feature, ha, telegram, _state = self._build_feature(high=250.0, hysteresis=0.0)

        high_state = {"state": "250.5"}
        ha.states[self.voltage_entity] = high_state
        await feature.on_state_change(self.voltage_entity, {"state": "249.0"}, high_state)
        await asyncio.sleep(0.01)

        recovered = {"state": "249.9"}
        ha.states[self.voltage_entity] = recovered
        await feature.on_state_change(self.voltage_entity, high_state, recovered)

        self.assertEqual(
            telegram.messages,
            [
                "voltage_high|voltage=250.5|phase=L1",
                "voltage_normal|voltage=249.9|phase=L1",
            ],
        )

    async def test_zero_voltage_is_missing_phase_not_low_voltage_alert(self) -> None:
        feature, ha, telegram, state = self._build_feature(
            low=195.0,
            hysteresis=5.0,
        )

        missing_state = {"state": "0.0"}
        ha.states[self.voltage_entity] = missing_state
        await feature.on_state_change(
            self.voltage_entity,
            {"state": "224.0"},
            missing_state,
        )
        await asyncio.sleep(0.01)

        recovered_state = {"state": "224.9"}
        ha.states[self.voltage_entity] = recovered_state
        await feature.on_state_change(
            self.voltage_entity,
            missing_state,
            recovered_state,
        )

        self.assertEqual(telegram.messages, [])
        self.assertFalse(state.get(self.prefix, f"voltage_alert_{self.voltage_entity}"))

    async def test_voltage_below_presence_threshold_clears_active_alert_silently(self) -> None:
        feature, ha, telegram, state = self._build_feature(
            low=195.0,
            hysteresis=5.0,
        )

        low_state = {"state": "190.0"}
        ha.states[self.voltage_entity] = low_state
        await feature.on_state_change(
            self.voltage_entity,
            {"state": "224.0"},
            low_state,
        )
        await asyncio.sleep(0.01)

        missing_state = {"state": "0.0"}
        ha.states[self.voltage_entity] = missing_state
        await feature.on_state_change(
            self.voltage_entity,
            low_state,
            missing_state,
        )

        recovered_state = {"state": "224.9"}
        ha.states[self.voltage_entity] = recovered_state
        await feature.on_state_change(
            self.voltage_entity,
            missing_state,
            recovered_state,
        )

        self.assertEqual(telegram.messages, ["voltage_low|voltage=190.0|phase=L1"])
        self.assertFalse(state.get(self.prefix, f"voltage_alert_{self.voltage_entity}"))


if __name__ == "__main__":
    unittest.main()

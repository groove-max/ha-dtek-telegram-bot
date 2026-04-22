from __future__ import annotations

import asyncio
import sys
import unittest
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
import types

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
    from features.emergency import EmergencyFeature


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
            f"{template_name}|"
            f"type={context.get('outage_type')}|"
            f"description={context.get('description')}|"
            f"start={context.get('start')}|"
            f"end={context.get('end')}|"
            f"old_reason={context.get('old_reason')}|"
            f"new_reason={context.get('new_reason')}|"
            f"old_end={context.get('old_end')}|"
            f"new_end={context.get('new_end')}"
        )


@unittest.skipUnless(HAS_RUNTIME_DEPS, "PyYAML and pydantic are not installed in the local test environment")
class EmergencyFeatureTest(unittest.IsolatedAsyncioTestCase):
    def _build_feature(self) -> tuple[EmergencyFeature, DummyHA, DummyTelegram, DummyState]:
        prefix = "doroga_liustdorfska_56v"
        states = {
            f"sensor.{prefix}_outage_status": {"state": "emergency"},
            f"sensor.{prefix}_outage_description": {"state": "Аварійні ремонтні роботи"},
            f"sensor.{prefix}_outage_start": {"state": "2026-03-09T05:17:00+00:00"},
            f"sensor.{prefix}_outage_end": {"state": "2026-03-09T11:56:00+00:00"},
        }
        ha = DummyHA(states)
        telegram = DummyTelegram()
        state = DummyState()
        feature = EmergencyFeature(
            address_config=SimpleNamespace(
                entity_prefix=prefix,
                display_name="Одеса • дорога Люстдорфська, 56В",
                telegram_chat_id="",
                emergency=SimpleNamespace(enabled=True, silent=False),
                power=SimpleNamespace(enabled=True),
            ),
            ha_client=ha,
            telegram=telegram,
            state_store=state,
            templates=DummyTemplates(),
        )
        feature.update_batch_delay = 0.01
        return feature, ha, telegram, state

    async def test_description_change_during_active_outage_sends_update(self) -> None:
        feature, ha, telegram, state = self._build_feature()
        state.set(feature.config.entity_prefix, "last_emergency_status", "emergency")
        state.set(
            feature.config.entity_prefix,
            "last_outage_description",
            "Аварійні ремонтні роботи",
        )
        ha.states[feature.entity("outage_description")] = {
            "state": "Екстренні відключення (Аварійне без застосування графіку погодинних відключень)"
        }

        await feature.on_state_change(
            feature.entity("outage_description"),
            {"state": "Аварійні ремонтні роботи"},
            {"state": "Екстренні відключення (Аварійне без застосування графіку погодинних відключень)"},
        )
        await asyncio.sleep(0.03)

        self.assertEqual(len(telegram.messages), 1)
        self.assertTrue(telegram.messages[0].startswith("emergency_update|"))
        self.assertIn("Екстренні відключення", telegram.messages[0])

    async def test_initial_description_echo_does_not_duplicate_start_message(self) -> None:
        feature, ha, telegram, state = self._build_feature()
        state.set(feature.config.entity_prefix, "last_emergency_status", "emergency")
        state.set(
            feature.config.entity_prefix,
            "last_outage_description",
            "Аварійні ремонтні роботи",
        )
        ha.states[feature.entity("outage_description")] = {
            "state": "Аварійні ремонтні роботи"
        }

        await feature.on_state_change(
            feature.entity("outage_description"),
            {"state": "unknown"},
            {"state": "Аварійні ремонтні роботи"},
        )

        self.assertEqual(telegram.messages, [])

    async def test_on_start_seeds_active_outage_state_from_ha(self) -> None:
        feature, ha, telegram, state = self._build_feature()

        await feature.on_start()

        self.assertEqual(
            state.get(feature.config.entity_prefix, "last_emergency_status"),
            "emergency",
        )
        self.assertEqual(
            state.get(feature.config.entity_prefix, "last_outage_end"),
            "2026-03-09T11:56:00+00:00",
        )
        self.assertEqual(
            state.get(feature.config.entity_prefix, "last_outage_start"),
            "2026-03-09T05:17:00+00:00",
        )
        self.assertEqual(
            state.get(feature.config.entity_prefix, "last_outage_description"),
            "Аварійні ремонтні роботи",
        )
        self.assertEqual(
            state.get(feature.config.entity_prefix, "outage_start_time"),
            "2026-03-09T05:17:00+00:00",
        )
        self.assertIsNotNone(
            state.get(feature.config.entity_prefix, "last_emergency_context_at")
        )
        self.assertEqual(telegram.messages, [])

    async def test_outage_end_becoming_available_after_unknown_sends_update(self) -> None:
        feature, ha, telegram, state = self._build_feature()
        state.set(feature.config.entity_prefix, "last_emergency_status", "emergency")
        state.set(feature.config.entity_prefix, "last_outage_end", None)
        ha.states[feature.entity("outage_end")] = {
            "state": "2026-03-09T19:30:00+00:00"
        }

        await feature.on_state_change(
            feature.entity("outage_end"),
            {"state": "unknown"},
            {"state": "2026-03-09T19:30:00+00:00"},
        )
        await asyncio.sleep(0.03)

        self.assertEqual(len(telegram.messages), 1)
        self.assertTrue(telegram.messages[0].startswith("emergency_update|"))
        self.assertIn("09.03.2026 21:30", telegram.messages[0])

    async def test_outage_start_becoming_available_after_unknown_sends_snapshot(self) -> None:
        feature, ha, telegram, state = self._build_feature()
        state.set(feature.config.entity_prefix, "last_emergency_status", "emergency")
        state.set(feature.config.entity_prefix, "last_outage_start", None)
        state.set(feature.config.entity_prefix, "outage_start_time", None)
        ha.states[feature.entity("outage_start")] = {
            "state": "2026-03-09T05:17:00+00:00"
        }

        await feature.on_state_change(
            feature.entity("outage_start"),
            {"state": "unknown"},
            {"state": "2026-03-09T05:17:00+00:00"},
        )

        self.assertEqual(len(telegram.messages), 1)
        self.assertIn("09.03.2026 07:17", telegram.messages[0])
        self.assertEqual(
            state.get(feature.config.entity_prefix, "last_outage_start"),
            "2026-03-09T05:17:00+00:00",
        )
        self.assertEqual(
            state.get(feature.config.entity_prefix, "outage_start_time"),
            "2026-03-09T05:17:00+00:00",
        )

    async def test_duplicate_start_snapshot_is_suppressed(self) -> None:
        feature, ha, telegram, state = self._build_feature()
        ha.states[feature.entity("status")] = {"state": "emergency"}

        await feature.on_state_change(
            feature.entity("status"),
            {"state": "ok"},
            {"state": "emergency"},
        )
        await feature.on_state_change(
            feature.entity("status"),
            {"state": "ok"},
            {"state": "emergency"},
        )

        self.assertEqual(len(telegram.messages), 1)
        self.assertTrue(telegram.messages[0].startswith("emergency_start|"))
        self.assertIsNotNone(
            state.get(feature.config.entity_prefix, "last_emergency_snapshot_key")
        )

    async def test_duplicate_outage_end_after_snapshot_is_suppressed(self) -> None:
        feature, ha, telegram, state = self._build_feature()
        state.set(feature.config.entity_prefix, "last_emergency_status", "emergency")
        state.set(feature.config.entity_prefix, "last_outage_end", "2026-03-09T19:30:00+00:00")

        await feature.on_state_change(
            feature.entity("outage_end"),
            {"state": "2026-03-09T11:56:00+00:00"},
            {"state": "2026-03-09T19:30:00+00:00"},
        )
        await asyncio.sleep(0.03)

        self.assertEqual(telegram.messages, [])

    async def test_new_local_power_loss_still_keeps_outage_end_as_update(self) -> None:
        feature, ha, telegram, state = self._build_feature()
        state.set(feature.config.entity_prefix, "last_emergency_status", "emergency")
        state.set(feature.config.entity_prefix, "last_outage_end", "2026-03-09T11:56:00+00:00")
        state.set(feature.config.entity_prefix, "power_state", "off")
        state.set(feature.config.entity_prefix, "power_last_change", "2026-03-09T12:26:00+00:00")
        state.set(
            feature.config.entity_prefix,
            "last_emergency_context_at",
            "2026-03-09T12:21:00+00:00",
        )
        ha.states[feature.entity("outage_end")] = {
            "state": "2026-03-09T19:30:00+00:00"
        }

        await feature.on_state_change(
            feature.entity("outage_end"),
            {"state": "2026-03-09T11:56:00+00:00"},
            {"state": "2026-03-09T19:30:00+00:00"},
        )
        await asyncio.sleep(0.03)

        self.assertEqual(len(telegram.messages), 1)
        self.assertTrue(telegram.messages[0].startswith("emergency_update|"))

    async def test_type_and_end_change_are_batched_into_one_message(self) -> None:
        feature, ha, telegram, state = self._build_feature()
        state.set(feature.config.entity_prefix, "last_emergency_status", "planned")
        state.set(
            feature.config.entity_prefix,
            "last_outage_description",
            "Аварійні ремонтні роботи",
        )
        state.set(
            feature.config.entity_prefix,
            "last_outage_end",
            "2026-03-09T11:56:00+00:00",
        )
        ha.states[feature.entity("status")] = {"state": "emergency"}
        ha.states[feature.entity("outage_description")] = {
            "state": "Екстренні відключення (Аварійне без застосування графіку погодинних відключень)"
        }
        ha.states[feature.entity("outage_end")] = {
            "state": "2026-03-09T19:30:00+00:00"
        }

        await feature.on_state_change(
            feature.entity("status"),
            {"state": "planned"},
            {"state": "emergency"},
        )
        await feature.on_state_change(
            feature.entity("outage_description"),
            {"state": "Аварійні ремонтні роботи"},
            {"state": "Екстренні відключення (Аварійне без застосування графіку погодинних відключень)"},
        )
        await feature.on_state_change(
            feature.entity("outage_end"),
            {"state": "2026-03-09T11:56:00+00:00"},
            {"state": "2026-03-09T19:30:00+00:00"},
        )
        await asyncio.sleep(0.03)

        self.assertEqual(len(telegram.messages), 1)
        self.assertTrue(telegram.messages[0].startswith("emergency_update|"))
        self.assertIn("new_reason=Екстренні відключення", telegram.messages[0])
        self.assertIn("new_end=09.03.2026 21:30", telegram.messages[0])

    async def test_end_change_only_uses_emergency_update_template(self) -> None:
        feature, ha, telegram, state = self._build_feature()
        state.set(feature.config.entity_prefix, "last_emergency_status", "emergency")
        state.set(
            feature.config.entity_prefix,
            "last_outage_end",
            "2026-03-09T11:56:00+00:00",
        )
        ha.states[feature.entity("outage_end")] = {
            "state": "2026-03-09T19:30:00+00:00"
        }

        await feature.on_state_change(
            feature.entity("outage_end"),
            {"state": "2026-03-09T11:56:00+00:00"},
            {"state": "2026-03-09T19:30:00+00:00"},
        )
        await asyncio.sleep(0.03)

        self.assertEqual(len(telegram.messages), 1)
        self.assertTrue(telegram.messages[0].startswith("emergency_update|"))
        self.assertIn("new_end=09.03.2026 21:30", telegram.messages[0])


if __name__ == "__main__":
    unittest.main()

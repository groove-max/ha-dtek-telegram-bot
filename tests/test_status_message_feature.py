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
    from features.status_message import StatusMessageFeature


class DummyHA:
    def __init__(self, states: dict[str, dict[str, str]] | None = None) -> None:
        self.states = states or {}

    async def get_state(self, entity_id: str) -> dict[str, str] | None:
        return self.states.get(entity_id)

    async def call_service(self, **kwargs: object) -> object:
        return {"success": True, "result": {"response": {kwargs["entity_id"]: {"events": []}}}}


class DummyTelegram:
    def __init__(self, *, edit_result: str) -> None:
        self.edit_result = edit_result
        self.sent_messages: list[str] = []
        self.sent_kwargs: list[dict[str, object]] = []
        self.edit_calls = 0

    async def send_message(self, text: str, **kwargs: object) -> int | None:
        self.sent_messages.append(text)
        self.sent_kwargs.append(dict(kwargs))
        return 44

    async def edit_message_result(self, message_id: int, text: str, **kwargs: object) -> str:
        self.edit_calls += 1
        return self.edit_result

    async def edit_message(self, message_id: int, text: str, **kwargs: object) -> bool:
        self.edit_calls += 1
        return self.edit_result == "ok"

    async def pin_message(self, message_id: int, **kwargs: object) -> bool:
        self.pinned_message_id = message_id
        return True


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
            f"{context.get('short_name') or context.get('display_name')}|"
            f"group={context.get('group')}"
        )


class DummyPowerMonitor:
    async def get_power_state(self) -> str:
        return "on"

    async def get_voltage_snapshot(self) -> dict[str, object]:
        return {
            "single_voltage": 227.1,
            "phases": [{"label": "L1", "voltage": 227.1, "available": True}],
            "missing_phases": [],
            "unknown_phases": [],
        }


@unittest.skipUnless(HAS_RUNTIME_DEPS, "PyYAML and pydantic are not installed in the local test environment")
class StatusMessageFeatureTest(unittest.IsolatedAsyncioTestCase):
    prefix = "vulytsia_prykladna_12a"

    async def test_enabling_pin_pins_existing_status_without_replacing_it(self):
        feature, telegram, state = self._build_feature(edit_result="ok")
        await feature.on_start()
        self.assertEqual(telegram.pinned_message_id, 33)
        self.assertEqual(state.get(self.prefix, "status_pinned_message_id"), 33)
        self.assertEqual(telegram.sent_messages, [])

    def _build_feature(
        self,
        *,
        edit_result: str,
        states: dict[str, dict[str, str]] | None = None,
        telegram_chat_id: str = "",
        stored_chat_id: str = "",
    ) -> tuple[StatusMessageFeature, DummyTelegram, DummyState]:
        telegram = DummyTelegram(edit_result=edit_result)
        state = DummyState()
        state.set(self.prefix, "status_message_id", 33)
        if stored_chat_id:
            state.set(self.prefix, "status_message_chat_id", stored_chat_id)
        config = SimpleNamespace(
            entity_prefix=self.prefix,
            display_name="Одеса • вулиця Прикладна, 12А",
            telegram_chat_id=telegram_chat_id,
            status_message=SimpleNamespace(
                enabled=True,
                min_update_interval=0,
                update_interval=300,
                delivery_mode="pinned_edit",
                pin=True,
                silent=False,
            ),
        )
        feature = StatusMessageFeature(
            address_config=config,
            ha_client=DummyHA(states=states),
            telegram=telegram,
            state_store=state,
            templates=DummyTemplates(),
        )
        feature.set_power_monitor(DummyPowerMonitor())
        return feature, telegram, state

    async def test_network_edit_error_does_not_create_new_status_message(self) -> None:
        feature, telegram, state = self._build_feature(edit_result="error")

        await feature.on_start()
        await asyncio.sleep(0)

        self.assertEqual(telegram.edit_calls, 1)
        self.assertEqual(telegram.sent_messages, [])
        self.assertEqual(state.get(self.prefix, "status_message_id"), 33)

    async def test_missing_message_recreates_status_message(self) -> None:
        feature, telegram, state = self._build_feature(edit_result="not_found")

        await feature.on_start()

        self.assertEqual(telegram.edit_calls, 1)
        self.assertEqual(len(telegram.sent_messages), 1)
        self.assertEqual(state.get(self.prefix, "status_message_id"), 44)

    async def test_chat_id_change_recreates_status_message_without_edit_attempt(self) -> None:
        feature, telegram, state = self._build_feature(
            edit_result="error",
            telegram_chat_id="-100999888777",
            stored_chat_id="-1001234567890",
        )

        await feature.on_start()

        self.assertEqual(telegram.edit_calls, 0)
        self.assertEqual(len(telegram.sent_messages), 1)
        self.assertEqual(telegram.sent_kwargs[0]["chat_id"], "-100999888777")
        self.assertEqual(state.get(self.prefix, "status_message_id"), 44)
        self.assertEqual(
            state.get(self.prefix, "status_message_chat_id"),
            "-100999888777",
        )

    async def test_status_message_uses_primary_schedule_group_when_state_is_missing(self) -> None:
        feature, telegram, state = self._build_feature(
            edit_result="not_found",
            states={
                f"sensor.{self.prefix}_primary_schedule_group": {"state": "6"},
            },
        )

        await feature.on_start()

        self.assertEqual(state.get(self.prefix, "current_group"), "6")
        self.assertEqual(len(telegram.sent_messages), 1)
        self.assertIn("group=6", telegram.sent_messages[0])


if __name__ == "__main__":
    unittest.main()

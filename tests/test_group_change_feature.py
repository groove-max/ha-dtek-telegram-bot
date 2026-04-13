from __future__ import annotations

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
    from features.group_change import GroupChangeFeature


class DummyHA:
    def __init__(self, states: dict[str, dict[str, str]] | None = None) -> None:
        self.states = states or {}

    async def get_state(self, entity_id: str) -> dict[str, str] | None:
        return self.states.get(entity_id)

    async def call_service(self, *args: object, **kwargs: object) -> dict[str, object]:
        return {}


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
            f"{template_name}|{context.get('old_group')}->{context.get('new_group')}"
        )


class DummyStatusMessage:
    def __init__(self) -> None:
        self.request_count = 0

    async def request_update(self) -> None:
        self.request_count += 1


@unittest.skipUnless(HAS_RUNTIME_DEPS, "PyYAML and pydantic are not installed in the local test environment")
class GroupChangeFeatureTest(unittest.IsolatedAsyncioTestCase):
    prefix = "doroga_liustdorfska_56v"

    def _build_feature(
        self,
        *,
        states: dict[str, dict[str, str]] | None = None,
    ) -> tuple[GroupChangeFeature, DummyTelegram, DummyState, DummyStatusMessage]:
        telegram = DummyTelegram()
        state = DummyState()
        status_message = DummyStatusMessage()
        config = SimpleNamespace(
            entity_prefix=self.prefix,
            display_name="Одеса • дорога Люстдорфська, 56В",
            telegram_chat_id="",
            group_change=SimpleNamespace(enabled=True, silent=False),
        )
        feature = GroupChangeFeature(
            address_config=config,
            ha_client=DummyHA(states=states),
            telegram=telegram,
            state_store=state,
            templates=DummyTemplates(),
        )
        feature.set_status_message(status_message)
        return feature, telegram, state, status_message

    def test_watches_primary_and_legacy_schedule_group_entities(self) -> None:
        feature, _telegram, _state, _status = self._build_feature()

        self.assertEqual(
            feature.get_watched_entities(),
            [
                f"sensor.{self.prefix}_primary_schedule_group",
                f"sensor.{self.prefix}_schedule_group",
            ],
        )

    async def test_on_start_reads_primary_schedule_group_entity(self) -> None:
        feature, _telegram, state, _status = self._build_feature(
            states={
                f"sensor.{self.prefix}_primary_schedule_group": {"state": "4"},
            },
        )

        await feature.on_start()

        self.assertEqual(state.get(self.prefix, "current_group"), "4")

    async def test_first_valid_group_updates_status_without_notification(self) -> None:
        feature, telegram, state, status_message = self._build_feature()

        await feature.on_state_change(
            f"sensor.{self.prefix}_primary_schedule_group",
            {"state": "unknown"},
            {"state": "6"},
        )

        self.assertEqual(state.get(self.prefix, "current_group"), "6")
        self.assertEqual(telegram.messages, [])
        self.assertEqual(status_message.request_count, 1)

    async def test_group_change_notification_refreshes_status_message(self) -> None:
        feature, telegram, state, status_message = self._build_feature()
        state.set(self.prefix, "current_group", "5")

        await feature.on_state_change(
            f"sensor.{self.prefix}_primary_schedule_group",
            {"state": "5"},
            {"state": "6"},
        )

        self.assertEqual(state.get(self.prefix, "current_group"), "6")
        self.assertEqual(telegram.messages, ["group_change|5->6"])
        self.assertEqual(status_message.request_count, 1)


if __name__ == "__main__":
    unittest.main()
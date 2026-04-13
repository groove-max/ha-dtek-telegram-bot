from __future__ import annotations

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

        def get_source(
            self, environment: object, template: str
        ) -> tuple[str, str | None, object]:
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
    from features.schedule_change import ScheduleChangeFeature
    from utils import now_kyiv


class DummyHA:
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
        return template_name


@unittest.skipUnless(
    HAS_RUNTIME_DEPS, "PyYAML and pydantic are not installed in the local test environment"
)
class ScheduleChangeFeatureTest(unittest.IsolatedAsyncioTestCase):
    prefix = "doroga_liustdorfska_56v"

    def _build_feature(self) -> tuple[ScheduleChangeFeature, DummyTelegram, DummyState]:
        telegram = DummyTelegram()
        state = DummyState()
        config = SimpleNamespace(
            entity_prefix=self.prefix,
            display_name="Одеса • дорога Люстдорфська, 56В",
            telegram_chat_id="",
            schedule_change=SimpleNamespace(enabled=True, silent=False),
        )
        feature = ScheduleChangeFeature(
            address_config=config,
            ha_client=DummyHA(),
            telegram=telegram,
            state_store=state,
            templates=DummyTemplates(),
        )
        return feature, telegram, state

    async def test_schedule_empty_sent_only_for_recent_non_empty_schedule(self) -> None:
        feature, telegram, state = self._build_feature()
        state.set(self.prefix, "schedule_signature", "previous")
        state.set(self.prefix, "has_schedule", True)
        state.set(self.prefix, "last_schedule_seen_at", now_kyiv().isoformat())

        async def _fetch_outage_events() -> list[dict[str, object]]:
            return []

        feature._fetch_outage_events = _fetch_outage_events  # type: ignore[method-assign]

        await feature._check_and_notify()

        self.assertEqual(telegram.messages, ["schedule_empty"])
        self.assertFalse(state.get(self.prefix, "has_schedule"))

    async def test_schedule_empty_skipped_for_stale_non_empty_schedule(self) -> None:
        feature, telegram, state = self._build_feature()
        state.set(self.prefix, "schedule_signature", "previous")
        state.set(self.prefix, "has_schedule", True)
        state.set(
            self.prefix,
            "last_schedule_seen_at",
            (now_kyiv() - timedelta(days=2)).isoformat(),
        )

        async def _fetch_outage_events() -> list[dict[str, object]]:
            return []

        feature._fetch_outage_events = _fetch_outage_events  # type: ignore[method-assign]

        await feature._check_and_notify()

        self.assertEqual(telegram.messages, [])
        self.assertFalse(state.get(self.prefix, "has_schedule"))


if __name__ == "__main__":
    unittest.main()

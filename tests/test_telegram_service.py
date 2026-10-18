from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


APP_ROOT = Path(__file__).resolve().parents[1] / "rootfs" / "app"
sys.path.insert(0, str(APP_ROOT))

if "aiogram" not in sys.modules:
    aiogram = types.ModuleType("aiogram")
    aiogram.Bot = object
    aiogram_enums = types.ModuleType("aiogram.enums")
    aiogram_enums.ParseMode = object
    aiogram_exceptions = types.ModuleType("aiogram.exceptions")

    class _DummyTelegramBadRequest(Exception):
        pass

    class _DummyTelegramRetryAfter(Exception):
        pass

    aiogram_exceptions.TelegramBadRequest = _DummyTelegramBadRequest
    aiogram_exceptions.TelegramRetryAfter = _DummyTelegramRetryAfter
    sys.modules["aiogram"] = aiogram
    sys.modules["aiogram.enums"] = aiogram_enums
    sys.modules["aiogram.exceptions"] = aiogram_exceptions

import telegram_service as telegram_service_module


class TelegramServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_message_cant_be_edited_is_treated_as_not_found(self) -> None:
        class FakeTelegramBadRequest(Exception):
            pass

        with patch.object(telegram_service_module, "Bot") as bot_cls:
            bot = AsyncMock()
            bot.session.close = AsyncMock()
            bot_cls.return_value = bot
            service = telegram_service_module.TelegramService(
                "123456:TEST_TOKEN_VALUE",
                "-1001234567890",
            )

        with patch.object(
            telegram_service_module,
            "TelegramBadRequest",
            FakeTelegramBadRequest,
        ):
            with patch.object(
                service,
                "_call_with_retry",
                side_effect=FakeTelegramBadRequest("message can't be edited"),
            ):
                result = await service.edit_message_result(33, "updated text")

        self.assertEqual(result, "not_found")


if __name__ == "__main__":
    unittest.main()
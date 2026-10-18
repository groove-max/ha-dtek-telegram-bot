"""Destination-independent delivery using real Telegram types and mocked I/O."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import aiogram
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import PinChatMessage, SendMessage
from aiogram.types import Chat, Message

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rootfs/app"))
from config import AddressConfig, FullConfig
from delivery_journal import DeliveryJournal
from telegram_service import TelegramService

DESTINATIONS = [
    ("123456789", "private"),
    ("-123456789", "group"),
    ("-1001234567890", "supergroup"),
    ("-1009876543210", "channel"),
]


class TelegramDestinationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def service(self, target, chat_type, *, test_target=""):
        bot = AsyncMock()
        message = Message(
            message_id=71,
            date=datetime.now(timezone.utc),
            chat=Chat(id=int(test_target or target), type=chat_type),
            text="status",
        )
        bot.send_message.return_value = message
        bot.edit_message_text.return_value = message
        bot.pin_chat_message.return_value = True
        bot.unpin_chat_message.return_value = True
        with patch("telegram_service.Bot", return_value=bot):
            service = TelegramService("123:test", target, test_chat_id=test_target)
        service.journal = DeliveryJournal(
            Path(self.directory.name) / f"{target}-{test_target}.sqlite"
        )
        self.addAsyncCleanup(service.close)
        return service, bot

    async def test_send_edit_pin_unpin_support_all_chat_types(self):
        for target, chat_type in DESTINATIONS:
            with self.subTest(chat_type=chat_type):
                cfg = FullConfig(telegram_chat_id=target)
                self.assertEqual(cfg.telegram_chat_id, target)
                service, bot = self.service(target, chat_type)
                self.assertEqual(await service.send_message("status", pin=True), 71)
                self.assertEqual(await service.edit_message_result(71, "updated"), "ok")
                self.assertTrue(await service.unpin_message(71))
                for call in [
                    bot.send_message,
                    bot.edit_message_text,
                    bot.pin_chat_message,
                    bot.unpin_chat_message,
                ]:
                    self.assertEqual(call.await_args.kwargs["chat_id"], target)
                self.assertTrue(service.journal.owns(target, 71))

    async def test_per_address_private_destination_overrides_main_channel(self):
        address = AddressConfig(entity_prefix="demo", telegram_chat_id="123456789")
        service, bot = self.service("-1009876543210", "private")
        await service.send_message("alert", chat_id=address.telegram_chat_id)
        self.assertEqual(bot.send_message.await_args.kwargs["chat_id"], "123456789")

    async def test_personal_test_destination_overrides_all_operations(self):
        service, bot = self.service(
            "-1009876543210", "private", test_target="123456789"
        )
        await service.send_message("test", chat_id="-123456789", pin=True)
        await service.edit_message_result(71, "test update", chat_id="-1001234567890")
        await service.unpin_message(71, chat_id="-1001234567890")
        for call in [
            bot.send_message,
            bot.edit_message_text,
            bot.pin_chat_message,
            bot.unpin_chat_message,
        ]:
            self.assertEqual(call.await_args.kwargs["chat_id"], "123456789")
        self.assertFalse(await service.pin_message(999))

    async def test_denied_pin_does_not_duplicate_successfully_sent_status(self):
        service, bot = self.service("-123456789", "group")
        bot.pin_chat_message.side_effect = TelegramBadRequest(
            method=PinChatMessage(chat_id="-123456789", message_id=71),
            message="not enough rights to pin a message",
        )
        for _ in range(2):
            self.assertEqual(
                await service.send_message("status", pin=True, delivery_key="status-1"),
                71,
            )
        self.assertEqual(bot.send_message.await_count, 1)
        self.assertEqual(await service.edit_message_result(71, "updated"), "ok")

    async def test_unpinned_private_status_needs_no_pin_operation(self):
        service, bot = self.service("123456789", "private")
        self.assertEqual(await service.send_message("status", pin=False), 71)
        bot.pin_chat_message.assert_not_awaited()
        self.assertEqual(await service.edit_message_result(71, "updated"), "ok")

    async def test_blocked_private_bot_does_not_record_success(self):
        service, bot = self.service("123456789", "private")
        bot.send_message.side_effect = TelegramForbiddenError(
            method=SendMessage(chat_id="123456789", text="alert"),
            message="bot was blocked by the user",
        )
        self.assertIsNone(await service.send_message("alert"))
        self.assertFalse(service.journal.owns("123456789", 71))

    async def test_feedback_accepts_only_selected_chat_for_each_type(self):
        for target, chat_type in DESTINATIONS:
            with self.subTest(chat_type=chat_type):
                service, _ = self.service(target, chat_type)
                kind = "channel_post" if chat_type == "channel" else "message"
                for update_id, sender in [(1, int(target)), (2, 999)]:
                    service.journal.ingest(
                        update_id,
                        {
                            kind: {
                                "message_id": update_id,
                                "chat": {"id": sender, "type": chat_type},
                                "text": "feedback",
                            }
                        },
                        target,
                    )
                rows = service.journal.db.execute(
                    "SELECT chat_id,action FROM events WHERE direction='in'"
                ).fetchall()
                self.assertEqual(rows, [(target, kind)])

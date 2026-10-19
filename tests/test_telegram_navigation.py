"""Message ownership, restart recovery and recipient discovery boundaries."""

import asyncio
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rootfs/app"))
from dialogue_screens import DialogueScreens
from telegram_directory import RecipientDirectory


class ScreensTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.bot = SimpleNamespace(delete_message=AsyncMock(), edit_message_reply_markup=AsyncMock())
        self.screens = DialogueScreens(self.db, self.bot)

    async def send(self, chat, mid):
        return await self.screens.replace(chat, AsyncMock(return_value=SimpleNamespace(message_id=mid)))

    async def test_only_previous_screen_is_deleted_after_success(self):
        await self.send(123, 10)
        await self.send(456, 20)
        # A pinned status / an alert never enters the screen ownership table.
        self.bot.delete_message.assert_not_awaited()
        failed = AsyncMock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            await self.screens.replace(123, failed)
        self.bot.delete_message.assert_not_awaited()
        await self.send(123, 11)
        self.bot.delete_message.assert_awaited_once_with(chat_id="123", message_id=10)
        self.assertEqual(self.db.execute("SELECT message_id FROM dialogue_screens WHERE chat_id='456'").fetchone()[0], 20)

    async def test_restart_preserves_cleanup_and_transient_failure_is_retried(self):
        await self.send(123, 10)
        self.bot.delete_message.side_effect = TimeoutError()
        await self.send(123, 11)
        self.screens = DialogueScreens(self.db, self.bot)
        self.bot.delete_message.side_effect = None
        await self.send(123, 12)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM obsolete_screens").fetchone()[0], 0)
        self.assertEqual({c.kwargs["message_id"] for c in self.bot.delete_message.call_args_list}, {10, 11})

    async def test_old_screen_controls_are_removed_and_shared_chats_are_rejected(self):
        with patch("dialogue_screens.time.time", return_value=time.time() - 49 * 3600):
            await self.send(123, 10)
        await self.send(123, 11)
        self.bot.delete_message.assert_not_awaited()
        self.bot.edit_message_reply_markup.assert_awaited_once_with(chat_id="123", message_id=10, reply_markup=None)
        operation = AsyncMock()
        with self.assertRaises(ValueError):
            await self.screens.replace(-10001, operation)
        operation.assert_not_awaited()

    async def test_concurrent_navigation_keeps_one_latest_screen(self):
        await asyncio.gather(self.send(123, 10), self.send(123, 11), self.send(123, 12))
        self.assertEqual(self.db.execute("SELECT message_id FROM dialogue_screens").fetchone()[0], 12)
        self.assertEqual([c.kwargs["message_id"] for c in self.bot.delete_message.call_args_list], [10, 11])


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.directory = RecipientDirectory(self.db)

    def message(self, chat_id=123, kind="private", date=100):
        return {"message": {"chat": {"id": chat_id, "type": kind, "first_name": "Example", "title": "Home", "username": "example"},
                            "from": {"id": 123, "is_bot": False}, "date": date, "text": "secret reading", "phone_number": "private"}}

    def membership(self, status, bot_id=99):
        return {"my_chat_member": {"date": 200, "chat": {"id": -1001, "type": "channel", "title": "House"},
                "from": {"id": 987, "first_name": "Admin"}, "new_chat_member": {"user": {"id": bot_id}, "status": status}}}

    def test_learns_actual_chat_not_group_senders_and_keeps_no_message_content(self):
        self.directory.observe(self.message(), 99)
        self.directory.observe(self.message(-10, "group"), 99)
        self.directory.observe(self.membership("administrator"), 99)
        peers = self.directory.list()
        self.assertEqual({p["id"] for p in peers}, {"123", "-10", "-1001"})
        self.assertNotIn("secret reading", json.dumps(peers))
        self.assertNotIn("phone_number", json.dumps(peers))
        self.assertNotIn("987", json.dumps(peers))

    def test_removed_chat_stays_unavailable_despite_older_edited_post(self):
        self.directory.observe(self.membership("kicked"), 99)
        self.directory.observe(self.message(-1001, "channel", date=150), 99)
        self.assertFalse(self.directory.list()[0]["available"])
        self.directory.observe(self.message(-1001, "channel", date=250), 99)
        self.assertTrue(self.directory.list()[0]["available"])

    def test_other_bot_membership_and_private_sender_mismatch_are_ignored(self):
        self.directory.observe(self.membership("administrator", bot_id=98), 99)
        self.directory.observe(self.message(456), 99)
        self.assertEqual(self.directory.list(), [])

    def test_restart_persistence_bot_isolation_and_bounded_size(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bot.sqlite"
            with sqlite3.connect(path) as db:
                RecipientDirectory(db).observe(self.message(), 99)
            with sqlite3.connect(path) as db:
                self.assertEqual(RecipientDirectory(db).list()[0]["id"], "123")
        self.assertEqual(self.directory.list(), [])
        for i in range(520):
            self.directory.observe(self.message(-i-1, "supergroup"), 99)
        self.assertEqual(len(self.directory.list()), 500)

    def test_api_returns_only_matching_bot_directory_without_granting_access(self):
        from config import FullConfig
        from main import Orchestrator

        self.directory.observe(self.message(), 99)
        app = Orchestrator.__new__(Orchestrator)
        app.config = FullConfig()
        app.bot_profiles = SimpleNamespace(
            snapshot=Mock(return_value={"profiles": [{"id": "default", "bot_id": "99"}, {"id": "second", "bot_id": "88"}]}),
            read=lambda: {},
        )
        app.bots = SimpleNamespace(
            profiles={}, public=lambda: [],
            clients={
                "default": SimpleNamespace(_bot=SimpleNamespace(id=99), journal=SimpleNamespace(recipients=self.directory)),
                "second": SimpleNamespace(_bot=SimpleNamespace(id=77), journal=SimpleNamespace(recipients=self.directory)),
            },
        )
        result = app.get_bot_profiles()
        self.assertEqual(result["recipients"]["default"][0]["id"], "123")
        self.assertNotIn("second", result["recipients"])
        self.assertEqual(app.config.addresses, [])


if __name__ == "__main__":
    unittest.main()

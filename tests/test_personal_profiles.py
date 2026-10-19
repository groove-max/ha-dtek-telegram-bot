"""Private conversations, independent routes, cabinet-only groups and image export."""

import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import itertools
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from aiogram.types import Update

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rootfs/app"))
from config import AddressConfig, FullConfig
from conversations import Conversations
from delivery_journal import DeliveryJournal
from bot_profiles import BotProfileStore, validate_destinations
from bot_runtime import BotRuntime
from ha_client import HACommandError
from personal_source import PersonalSource
from state_store import StateStore
from template_engine import TemplateEngine
from schedule_image import render_grid
from PIL import Image


def personal(**changes):
    return AddressConfig(
        **{
            "mode": "personal",
            "account_entity": "sensor.demo_account",
            "telegram_user_id": 123,
            "display_name": "Приклад, квартира 1",
            **changes,
        }
    )


class ProfileConfigTests(unittest.TestCase):
    def test_legacy_identity_and_two_profiles_for_one_house(self):
        legacy = AddressConfig(entity_prefix="house")
        self.assertEqual(legacy.key, "house")
        FullConfig(
            addresses=[
                legacy,
                AddressConfig(entity_prefix="house", profile_id="private-view"),
            ]
        )
        with self.assertRaises(ValueError):
            FullConfig(addresses=[legacy, legacy])

    def test_private_recipient_and_no_manual_group_fallback(self):
        self.assertEqual(personal().telegram_chat_id, "123")
        for changes in (
            {"telegram_user_id": 0},
            {"telegram_chat_id": "-100123"},
            {"selected_group": "GPV5.1"},
            {"account_entity": ""},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                personal(**changes)

    def test_private_test_route_never_falls_back_to_shared_channel(self):
        with TemporaryDirectory() as directory:
            config = FullConfig(
                telegram_bot_token="123456:TEST_TOKEN",
                test_chat_id="-10001",
                addresses=[personal()],
            )
            store = BotProfileStore(Path(directory) / "profiles.json")
            with self.assertRaises(ValueError):
                validate_destinations(config, store)
            runtime = BotRuntime(config, store, Path(directory))
            self.assertIsNone(runtime.service(config.addresses[0]))
            self.assertEqual(runtime.chat(config.addresses[0]), "")


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = DeliveryJournal(Path(self.tmp.name) / "journal.sqlite")
        self.addCleanup(self.journal.close)
        self.profile = personal(allow_readings=True)
        self.app = SimpleNamespace(
            config=FullConfig(addresses=[self.profile]),
            ha=SimpleNamespace(command=AsyncMock()),
            state=StateStore(Path(self.tmp.name) / "state.json"),
            templates=TemplateEngine(Path(self.tmp.name) / "templates"),
        )
        self.app.bots = SimpleNamespace(chat=lambda a: a.telegram_chat_id)
        self.bot = SimpleNamespace(
            id=123456,
            send_message=AsyncMock(side_effect=lambda **kwargs: SimpleNamespace(message_id=next(message_ids))),
            answer_callback_query=AsyncMock(),
            send_document=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        message_ids = itertools.count(10)
        self.tg = SimpleNamespace(_bot=self.bot, _test_chat_id="", journal=self.journal)
        from dialogue_screens import DialogueScreens
        screens = DialogueScreens(self.journal.db, self.bot)
        self.tg.replace_menu = screens.replace
        self.router = Conversations(self.app, "default", self.tg)

    def message(self, user=777, text="/start", kind="private", **changes):
        return {
            "message": {
                "message_id": 1,
                "from": {"id": user, "is_bot": False},
                "chat": {"id": user, "type": kind},
                "text": text,
                "date": time.time(),
                **changes,
            }
        }

    def callback(self, token, user=123):
        return {
            "callback_query": {
                "id": "callback",
                "from": {"id": user, "is_bot": False},
                "data": token,
                "message": {"message_id": 1, "chat": {"id": user, "type": "private"}},
            }
        }

    def telegram_update(self, payload):
        """Use the same validated aiogram models that getUpdates returns."""
        payload = {"update_id": 100, **payload}
        event = payload.get("callback_query") or payload["message"]
        event["from"]["first_name"] = "Example"
        if "callback_query" in payload:
            event["chat_instance"] = "private-chat"
            event["message"]["date"] = int(time.time())
        return Update.model_validate(payload)

    async def test_real_telegram_start_opens_menu_and_callback_dispatches(self):
        await self.router.handle(self.telegram_update(self.message(user=123)))
        self.bot.send_message.assert_awaited_once()
        sent = self.bot.send_message.call_args.kwargs
        self.assertEqual(sent["chat_id"], 123)
        first = sent["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(first.text, "📅 Графік на сьогодні")
        self.assertNotIn("Стан", str(sent["reply_markup"]))
        self.router.action = AsyncMock()
        await self.router.handle(self.telegram_update(self.callback(first.callback_data)))
        self.bot.answer_callback_query.assert_awaited_once_with("callback")
        self.router.action.assert_awaited_once_with(123, self.profile, "grid", 0)

    async def test_multiple_accounts_use_one_selector_and_reminder_does_not_replace_it(self):
        second = personal(account_entity="sensor.other_account", display_name="Other home")
        self.app.config.addresses.append(second)
        await self.router.menu(123, self.router.profiles(123))
        self.bot.send_message.assert_awaited_once()
        menu = self.bot.send_message.call_args.kwargs["reply_markup"]
        self.assertEqual(len(menu.inline_keyboard), 2)
        self.profile.reading_reminders = True
        self.app.ha.command.return_value = {"period": "2026-10", "due": True}
        await self.router.remind(self.profile, 123)
        self.bot.delete_message.assert_not_awaited()
        self.assertEqual(self.journal.db.execute("SELECT message_id FROM dialogue_screens").fetchone()[0], 10)
        await self.router.handle(self.callback(menu.inline_keyboard[1][0].callback_data))
        self.bot.delete_message.assert_awaited_once_with(chat_id="123", message_id=10)
        self.assertIn("Other home", self.bot.send_message.call_args.kwargs["text"])

    async def test_status_uses_shared_renderer_and_navigation_can_be_reused(self):
        self.app.render_status = AsyncMock(return_value="🟢 Дім <A & B>\n🔌 Напруга: 230 В")
        keyboard = self.router.keyboard(123, self.profile, [("Стан", "status", None)])
        token = keyboard.inline_keyboard[0][0].callback_data
        for _ in range(2):
            await self.router.handle(self.telegram_update(self.callback(token)))
        self.assertEqual(self.app.render_status.await_count, 2)
        sent = self.bot.send_message.call_args.kwargs
        self.assertEqual(sent["chat_id"], 123)
        self.assertIn("&lt;A &amp; B&gt;", sent["text"])
        self.assertEqual(sent["reply_markup"].inline_keyboard[0][0].text, "🏠 Меню")
        self.app.ha.command.assert_not_awaited()

    async def test_status_button_covers_automatic_and_manual_modes_for_each_profile_type(self):
        for mode, enabled, pin, delivery, interval in itertools.product(
            ("personal", "building"), (False, True), (False, True),
            ("pinned_edit", "send_new"), (0, 300),
        ):
            with self.subTest(mode=mode, enabled=enabled, pin=pin,
                              delivery=delivery, interval=interval):
                status = dict(enabled=enabled, pin=pin, delivery_mode=delivery,
                              update_interval=interval)
                profile = (
                    personal(status_message=status) if mode == "personal" else
                    AddressConfig(entity_prefix="house", telegram_chat_id="123",
                                  status_message=status)
                )
                self.app.config.addresses = [profile]
                await self.router.menu(123, self.router.profiles(123))
                rows = self.bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard
                buttons = [button.text for row in rows for button in row]
                expected = not (enabled and pin and delivery == "pinned_edit" and interval > 0)
                self.assertEqual("⚡️ Стан" in buttons, expected)

    async def test_disabled_automatic_status_can_be_requested_and_menu_reflects_new_settings(self):
        self.profile.status_message.enabled = False
        self.app.render_status = AsyncMock(side_effect=["🔌 221 В", "🔌 229 В"])
        await self.router.handle(self.telegram_update(self.message(user=123)))
        rows = self.bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard
        button = next(button for row in rows for button in row if button.text == "⚡️ Стан")
        for voltage in ("221 В", "229 В"):
            await self.router.handle(self.telegram_update(self.callback(button.callback_data)))
            sent = self.bot.send_message.call_args.kwargs
            self.assertIn(voltage, sent["text"])
            self.assertEqual(sent["reply_markup"].inline_keyboard[0][0].text, "🏠 Меню")
        self.assertEqual(self.app.render_status.await_count, 2)
        self.app.ha.command.assert_not_awaited()
        self.assertIsNone(self.app.state.get(self.profile.key, "status_message_id"))
        menu_token = sent["reply_markup"].inline_keyboard[0][0].callback_data
        self.profile.status_message.enabled = True
        await self.router.handle(self.telegram_update(self.callback(menu_token)))
        self.assertNotIn("⚡️ Стан", str(self.bot.send_message.call_args.kwargs["reply_markup"]))
        self.profile.status_message.update_interval = 0
        await self.router.handle(self.telegram_update(self.callback(menu_token)))
        self.assertIn("⚡️ Стан", str(self.bot.send_message.call_args.kwargs["reply_markup"]))

    async def test_status_menu_follows_selected_account_not_another_accounts_settings(self):
        second = personal(account_entity="sensor.other_account", status_message={"enabled": False})
        self.app.config.addresses.append(second)
        await self.router.handle(self.telegram_update(self.message(user=123)))
        selector = self.bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard
        for index, expected in ((1, True), (0, False)):
            await self.router.handle(self.telegram_update(self.callback(selector[index][0].callback_data)))
            keyboard = self.bot.send_message.call_args.kwargs["reply_markup"]
            self.assertEqual("⚡️ Стан" in str(keyboard), expected)

    async def test_return_to_menu_invalidates_reading_confirmation(self):
        token = self.router.keyboard(123, self.profile, [("Send", "confirm", "draft")]).inline_keyboard[0][0].callback_data
        menu = self.router.navigation(123, self.profile).inline_keyboard[0][0].callback_data
        self.router.sessions[123] = {"profile": self.profile.key, "expires": time.time() + 300}
        await self.router.handle(self.telegram_update(self.callback(menu)))
        self.assertNotIn(123, self.router.sessions)
        await self.router.handle(self.telegram_update(self.callback(token)))
        self.app.ha.command.assert_not_awaited()

    async def test_real_telegram_stranger_only_gets_introduction(self):
        await self.router.handle(self.telegram_update(self.message(user=777)))
        self.assertIn("GitHub", self.bot.send_message.call_args.kwargs["text"])
        self.app.ha.command.assert_not_awaited()

    async def test_unknown_gets_links_once_and_no_private_data(self):
        await self.router.handle(self.message())
        await self.router.handle(self.message(text="hello again"))
        self.bot.send_message.assert_awaited_once()
        args = self.bot.send_message.call_args.kwargs
        self.assertEqual(args["chat_id"], 777)
        self.assertIn("GitHub", args["text"])
        self.assertNotIn("квартира 1", args["text"])
        self.assertNotIn("sensor.demo_account", str(args))
        self.app.ha.command.assert_not_awaited()
        restarted = Conversations(self.app, "default", self.tg)
        await restarted.handle(self.message())
        self.bot.send_message.assert_awaited_once()

    async def test_no_greeting_for_groups_bots_old_edits_or_disabled(self):
        await self.router.handle(self.message(kind="supergroup"))
        await self.router.handle({"edited_message": self.message()["message"]})
        await self.router.handle(self.message(date=time.time() - 600))
        await self.router.handle(self.message(**{"from": {"id": 777, "is_bot": True}}))
        self.app.config.welcome_enabled = False
        await self.router.handle(self.message())
        self.bot.send_message.assert_not_awaited()

    async def test_welcome_override_applies_immediately_and_survives_restart(self):
        self.app.templates.save_override("welcome", "Вітаємо! Ваш ID: {{ telegram_user_id }}")
        await self.router.handle(self.message())
        sent = self.bot.send_message.call_args.kwargs
        self.assertEqual(sent["text"], "Вітаємо! Ваш ID: 777")
        self.assertIsNone(sent["reply_markup"])
        self.app.templates = TemplateEngine(Path(self.tmp.name) / "templates")
        await Conversations(self.app, "default", self.tg).handle(self.message(user=778))
        self.assertEqual(self.bot.send_message.call_args.kwargs["text"], "Вітаємо! Ваш ID: 778")
        self.app.templates.delete_override("welcome")
        await self.router.handle(self.message(user=779))
        text = self.bot.send_message.call_args.kwargs["text"]
        self.assertIn("https://github.com/groove-max/ha-dtek-monitor", text)
        self.assertIn("https://github.com/groove-max/ha-dtek-telegram-bot", text)
        self.app.ha.command.assert_not_awaited()

    async def test_welcome_preview_has_only_public_context(self):
        from main import Orchestrator
        app = Orchestrator.__new__(Orchestrator)
        result = await app._build_template_context(self.profile, "welcome")
        self.assertEqual(result, {"telegram_user_id": 123456789})

    async def test_history_and_old_readback_button_show_dtek_without_local_attempt(self):
        self.app.ha.command.return_value = {
            "status": "no_attempt", "attempt": None, "fetched_at": "2026-10-19T10:00:00+03:00",
            "latest": [{"date_time": "2026-10-18T00:00:00+03:00", "value": "1025",
                        "zone": "GENERAL", "energy": "ACTIVE", "status": "active"}],
        }
        for action in ("result", "history", "readback"):
            await self.router.action(123, self.profile, action, None)
            sent = self.bot.send_message.call_args.kwargs
            self.assertIn("1025 кВт·год", sent["text"])
            self.assertIn("18.10.2026", sent["text"])
            self.assertNotIn("немає запису передачі", sent["text"])
            self.assertNotIn("придатного запису", sent["text"])
            self.assertNotIn("Перевірити останню передачу", str(sent["reply_markup"]))
        self.assertTrue(all(c.kwargs["action"] in ("history", "readback") for c in self.app.ha.command.call_args_list))

    async def test_unavailable_history_is_not_reported_as_empty(self):
        self.app.ha.command.side_effect = HACommandError("unavailable")
        button = self.router.keyboard(123, self.profile, [("History", "history", None)]).inline_keyboard[0][0]
        await self.router.handle(self.callback(button.callback_data))
        text = self.bot.send_message.call_args.kwargs["text"]
        self.assertIn("Історія показів недоступна", text)
        self.assertNotIn("не повернув записів", text)

    async def test_history_toggle_pagination_and_refresh_keep_filter_without_submission(self):
        def record(status, value="120"):
            return {"status": status, "value": value, "energy": "ACTIVE", "zone": "GENERAL", "date_time": "2026-09-30T23:59:00+03:00"}
        async def response(kind, **kw):
            mode = kw.get("include_inactive", False);offset = kw.get("history_offset", 0)
            return {"latest": [record("inactive")] if mode else [record("active"), record("blocked"), record("unknown")],
                    "years": [2026], "history_view": {"include_inactive": mode, "inactive_count": 8,
                    "total": 11, "visible": 11 if mode else 3, "offset": offset,
                    "previous_offset": 0 if offset else None, "next_offset": 6 if mode and offset==0 else None}}
        self.app.ha.command.side_effect = response
        await self.router.action(123, self.profile, "history", None)
        text = self.bot.send_message.call_args.kwargs["text"]
        self.assertNotIn("ℹ️ неактивний запис", text)
        self.assertIn("⚠️ заблокований запис", text)
        self.assertIn("⚠️ статус запису не вказаний", text)
        self.assertIn("Приховано неактивних записів: 8", text)
        async def click(label):
            markup = self.bot.send_message.call_args.kwargs["reply_markup"]
            button = next(b for row in markup.inline_keyboard for b in row if b.text == label)
            await self.router.handle(self.callback(button.callback_data))
        await click("🗂 Показати всі записи ДТЕК")
        self.assertTrue(self.app.ha.command.call_args.kwargs["include_inactive"])
        self.assertIn("ℹ️ неактивний запис", self.bot.send_message.call_args.kwargs["text"])
        await click("➡️ Старіші записи")
        self.assertEqual(self.app.ha.command.call_args.kwargs["history_offset"], 6)
        await click("🔄 Оновити з ДТЕК")
        self.assertEqual(self.app.ha.command.call_args.kwargs["history_offset"], 6)
        self.assertTrue(self.app.ha.command.call_args.kwargs["include_inactive"])
        await click("📋 Приховати неактивні записи")
        self.assertFalse(self.app.ha.command.call_args.kwargs["include_inactive"])
        self.assertEqual(self.app.ha.command.call_args.kwargs["history_offset"], 0)
        self.assertTrue(all(c.kwargs["action"] == "history" for c in self.app.ha.command.call_args_list))

    async def test_only_inactive_history_is_not_called_empty_and_readback_keeps_controls(self):
        result = {"status": "no_attempt", "latest": [], "history_view": {"include_inactive": False,
                  "inactive_count": 3, "total": 3, "visible": 0, "offset": 0, "previous_offset": None, "next_offset": None}}
        self.app.ha.command.return_value = result
        await self.router.action(123, self.profile, "readback", None)
        sent = self.bot.send_message.call_args.kwargs
        self.assertIn("Є лише неактивні записи", sent["text"])
        self.assertNotIn("ДТЕК не повернув", sent["text"])
        self.assertIn("Показати всі записи ДТЕК", str(sent["reply_markup"]))

    async def test_failed_archive_page_retry_retains_filter(self):
        self.app.ha.command.side_effect = HACommandError("unavailable")
        payload = {"include_inactive": True, "history_offset": 6}
        token = self.router.keyboard(123, self.profile, [("Page", "history", payload)]).inline_keyboard[0][0].callback_data
        await self.router.handle(self.callback(token))
        retry = self.bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        await self.router.handle(self.callback(retry))
        self.assertTrue(self.app.ha.command.call_args.kwargs["include_inactive"])
        self.assertEqual(self.app.ha.command.call_args.kwargs["history_offset"], 6)

    async def test_account_unavailable_distinguishes_history_and_preparation(self):
        self.app.ha.command.side_effect = HACommandError("not_available")
        for action in ("history", "readback", "readings"):
            with self.subTest(action=action):
                button = self.router.keyboard(123, self.profile, [(action, action, None)]).inline_keyboard[0][0]
                await self.router.handle(self.callback(button.callback_data))
                sent = self.bot.send_message.call_args.kwargs
                self.assertIn("Рахунок ДТЕК зараз недоступний", sent["text"])
                self.assertNotIn("Якщо ви підтвердили", sent["text"])
                self.assertNotIn("Результат передачі", sent["text"])
                retry = sent["reply_markup"].inline_keyboard[0][0].callback_data
                next_action = self.router.buttons[retry.removeprefix("dtek:")]["action"]
                self.assertEqual(next_action, action)
                if action == "readings":
                    self.assertIn("На цьому кроці покази не надсилаються", sent["text"])
                await self.router.handle(self.callback(retry))
        self.assertNotIn("confirm", [c.kwargs["action"] for c in self.app.ha.command.call_args_list])

    async def test_preparation_failure_after_manual_input_does_not_warn_of_submission(self):
        self.app.ha.command.side_effect = TimeoutError()
        self.router.sessions[123] = {
            "profile": self.profile.key, "input": True,
            "expires": time.time() + 300, "values": {},
            "context": {"revision": "rev", "zones": [{"zone": "GENERAL", "previous": "1000"}]},
        }
        await self.router.handle(self.message(user=123, text="1025"))
        self.assertEqual(self.app.ha.command.call_args.kwargs["action"], "prepare")
        text = self.bot.send_message.call_args.kwargs["text"]
        self.assertIn("Не вдалося підготувати передачу", text)
        self.assertIn("На цьому кроці покази не надсилаються", text)
        self.assertNotIn("Результат передачі", text)

    async def test_sensor_proposal_is_visible_and_requires_explicit_confirmation(self):
        context = {"account": "••••0001", "revision": "rev", "zones": [{"zone": "GENERAL", "previous": "1000"}]}
        energy = {"binding": {"mode": "offset"}, "ready": True, "proposal": "source-token",
                  "readings": {"GENERAL": {"send_value": "1025", "source": {"at": "2026-10-19T10:00:00+03:00"}}}}
        draft = {"confirmation": "confirm-token", "readings": [{"zone": "GENERAL", "previous": "1000", "value": "1025", "increase": "25"}]}
        self.app.ha.command.side_effect = [context, energy, draft]
        await self.router.action(123, self.profile, "readings", None)
        sent = self.bot.send_message.call_args.kwargs
        self.assertIn("1025 кВт·год", sent["text"])
        self.assertIn("коригування", sent["text"])
        self.assertNotIn("source-token", sent["text"])
        await self.router.handle(self.callback(sent["reply_markup"].inline_keyboard[0][0].callback_data))
        self.assertEqual(self.app.ha.command.call_args.kwargs["action"], "prepare")
        self.assertEqual(self.app.ha.command.call_args.kwargs["source_token"], "source-token")
        self.assertNotIn("confirm", [c.kwargs["action"] for c in self.app.ha.command.call_args_list])
        manual = self.bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard[1][0]
        await self.router.handle(self.callback(manual.callback_data))
        self.assertNotIn("source_token", self.router.sessions[123])

    async def test_missing_and_stale_sources_explain_why_manual_entry_is_needed(self):
        context = {"account": "••••0001", "zones": [{"zone": "GENERAL", "previous": "1000"}]}
        for energy, expected in [({"binding": None}, "не налаштовано"),
                                 ({"binding": {}, "readings": {"GENERAL": {"error": "source_stale"}}}, "застаріли"),
                                 ({"binding": {}, "readings": {"GENERAL": {"error": "source_reset"}}}, "скидання")]:
            self.app.ha.command.side_effect = [context, energy]
            await self.router.action(123, self.profile, "readings", None)
            sent = self.bot.send_message.call_args.kwargs
            self.assertIn(expected, sent["text"])
            self.assertNotIn("Використати розрахунок", str(sent["reply_markup"]))
            self.assertIn("Ввести вручну", str(sent["reply_markup"]))

    async def test_global_greeting_limit_and_authorized_menu(self):
        for user in range(1000, 1030):
            await self.router.handle(self.message(user=user))
        self.assertEqual(self.bot.send_message.await_count, 20)
        self.bot.send_message.reset_mock()
        await self.router.handle(self.message(user=123))
        self.assertNotIn("GitHub", self.bot.send_message.call_args.kwargs["text"])
        self.assertTrue(self.router.buttons)

    async def test_wrong_user_unknown_token_revoked_profile_never_calls_ha(self):
        keyboard = self.router.keyboard(
            123, self.profile, [("send", "confirm", "private-token")]
        )
        token = keyboard.inline_keyboard[0][0].callback_data
        await self.router.handle(self.callback(token, 777))
        self.app.config.addresses = []
        await self.router.handle(self.callback(token))
        self.app.ha.command.assert_not_awaited()

    async def test_confirm_is_one_attempt_and_identity_scoped(self):
        self.app.ha.command.return_value = {"status": "accepted"}
        token = (
            self.router.keyboard(123, self.profile, [("send", "confirm", "draft")])
            .inline_keyboard[0][0]
            .callback_data
        )
        await self.router.handle(self.callback(token))
        await self.router.handle(self.callback(token))
        self.app.ha.command.assert_awaited_once()
        data = self.app.ha.command.call_args.kwargs
        self.assertEqual(data["telegram_user_id"], 123)
        self.assertEqual(data["telegram_bot_id"], "123456")
        self.assertEqual(data["profile_id"], self.profile.key)
        self.assertEqual(data["confirmation"], "draft")

    async def test_timeout_never_replays_and_does_not_claim_success(self):
        self.app.ha.command.side_effect = TimeoutError()
        token = (
            self.router.keyboard(123, self.profile, [("send", "confirm", "draft")])
            .inline_keyboard[0][0]
            .callback_data
        )
        await self.router.handle(self.callback(token))
        self.assertNotIn("✅", self.bot.send_message.call_args.kwargs["text"])
        sent = self.bot.send_message.call_args.kwargs
        self.assertIn("Результат передачі не підтверджено", sent["text"])
        self.assertNotIn("На цьому кроці покази не надсилаються", sent["text"])
        self.assertTrue(all(b["action"] != "confirm" for b in self.router.buttons.values()))
        await self.router.handle(self.callback(token))
        self.app.ha.command.assert_awaited_once()

    async def test_permission_removed_after_preview_blocks_confirmation(self):
        token = (
            self.router.keyboard(123, self.profile, [("send", "confirm", "draft")])
            .inline_keyboard[0][0]
            .callback_data
        )
        self.profile.allow_readings = False
        await self.router.handle(self.callback(token))
        self.app.ha.command.assert_not_awaited()

    async def test_manual_day_night_inputs_prepare_only_then_explicit_confirm(self):
        context = {
            "account": "••••0001",
            "address": "Приклад",
            "revision": "rev",
            "zones": [
                {"zone": "DAY", "previous": "100"},
                {"zone": "NIGHT", "previous": "50"},
            ],
        }
        self.app.ha.command.return_value = {
            "confirmation": "draft",
            "readings": [
                {"zone": "DAY", "previous": "100", "value": 110, "increase": "10"},
                {"zone": "NIGHT", "previous": "50", "value": 55, "increase": "5"},
            ],
        }
        await self.router.action(
            123,
            self.profile,
            "manual",
            {"profile": self.profile.key, "context": context},
        )
        session = self.router.sessions[123]
        await self.router.input_reading(123, self.profile, "110", session)
        self.app.ha.command.assert_not_awaited()
        await self.router.input_reading(123, self.profile, "55", session)
        self.assertEqual(self.app.ha.command.call_args.kwargs["action"], "prepare")
        self.assertEqual(
            self.app.ha.command.call_args.kwargs["values"],
            {"DAY": "110", "NIGHT": "55"},
        )
        self.assertIn(
            "Різниця між показами", self.bot.send_message.call_args.kwargs["text"]
        )
        self.assertNotIn("draft", self.bot.send_message.call_args.kwargs["text"])

    async def test_test_chat_override_cannot_leak_reply(self):
        self.tg._test_chat_id = "-10001"
        await self.router.handle(self.message())
        await self.router.handle(self.message(user=123))
        self.bot.send_message.assert_not_awaited()

    async def test_reminder_dedup_and_dismiss_do_not_submit(self):
        self.profile.reading_reminders = True
        self.app.ha.command.return_value = {"period": "2026-10", "due": True}
        await self.router.on_tick()
        await self.router.on_tick()
        self.bot.send_message.assert_awaited_once()
        await self.router.action(123, self.profile, "dismiss_reminder", "2026-10")
        await self.router.on_tick()
        self.assertTrue(
            all(
                c.kwargs["action"] == "reminder"
                for c in self.app.ha.command.call_args_list
            )
        )

    async def test_other_authorized_user_cannot_consume_owner_button(self):
        self.app.config.addresses.append(personal(telegram_user_id=456))
        token = (
            self.router.keyboard(123, self.profile, [("send", "confirm", "draft")])
            .inline_keyboard[0][0]
            .callback_data
        )
        await self.router.handle(self.callback(token, 456))
        self.app.ha.command.assert_not_awaited()
        self.assertIn(token.removeprefix("dtek:"), self.router.buttons)
        self.app.ha.command.return_value = {"status": "accepted"}
        await self.router.handle(self.callback(token))
        self.app.ha.command.assert_awaited_once()

    async def test_cancel_or_edit_invalidates_previous_confirmation(self):
        for action in ("cancel", "manual", "readings"):
            with self.subTest(action=action):
                self.app.ha.command.reset_mock()
                token = (
                    self.router.keyboard(
                        123, self.profile, [("send", "confirm", "obsolete")]
                    )
                    .inline_keyboard[0][0]
                    .callback_data
                )
                if action == "cancel":
                    await self.router.handle(self.message(user=123, text="/cancel"))
                else:
                    context = {"zones": [{"zone": "GENERAL", "previous": "100"}]}
                    self.app.ha.command.return_value = {**context, "blocked": True}
                    await self.router.action(
                        123,
                        self.profile,
                        action,
                        {"profile": self.profile.key, "context": context},
                    )
                self.app.ha.command.reset_mock()
                await self.router.handle(self.callback(token))
                self.app.ha.command.assert_not_awaited()

    async def test_failed_reminder_backs_off_and_other_accounts_continue(self):
        self.profile.reading_reminders = True
        second = personal(telegram_user_id=456, reading_reminders=True)
        self.app.config.addresses.append(second)
        self.app.ha.command.return_value = {"period": "2026-10", "due": True}
        self.bot.send_message.side_effect = [
            TimeoutError(),
            SimpleNamespace(message_id=2),
        ]
        await self.router.on_tick()
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.assertIsNone(self.app.state.get(self.profile.key, "reading_reminder_sent"))
        self.assertEqual(
            self.app.state.get(second.key, "reading_reminder_sent"), "2026-10"
        )
        await self.router.on_tick()
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_result_readback_is_read_only(self):
        self.app.ha.command.return_value = {"status": "matched"}
        await self.router.action(123, self.profile, "readback", None)
        self.assertEqual(self.app.ha.command.call_args.kwargs["action"], "readback")
        self.assertIn(
            "не підтвердження", self.bot.send_message.call_args.kwargs["text"]
        )

    async def test_template_preview_cannot_send_personal_address_to_shared_test_chat(
        self,
    ):
        from main import Orchestrator

        app = Orchestrator.__new__(Orchestrator)
        app.preview_template = AsyncMock(
            return_value={"rendered": "Private account address"}
        )
        app._profile_for = Mock(return_value=SimpleNamespace(test_chat_id="-10001"))
        with patch("main.TelegramService") as factory:
            with self.assertRaisesRegex(ValueError, "приватному чаті"):
                await app.send_template_test(
                    self.app.config, template_name="status_on", address_index=0
                )
            factory.assert_not_called()


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.profile = personal()
        now = datetime.now(timezone.utc)
        self.snapshot = {
            "version": 1,
            "groups": ["GPV3.1"],
            "checked_at": now.isoformat(),
            "attributes": {
                "schedule_groups": ["GPV3.1"],
                "schedule_snapshot_id": "v1",
                "schedule_notifications_ready": True,
            },
            "entities": {},
            "calendars": {
                "outage": {
                    "available": True,
                    "events": [
                        {
                            "start": now.isoformat(),
                            "end": (now + timedelta(hours=1)).isoformat(),
                            "description": "Planned; group=GPV3.1",
                        }
                    ],
                }
            },
        }
        self.ha = SimpleNamespace(
            command=AsyncMock(return_value=self.snapshot), get_state=AsyncMock()
        )
        self.source = PersonalSource(self.ha, self.profile)

    async def test_cabinet_group_and_failure_cannot_fall_back_to_building(self):
        entity = f"sensor.{self.profile.entity_prefix}_primary_schedule_group"
        state = await self.source.get_state(entity)
        self.assertEqual(state["state"], "GPV3.1")
        self.ha.command.side_effect = HACommandError("unavailable")
        state = await self.source.get_state(entity)
        self.assertEqual(state["state"], "unavailable")
        self.assertIsNone(self.source.snapshot)
        self.ha.get_state.assert_not_awaited()

    async def test_unpublished_calendar_is_not_empty_success(self):
        self.snapshot["calendars"]["outage"]["available"] = False
        from outage_calendar import ScheduleUnavailable
        with self.assertRaises(ScheduleUnavailable):
            await self.source.call_service(
                "calendar",
                "get_events",
                f"calendar.{self.profile.entity_prefix}_outage_schedule",
                {},
            )

    async def test_independent_voltage_is_forwarded(self):
        await self.source.get_state("sensor.input_voltage")
        self.ha.get_state.assert_awaited_once_with("sensor.input_voltage")
        self.ha.command.assert_not_awaited()

    async def test_calendar_contract_is_consumed_by_existing_feature(self):
        from features.group_change import GroupChangeFeature
        from template_engine import TemplateEngine

        with TemporaryDirectory() as directory:
            state = StateStore(Path(directory) / "state.json")
            feature = GroupChangeFeature(
                self.profile,
                self.source,
                SimpleNamespace(send_message=AsyncMock(return_value=1)),
                state,
                TemplateEngine(),
            )
            events = await feature.calendar_snapshot(feature._fetch_outage_events)
            self.assertEqual(len(events), 1)
            self.assertIn("GPV3.1", events[0]["description"])
            self.ha.watch_entities = Mock()
            await self.source.refresh()
            self.ha.watch_entities.assert_called_once()
            self.assertIn(
                self.profile.account_entity, self.ha.watch_entities.call_args.args[0]
            )
            # Even a public group event cannot insert house groups into a personal profile.
            await feature.on_state_change(
                feature.entity("schedule_group"), {}, {"state": "GPV5.1"}
            )
            self.assertEqual(feature.state_get("current_groups"), ["GPV3.1"])

    async def test_shared_status_is_read_only_and_uses_cabinet_groups_and_overrides(self):
        from main import Orchestrator
        from template_engine import TemplateEngine

        with TemporaryDirectory() as directory:
            state = StateStore(Path(directory) / "state.json")
            self.addAsyncCleanup(state.flush)
            app = Orchestrator.__new__(Orchestrator)
            app.ha, app.state = self.ha, state
            self.ha.get_state.return_value = {"state": "unavailable", "attributes": {}}
            telegram = SimpleNamespace(send_message=AsyncMock(), edit_message_result=AsyncMock())
            app.bots = SimpleNamespace(service=lambda a: telegram)
            app.personal_sources = {self.profile.key: self.source}
            app.templates = TemplateEngine(Path(directory) / "templates")
            monitor = SimpleNamespace(
                get_power_state=AsyncMock(return_value="on"),
                get_voltage_snapshot=AsyncMock(return_value={"single_voltage": 260, "phases": []}),
            )
            app._power_monitors = {self.profile.key: monitor}
            self.profile.status_message.enabled = False
            self.snapshot["calendars"]["outage"]["events"][0]["start"] = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            text = await app.render_status(self.profile)
            self.assertIn("🔵 Група GPV3.1", text)
            self.assertEqual(text.count("GPV3.1"), 1)
            self.assertIn("🔌 Напруга: 260", text)
            self.assertNotIn("Електропостачання: норма", text)
            self.assertIn("📅 Найближче планове:\n", text)
            self.assertIn("🕒 Оновлено:", text)
            self.snapshot["attributes"]["schedule_notifications_ready"] = False
            monitor.get_power_state.return_value = "unknown"
            text = await app.render_status(self.profile)
            self.assertIn("❔ Стан живлення невідомий", text)
            self.assertIn("Графік тимчасово недоступний", text)
            self.assertNotIn("не плануються", text)
            app.templates.save_override("status_on", "CUSTOM {{ short_name }} / {{ group }} / {{ voltage }}")
            monitor.get_power_state.return_value = "on"
            text = await app.render_status(self.profile)
            self.assertTrue(text.startswith("CUSTOM "))
            self.assertIn("GPV3.1 / 260", text)
            telegram.send_message.assert_not_awaited()
            telegram.edit_message_result.assert_not_awaited()
            self.assertIsNone(state.get(self.profile.key, "status_message_id"))
            self.assertTrue(all(c.kwargs["action"] == "snapshot" for c in self.ha.command.call_args_list))
            await state.flush()

    async def test_personal_profile_wires_all_enabled_notifications_to_private_source(self):
        from main import Orchestrator
        from template_engine import TemplateEngine

        with TemporaryDirectory() as directory:
            app = Orchestrator.__new__(Orchestrator)
            self.profile.voltage.enabled = True
            from config import VoltageEntityConfig
            self.profile.voltage.entities = [VoltageEntityConfig(entity="sensor.grid_voltage")]
            self.profile.power.enabled = True
            app.config = FullConfig(addresses=[self.profile])
            app.ha, app.personal_sources = self.ha, {self.profile.key: self.source}
            app.state, app.templates = StateStore(Path(directory) / "state.json"), TemplateEngine()
            telegram = SimpleNamespace()
            app.bots = SimpleNamespace(service=lambda a: telegram, chat=lambda a: a.telegram_chat_id)
            app.features, app._features_by_address, app._power_monitors = [], {}, {}
            app._init_features()
            self.assertEqual({f.name for f in app.features}, {
                "group_change", "schedule_change", "emergency", "voltage",
                "power_presence", "upcoming_outage", "status_message",
            })
            for feature in app.features:
                self.assertIs(feature.ha, self.source)
                self.assertIs(feature.tg, telegram)
                self.assertEqual(feature.telegram_chat_id, "123")
            self.assertIn("sensor.grid_voltage", app._collect_watched_entities())


class ImageTests(unittest.TestCase):
    def data(self):
        return {
            "status": "ready",
            "address": "Приклад",
            "days": [
                {
                    "date": "2026-10-19",
                    "slots": [{"label": "00:00"}, {"label": "00:30"}],
                    "rows": [
                        {
                            "id": "GPV1.1",
                            "label": "GPV1.1",
                            "home": True,
                            "selected": False,
                            "cells": ["outage", "unknown"],
                        },
                        {
                            "id": "GPV3.1",
                            "label": "GPV3.1",
                            "home": False,
                            "selected": True,
                            "cells": ["clear", "forecast"],
                        },
                    ],
                }
            ],
        }

    def test_valid_png_all_rows_and_distinct_personal_highlight(self):
        data = self.data()
        a = render_grid(data)
        b = render_grid(data, personal=True)
        self.assertNotEqual(a, b)
        image = Image.open(BytesIO(b))
        image.verify()

    def test_no_fabricated_empty_or_outage_cells(self):
        data = self.data()
        for row in data["days"][0]["rows"]:
            row["cells"] = ["unknown", "unknown"]
        with self.assertRaises(ValueError):
            render_grid(data)
        data = self.data()
        data["days"][0]["rows"][0]["cells"][0] = "fake"
        with self.assertRaises(ValueError):
            render_grid(data)


class IncomingTests(unittest.IsolatedAsyncioTestCase):
    async def test_cursor_committed_before_handler_and_duplicates_skipped(self):
        from telegram_service import TelegramService

        with TemporaryDirectory() as directory:
            journal = DeliveryJournal(Path(directory) / "journal.sqlite")
            self.addCleanup(journal.close)
            update = SimpleNamespace(
                update_id=10,
                model_dump=lambda **kw: {"message": {"text": "private reading"}},
            )
            service = TelegramService("123456:TEST_TOKEN", "-1001")
            self.addAsyncCleanup(service._bot.session.close)
            service.journal = journal

            async def handler(item):
                self.assertEqual(journal.offset, 11)
                raise RuntimeError("delivery failed after operation")

            service._incoming_handler = AsyncMock(side_effect=handler)
            service._bot.get_updates = AsyncMock(
                side_effect=[[update, update], asyncio.CancelledError()]
            )
            with self.assertRaises(asyncio.CancelledError):
                await service._poll_feedback()
            service._incoming_handler.assert_awaited_once()
            self.assertEqual(journal.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    async def test_disabled_feedback_keeps_cursor_without_saving_private_payload(self):
        with TemporaryDirectory() as directory:
            journal = DeliveryJournal(Path(directory) / "journal.sqlite")
            self.addCleanup(journal.close)
            for update_id, kind in enumerate(("message", "callback_query", "edited_message"), 1):
                journal.ingest(update_id, {
                    kind: {"chat": {"id": 123}, "text": "private reading"}
                }, "")
            self.assertEqual(journal.offset, 4)
            self.assertEqual(journal.db.execute("SELECT COUNT(*) FROM incoming").fetchone()[0], 3)
            self.assertEqual(journal.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    async def test_webhook_and_shared_poller_lock_are_not_overwritten(self):
        from telegram_service import TelegramService

        with TemporaryDirectory() as directory:
            service = TelegramService("123456:TEST_TOKEN", "-1001")
            self.addAsyncCleanup(service._bot.session.close)
            service.journal = DeliveryJournal(Path(directory) / "journal.sqlite")
            self.addCleanup(service.journal.close)
            service._bot.get_webhook_info = AsyncMock(
                return_value=SimpleNamespace(url="https://example.invalid/webhook")
            )
            lock = Path(directory) / "updates.lock"
            with self.assertRaises(RuntimeError):
                await service.start_updates(lock)
            self.assertIsNone(service._feedback_task)
            service._bot.get_webhook_info.return_value = SimpleNamespace(url="")
            service._poll_feedback = AsyncMock()
            await service.start_updates(lock)
            with self.assertRaises(RuntimeError):
                await service.start_updates(lock)
            await service._feedback_task
            service._feedback_lock.close()


class DispatchIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_building_group_event_queues_cabinet_read_in_worker(self):
        from main import Orchestrator

        app = Orchestrator.__new__(Orchestrator)
        profile = personal()
        source = SimpleNamespace(
            config=profile,
            snapshot={
                "entities": {"schedule_group": "sensor.house_primary_schedule_group"}
            },
            get_state=AsyncMock(),
        )
        app.personal_sources = {profile.key: source}
        feature = SimpleNamespace(name="group_change")
        app._features_by_entity = {
            f"sensor.{profile.entity_prefix}_schedule_group": [feature]
        }
        app._enqueue_features = AsyncMock()
        await app._dispatch_state_change(
            "sensor.house_primary_schedule_group", {}, {"state": "GPV5.1"}
        )
        source.get_state.assert_not_awaited()
        self.assertEqual(
            app._enqueue_features.call_args.args,
            (
                "on_state_change",
                [feature],
                (f"sensor.{profile.entity_prefix}_schedule_group", {}, {}),
            ),
        )

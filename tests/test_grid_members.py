"""Large schedule views and multiple explicitly authorized private recipients."""

from copy import deepcopy
from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rootfs/app"))
import test_personal_profiles as fixtures
from config import AddressConfig, FullConfig, delivery_profiles
from bot_profiles import BotProfileStore, validate_destinations
from bot_runtime import BotRuntime
from ha_client import HACommandError
from schedule_pages import select_page, render_page
from PIL import Image


def large_grid(count=60):
    slots = [{"label": f"{i//2:02}:{30*(i%2):02}", "offset": "+0300"} for i in range(48)]
    rows = [dict(id=f"GPV{i//2+1}.{i%2+1}", label=f"GPV{i//2+1}.{i%2+1}",
                 home=i in (56, 57), selected=i == 56,
                 cells=["clear"] * 20 + ["outage"] * 8 + ["unknown"] * 20) for i in range(count)]
    return {"status": "ready", "address": "Вигаданий будинок", "days": [
        {"date": "2026-10-25", "rows": rows, "slots": slots},
        {"date": "2026-10-26", "rows": deepcopy(rows), "slots": deepcopy(slots)},
    ]}


class GridPagesTests(unittest.TestCase):
    def test_defaults_keep_all_building_groups_and_personal_assignment(self):
        data = large_grid()
        self.assertEqual([r["id"] for r in select_page(data).rows], ["GPV29.1", "GPV29.2"])
        mine = select_page(data, personal=True)
        self.assertEqual([r["id"] for r in mine.rows], ["GPV29.1"])
        self.assertEqual(mine.scope, "mine")

    def test_all_sixty_groups_appear_once_and_source_is_not_mutated(self):
        data = large_grid()
        original = deepcopy(data)
        ids = []
        for page in range(5):
            view = select_page(data, scope="all", page=page)
            self.assertEqual(view.pages, 5)
            self.assertEqual(len(view.rows), 12)
            ids.extend(r["id"] for r in view.rows)
            with Image.open(BytesIO(render_page(view))) as image:
                self.assertEqual(image.width, 840)
                self.assertLess(image.height, 1500)
        self.assertEqual(ids, [r["id"] for r in data["days"][0]["rows"]])
        self.assertEqual(data, original)

    def test_unknown_groups_do_not_become_an_arbitrary_personal_assignment(self):
        data = large_grid()
        for row in data["days"][0]["rows"]:
            row["selected"] = row["home"] = False
        view = select_page(data, personal=True)
        self.assertTrue(view.unassigned)
        self.assertEqual(view.scope, "all")
        self.assertEqual(select_page(data, scope="mine").rows, [])
        for row in data["days"][0]["rows"]:
            row["cells"] = ["unknown"] * 48
        # Unpublished rows stay unknown, and the user can still navigate the table.
        self.assertTrue(render_page(select_page(data)).startswith(b"\x89PNG"))

    def test_dst_slots_and_last_page_are_not_dropped(self):
        for change in (-2, 2):
            data = large_grid(61)
            day = data["days"][0]
            if change == 2:
                day["slots"][8:8] = [{"label": "03:00", "offset": "+0200"}, {"label": "03:30", "offset": "+0200"}]
            else:
                del day["slots"][6:8]
            for row in day["rows"]:
                row["cells"] = ["forecast"] * (48 + change)
            view = select_page(data, scope="all", page=99)
            self.assertEqual((view.page, view.pages, len(view.rows)), (5, 6, 1))
            self.assertEqual(len(view.rows[0]["cells"]), 48 + change)
            self.assertTrue(render_page(view))

    def test_invalid_payload_is_rejected(self):
        for scope, page in [("wrong", 0), ("all", -1), ("all", True)]:
            with self.assertRaises(ValueError):
                select_page(large_grid(), scope=scope, page=page)
        data = large_grid()
        data["days"][0]["rows"][0]["cells"] = []
        with self.assertRaises(ValueError):
            select_page(data)


class MemberConfigTests(unittest.TestCase):
    def test_legacy_status_identity_and_editor_round_trip(self):
        old = fixtures.personal()
        shared = AddressConfig.model_validate({**old.model_dump(), "telegram_user_ids": [123, 456, 789, 901, 902]})
        config = FullConfig(addresses=[shared])
        deliveries = list(delivery_profiles(config))
        self.assertEqual(len(config.addresses), 1)
        self.assertEqual(len(deliveries), 5)
        self.assertEqual(deliveries[0], old)
        self.assertEqual({p.telegram_chat_id for p in deliveries}, {"123", "456", "789", "901", "902"})
        self.assertEqual(len({p.key for p in deliveries}), 5)
        shared.telegram_user_ids = [456, 901, 789, 902]
        self.assertEqual(shared.for_user(456).key, deliveries[1].key)
        restored = FullConfig.model_validate(config.model_dump())
        self.assertEqual(restored.addresses[0].telegram_user_id, 456)
        self.assertEqual(restored.addresses[0].telegram_chat_id, "456")
        self.assertEqual(restored.addresses[0].for_user(456).key, deliveries[1].key)
        with self.assertRaises(ValueError):
            shared.for_user(123)

    def test_invalid_empty_negative_or_duplicate_account_membership(self):
        for users in ([], [0], [-100], [True], [1.2], ["123"], [2**53], list(range(1, 52))):
            with self.subTest(users=users), self.assertRaises(ValueError):
                fixtures.personal(telegram_user_ids=users)
        shared = fixtures.personal(telegram_user_ids=[123, 456, 456])
        self.assertEqual(shared.telegram_user_ids, [123, 456])
        with self.assertRaises(ValueError):
            FullConfig(addresses=[shared, fixtures.personal(profile_id="second", telegram_user_id=456)])

    def test_private_test_mode_never_redirects_other_family_members(self):
        with TemporaryDirectory() as folder:
            config = FullConfig(telegram_bot_token="123456:TEST_TOKEN", test_chat_id="456", addresses=[fixtures.personal(telegram_user_ids=[123, 456])])
            store = BotProfileStore(Path(folder) / "bots.json")
            validate_destinations(config, store)
            runtime = BotRuntime(config, store, Path(folder))
            first, second = list(delivery_profiles(config))
            self.assertEqual(runtime.chat(first), "")
            self.assertEqual(runtime.chat(second), "456")
            self.assertNotEqual(runtime.state.routes[first.key], runtime.state.routes[second.key])


class MemberConversationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ConversationTests.asyncSetUp
    message = fixtures.ConversationTests.message
    callback = fixtures.ConversationTests.callback

    async def test_five_private_menus_no_implicit_guest_access(self):
        self.profile.telegram_user_ids = [123, 456, 789, 901, 902]
        for user in self.profile.telegram_user_ids:
            await self.router.handle(self.message(user=user))
            self.assertEqual(self.bot.send_message.call_args.kwargs["chat_id"], user)
            self.assertIn("Передати покази", str(self.bot.send_message.call_args.kwargs["reply_markup"]))
        self.assertEqual(self.router.profiles(777), [])
        self.assertEqual(len(self.app.config.addresses), 1)

    async def test_revocation_and_buttons_are_scoped_to_the_person(self):
        self.profile.telegram_user_ids = [123, 456]
        first, second = self.router.profiles(123)[0], self.router.profiles(456)[0]
        token = self.router.keyboard(456, second, [("Check", "history", None)]).inline_keyboard[0][0].callback_data
        self.router.action = AsyncMock()
        await self.router.handle(self.callback(token, user=123))
        self.router.action.assert_not_awaited()
        await self.router.handle(self.callback(token, user=456))
        self.router.action.assert_awaited_once_with(456, second, "history", None)
        self.profile.telegram_user_ids = [123]
        with self.assertRaises(HACommandError):
            await self.router.cabinet(second, 456, "confirm", confirmation="obsolete")
        self.app.ha.command.assert_not_awaited()
        await self.router.cabinet(first, 123, "history")
        self.assertEqual(self.app.ha.command.call_args.kwargs["telegram_user_id"], 123)

    async def test_reminders_and_documents_are_independent_per_person(self):
        self.profile.telegram_user_ids = [123, 456]
        self.profile.reading_reminders = self.profile.document_notifications = True
        self.app.ha.command.return_value = {"due": True, "period": "2026-10"}
        self.router.check_documents = AsyncMock()
        await self.router.on_tick()
        self.assertEqual({c.kwargs["chat_id"] for c in self.bot.send_message.call_args_list}, {123, 456})
        self.assertEqual({c.args[1] for c in self.router.check_documents.call_args_list}, {123, 456})
        self.bot.send_message.reset_mock()
        await self.router.on_tick()
        self.bot.send_message.assert_not_awaited()

    async def test_grid_navigation_replaces_screen_and_full_export_remains_available(self):
        self.app.ha.command.return_value = large_grid()
        self.bot.send_photo = AsyncMock(side_effect=lambda **kw: SimpleNamespace(message_id=100 + self.bot.send_photo.await_count))
        self.bot.send_document = AsyncMock(return_value=SimpleNamespace(message_id=200))
        await self.router.grid(123, self.profile, 0)
        self.assertIn("Моя група", self.bot.send_photo.call_args.kwargs["caption"])
        self.assertEqual(self.app.ha.command.call_args.kwargs["action"], "refresh")
        request = {"date": "2026-10-25", "scope": "all", "page": 4}
        await self.router.grid(123, self.profile, request)
        self.assertIn("5 із 5", self.bot.send_photo.call_args.kwargs["caption"])
        self.assertEqual(self.app.ha.command.call_args.kwargs["action"], "grid")
        self.bot.delete_message.assert_awaited_once_with(chat_id="123", message_id=101)
        await self.router.grid(123, self.profile, {**request, "full": True})
        self.bot.send_document.assert_awaited_once()
        self.assertIn("Повна таблиця", self.bot.send_document.call_args.kwargs["caption"])

    async def test_old_date_button_cannot_silently_show_a_different_day(self):
        self.app.ha.command.return_value = large_grid()
        await self.router.grid(123, self.profile, {"date": "2026-10-24", "scope": "all", "page": 0})
        self.assertIn("Дата вже минула", self.bot.send_message.call_args.kwargs["text"])

    async def test_two_people_get_distinct_submission_principals(self):
        self.profile.telegram_user_ids = [123, 456]
        for user in (123, 456):
            await self.router.cabinet(self.router.profiles(user)[0], user, "prepare", values={"GENERAL": "12345"})
        calls = [c.kwargs for c in self.app.ha.command.call_args_list]
        self.assertEqual({c["telegram_user_id"] for c in calls}, {123, 456})
        self.assertEqual(len({c["profile_id"] for c in calls}), 2)

    async def test_notifications_and_pinned_status_routes_exist_for_all_five_members(self):
        from main import Orchestrator
        from personal_source import PersonalSource
        self.profile.telegram_user_ids = [123, 456, 789, 901, 902]
        config = FullConfig(telegram_bot_token="123456:TEST_TOKEN", receive_commands=False, addresses=[self.profile])
        store = BotProfileStore(Path(self.tmp.name) / "bots.json")
        runtime = BotRuntime(config, store, Path(self.tmp.name))
        await runtime.start()
        try:
            app = Orchestrator.__new__(Orchestrator)
            app.config, app.ha, app.bots = config, self.app.ha, runtime
            app.deliveries = list(delivery_profiles(config))
            app.personal_sources = {p.key: PersonalSource(app.ha, p) for p in app.deliveries}
            app.state, app.templates = runtime.state, self.app.templates
            app.features, app._features_by_address, app._power_monitors = [], {}, {}
            app._init_features()
            for person in app.deliveries:
                features = app._features_by_address[person.key]
                self.assertTrue({"schedule_change", "group_change", "status_message"}.issubset({f.name for f in features}))
                self.assertEqual({f.telegram_chat_id for f in features}, {str(person.telegram_user_id)})
                self.assertEqual(runtime.clients["default"].allowed_routes[person.key], str(person.telegram_user_id))
            first, second = app.deliveries[:2]
            runtime.state.set(first.key, "status_message_id", 1234)
            runtime.state.set(first.key, "last_schedule_signature", "already_sent")
            self.assertIsNone(runtime.state.get(second.key, "status_message_id"))
            self.assertIsNone(runtime.state.get(second.key, "last_schedule_signature"))
        finally:
            await runtime.close()

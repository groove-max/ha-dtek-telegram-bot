"""Credential isolation, routing migration and independent Telegram workers."""

import asyncio
import json
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rootfs/app"))
from bot_profiles import (
    BotProfile,
    BotProfileStore,
    ProfileConflict,
    validate_destinations,
)
from bot_runtime import BotRuntime
from config import AddressConfig, FullConfig, build_runtime_config_payload
from delivery_journal import DeliveryJournal
from main import Orchestrator
from state_store import StateStore

TOKEN_A = "123456:TEST_TOKEN_A"
TOKEN_B = "654321:TEST_TOKEN_B"


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = BotProfileStore(self.path / "bots.json")
        self.config = FullConfig(telegram_bot_token=TOKEN_A, telegram_chat_id="-10001")
        self.bot = SimpleNamespace(
            get_me=AsyncMock(
                return_value=SimpleNamespace(id=654321, username="demo_bot")
            ),
            session=SimpleNamespace(close=AsyncMock()),
        )
        self.patcher = patch("bot_profiles.Bot", return_value=self.bot)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def save(self, **changes):
        payload = dict(
            name="House B",
            token=TOKEN_B,
            chat_id="-10002",
            expected_revision=self.store.revision(),
        )
        payload.update(changes)
        return await self.store.save(payload, self.config)

    async def test_verified_token_private_file_and_public_metadata(self):
        profile = await self.save()
        self.assertEqual(profile.id, "bot_654321")
        self.assertEqual(profile.username, "demo_bot")
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        public = json.dumps(self.store.snapshot(self.config))
        self.assertNotIn(TOKEN_A, public)
        self.assertNotIn(TOKEN_B, public)
        self.assertNotIn(TOKEN_B, repr(profile))
        self.assertNotIn(TOKEN_B, json.dumps(build_runtime_config_payload(self.config)))
        self.assertEqual(self.store.read()[profile.id].token, TOKEN_B)
        self.bot.session.close.assert_awaited_once()

    async def test_blank_token_keeps_secret_and_rotation_keeps_identity(self):
        profile = await self.save()
        updated = await self.save(id=profile.id, name="Renamed", token="")
        self.assertEqual(updated.token, TOKEN_B)
        rotated = await self.save(id=profile.id, token="654321:ROTATED_TEST_TOKEN")
        self.assertEqual(rotated.id, profile.id)
        self.assertNotEqual(rotated.token, TOKEN_B)

    async def test_duplicate_bot_and_default_identity_rejected(self):
        await self.save()
        with self.assertRaisesRegex(ValueError, "вже додано"):
            await self.save()
        self.bot.get_me.return_value.id = 123456
        with self.assertRaisesRegex(ValueError, "основний бот"):
            await self.save()

    async def test_token_for_different_bot_cannot_replace_profile(self):
        profile = await self.save()
        self.bot.get_me.return_value.id = 987654
        with self.assertRaisesRegex(ValueError, "іншому боту"):
            await self.save(id=profile.id, token="987654:OTHER_TEST_TOKEN")
        self.assertEqual(self.store.read()[profile.id].token, TOKEN_B)

    async def test_failed_verification_never_saves_or_echoes_secret(self):
        self.bot.get_me.side_effect = RuntimeError(TOKEN_B)
        with self.assertRaises(ValueError) as caught:
            await self.save()
        self.assertNotIn(TOKEN_B, str(caught.exception))
        self.assertFalse(self.store.path.exists())
        self.bot.session.close.assert_awaited_once()

    async def test_concurrent_saves_cannot_overwrite_during_get_me(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed():
            started.set()
            await release.wait()
            return SimpleNamespace(id=654321, username="demo_bot")

        self.bot.get_me.side_effect = delayed
        saving = asyncio.create_task(self.save())
        await started.wait()
        self.store._write(
            {
                "bot_777777": BotProfile(
                    "bot_777777", "777777", "Other", token="777777:TEST_TOKEN"
                )
            }
        )
        release.set()
        with self.assertRaises(ProfileConflict):
            await saving
        self.assertEqual(list(self.store.read()), ["bot_777777"])

    async def test_delete_referenced_profile_is_blocked(self):
        profile = await self.save()
        with self.assertRaises(ValueError):
            self.store.delete(profile.id, self.store.revision(), {profile.id})
        self.store.delete(profile.id, self.store.revision(), set())
        self.assertEqual(self.store.read(), {})

    async def test_destinations_do_not_fall_back_to_default_bot(self):
        self.config.addresses = [
            AddressConfig(entity_prefix="demo", telegram_bot_id="missing")
        ]
        with self.assertRaisesRegex(ValueError, "неналаштованого"):
            validate_destinations(self.config, self.store)
        await self.save(chat_id="")
        self.config.addresses[0].telegram_bot_id = "bot_654321"
        with self.assertRaisesRegex(ValueError, "чат"):
            validate_destinations(self.config, self.store)
        self.config.addresses[0].telegram_chat_id = "-10003"
        validate_destinations(self.config, self.store)

    async def test_test_mode_requires_destination_and_feedback_requires_test_mode(self):
        for changes in (
            {"test_mode": True},
            {"feedback_enabled": True},
            {"feedback_enabled": True, "test_chat_id": "-10003"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                await self.save(**changes)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = BotProfileStore(self.path / "bots.json")
        self.profile = BotProfile(
            "bot_654321",
            "654321",
            "Second",
            token=TOKEN_B,
            chat_id="-10002",
            test_chat_id="-10003",
        )
        self.store._write({self.profile.id: self.profile})
        self.config = FullConfig(
            telegram_bot_token=TOKEN_A,
            telegram_chat_id="-10001",
            addresses=[
                AddressConfig(entity_prefix="house_a"),
                AddressConfig(entity_prefix="house_b", telegram_bot_id=self.profile.id),
                AddressConfig(
                    entity_prefix="house_c",
                    telegram_bot_id=self.profile.id,
                    telegram_chat_id="-10004",
                ),
            ],
        )
        self.runtimes = []

    async def runtime(self, config=None):
        runtime = BotRuntime(config or self.config, self.store, self.path)
        self.runtimes.append(runtime)
        await runtime.start()
        return runtime

    async def asyncTearDown(self):
        for runtime in self.runtimes:
            await runtime.close()

    async def test_one_client_per_bot_and_correct_default_chats(self):
        runtime = await self.runtime()
        a, b, c = self.config.addresses
        self.assertIs(runtime.service(b), runtime.service(c))
        self.assertIsNot(runtime.service(a), runtime.service(b))
        self.assertEqual(
            [runtime.chat(x) for x in (a, b, c)], ["-10001", "-10002", "-10004"]
        )
        self.assertEqual(len(runtime.clients), 2)
        self.assertIsNot(runtime.service(a).journal, runtime.service(b).journal)

    async def test_slow_or_failing_bot_does_not_block_other_worker(self):
        runtime = await self.runtime()
        started, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def slow():
            started.set()
            await release.wait()
            raise RuntimeError("Bad bot")

        first = SimpleNamespace(name="first", on_tick=slow)
        second = SimpleNamespace(
            name="second", on_tick=AsyncMock(side_effect=delivered.set)
        )
        await runtime.enqueue("default", "on_tick", [first])
        await started.wait()
        await runtime.enqueue(self.profile.id, "on_tick", [second])
        await asyncio.wait_for(delivered.wait(), 1)
        release.set()
        await runtime._queues["default"].join()
        await runtime.enqueue("default", "on_tick", [second])
        await runtime._queues["default"].join()
        self.assertEqual(second.on_tick.await_count, 2)

    async def test_status_ids_and_delivery_keys_are_separate_for_same_chat(self):
        self.config.addresses[1].telegram_chat_id = "-10001"
        runtime = await self.runtime()
        runtime.state.set("house_a", "status_message_id", 42)
        self.assertIsNone(runtime.state.get("house_b", "status_message_id"))
        first, second = runtime.clients.values()
        first.journal.prepare_delivery("same", "scope", {}, 99999999999)
        first.journal.finish_delivery("same", "ok", 42)
        self.assertIsNone(second.journal.delivery("same"))

    async def test_migration_preserves_default_status_once_and_switches_safely(self):
        state = StateStore(self.path / "state.json")
        state.set("house_a", "status_message_id", 42)
        state.set("house_a", "status_message_chat_id", "-10001")
        state.set("house_a", "power_state", "on")
        await state.flush()
        legacy = DeliveryJournal(self.path / "production/telegram.sqlite")
        event = legacy.begin("send", "-10001", None, {})
        legacy.finish(event, "ok", {}, 42)
        legacy.close()
        original = await self.runtime()
        self.assertEqual(original.state.get("house_a", "status_message_id"), 42)
        self.assertTrue(original.clients["default"].journal.owns("-10001", 42))
        await original.close()
        self.runtimes.remove(original)
        changed = self.config.model_copy(deep=True)
        changed.addresses[0].telegram_bot_id = self.profile.id
        new = await self.runtime(changed)
        self.assertIsNone(new.state.get("house_a", "status_message_id"))
        self.assertFalse(new.clients[self.profile.id].journal.owns("-10001", 42))
        await new.close()
        self.runtimes.remove(new)
        changed.addresses[0].telegram_bot_id = "default"
        changed.telegram_bot_token = "333333:TEST_TOKEN_C"
        new = await self.runtime(changed)
        self.assertIsNone(new.state.get("house_a", "status_message_id"))
        self.assertFalse(new.clients["default"].journal.owns("-10001", 42))

    async def test_changing_chat_starts_new_status_scope(self):
        old = await self.runtime()
        old.state.set("house_a", "status_message_id", 42)
        await old.close()
        self.runtimes.remove(old)
        self.config.addresses[0].telegram_chat_id = "-10009"
        new = await self.runtime()
        self.assertIsNone(new.state.get("house_a", "status_message_id"))

    async def test_invalid_default_token_keeps_second_bot_running(self):
        self.config.telegram_bot_token = "invalid"
        runtime = await self.runtime()
        self.assertNotIn("default", runtime.clients)
        self.assertIn(self.profile.id, runtime.clients)
        self.assertTrue(runtime.errors["default"])

    async def test_no_tokens_still_allows_editor_start(self):
        self.store._write({})
        runtime = await self.runtime(FullConfig())
        self.assertEqual(runtime.clients, {})

    async def test_duplicate_identity_in_options_never_creates_second_poller(self):
        self.config.telegram_bot_token = TOKEN_B
        runtime = await self.runtime()
        self.assertEqual(len(runtime.clients), 1)
        self.assertIn(self.profile.id, runtime.errors)

    async def test_stale_retry_never_goes_to_old_or_new_chat(self):
        runtime = await self.runtime()
        client = runtime.clients["default"]
        payload = dict(
            chat="-10009",
            text="obsolete",
            context={"address": "house_a", "feature": "voltage"},
        )
        client.journal.prepare_delivery("obsolete", "scope", payload, 99999999999)
        client.journal.db.execute("UPDATE deliveries SET status='retry',retry_at=0")
        client.journal.db.commit()
        client._send = AsyncMock()
        await client.retry_pending(runtime.state)
        client._send.assert_not_awaited()
        self.assertEqual(client.journal.delivery("obsolete")[0], "superseded")

    async def test_token_rotation_keeps_existing_message_identity(self):
        old = await self.runtime()
        old.state.set("house_b", "status_message_id", 84)
        await old.close()
        self.runtimes.remove(old)
        self.store._write(
            {
                self.profile.id: BotProfile(
                    self.profile.id,
                    self.profile.bot_id,
                    "Second",
                    token="654321:ROTATED_TEST_TOKEN",
                    chat_id=self.profile.chat_id,
                )
            }
        )
        new = await self.runtime()
        self.assertEqual(new.state.get("house_b", "status_message_id"), 84)

    async def test_test_template_requires_test_chat_and_uses_selected_bot(self):
        with patch("main.BotProfileStore", return_value=self.store), patch(
            "main.BotRuntime", side_effect=lambda c, s: BotRuntime(c, s, self.path)
        ):
            app = Orchestrator(self.config)
        self.addAsyncCleanup(app.bots.close)
        app.preview_template = AsyncMock(
            return_value={"rendered": "Test", "address": {}, "context": {}}
        )
        with patch("main.TelegramService") as factory:
            service = factory.return_value
            service.send_message = AsyncMock(return_value=3)
            service.close = AsyncMock()
            with self.assertRaisesRegex(ValueError, "тестовий чат"):
                await app.send_template_test(
                    self.config, template_name="status_on", address_index=0
                )
            factory.assert_not_called()
            result = await app.send_template_test(
                self.config, template_name="status_on", address_index=1
            )
            factory.assert_called_once_with(TOKEN_B, "-10002", test_chat_id="-10003")
            self.assertEqual(result["sent"]["chat_id"], "-10003")
            service.close.assert_awaited_once()

    async def test_busy_worker_coalesces_events_without_blocking_ha_dispatch(self):
        runtime = await self.runtime()
        started, release = asyncio.Event(), asyncio.Event()

        async def slow():
            started.set()
            await release.wait()

        feature = SimpleNamespace(
            name="slow", on_start=slow, on_state_change=AsyncMock()
        )
        await runtime.enqueue("default", "on_start", [feature])
        await started.wait()
        for number in range(1000):
            await runtime.enqueue(
                "default",
                "on_state_change",
                [feature],
                ("sensor.demo", {"state": str(number)}, {"state": str(number + 1)}),
            )
        self.assertEqual(runtime._queues["default"].qsize(), 1)
        other = SimpleNamespace(name="healthy", on_start=AsyncMock())
        await runtime.enqueue(self.profile.id, "on_start", [other])
        await asyncio.wait_for(runtime._queues[self.profile.id].join(), 1)
        other.on_start.assert_awaited_once()
        release.set()
        await runtime._queues["default"].join()
        feature.on_state_change.assert_awaited_once_with(
            "sensor.demo", {"state": "0"}, {"state": "1000"}
        )

    async def test_profile_test_mode_does_not_redirect_other_bots(self):
        from dataclasses import replace

        self.store._write({self.profile.id: replace(self.profile, test_mode=True)})
        runtime = await self.runtime()
        self.assertEqual(runtime.chat(self.config.addresses[0]), "-10001")
        self.assertEqual(runtime.chat(self.config.addresses[1]), "-10003")
        self.assertEqual(runtime.chat(self.config.addresses[2]), "-10003")
        self.assertFalse(runtime.clients[self.profile.id]._may_mutate("-10003", 99))

    async def test_incomplete_test_profile_is_paused_instead_of_using_production(self):
        from dataclasses import replace

        self.store._write(
            {self.profile.id: replace(self.profile, test_mode=True, test_chat_id="")}
        )
        runtime = await self.runtime()
        self.assertNotIn(self.profile.id, runtime.clients)
        self.assertEqual(runtime.chat(self.config.addresses[1]), "")
        self.assertIn("default", runtime.clients)

    async def test_feedback_starts_once_per_bot_and_failure_is_isolated(self):
        from dataclasses import replace

        self.store._write(
            {
                self.profile.id: replace(
                    self.profile, test_mode=True, feedback_enabled=True
                )
            }
        )
        with patch(
            "bot_runtime.TelegramService.start_feedback",
            new_callable=AsyncMock,
            side_effect=RuntimeError("webhook"),
        ) as feedback:
            runtime = await self.runtime()
            await asyncio.gather(*runtime._feedback_starts)
        feedback.assert_awaited_once()
        self.assertIn(self.profile.id, runtime.errors)
        self.assertEqual(len(runtime.clients), 2)

    async def test_legacy_test_state_is_migrated_without_importing_production_ids(self):
        import hashlib

        self.config.test_chat_id = "-10005"
        namespace = hashlib.sha256(b"123456:-10005").hexdigest()[:16]
        legacy = StateStore(self.path / f"test-{namespace}/state.json")
        legacy.set("house_a", "status_message_id", 66)
        legacy.set("house_a", "status_message_chat_id", "-10005")
        await legacy.flush()
        production = StateStore(self.path / "state.json")
        production.set("house_a", "status_message_id", 77)
        production.set("house_a", "status_message_chat_id", "-10001")
        await production.flush()
        runtime = await self.runtime()
        self.assertEqual(runtime.state.get("house_a", "status_message_id"), 66)
        await runtime.close()
        self.runtimes.remove(runtime)
        self.config.test_chat_id = ""
        runtime = await self.runtime()
        # Production history should also survive an upgrade performed in test mode.
        self.assertEqual(runtime.state.get("house_a", "status_message_id"), 77)


class BotAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        import config
        from ui_server import UIServer

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = BotProfileStore(self.path / "bots.json")
        self.config = FullConfig(
            telegram_bot_token=TOKEN_A,
            telegram_chat_id="-10001",
            addresses=[AddressConfig(entity_prefix="demo")],
        )
        for name in ("CONFIG_PATH_HA", "CONFIG_PATH"):
            patcher = patch.object(config, name, self.path / f"{name}.yaml")
            patcher.start()
            self.addCleanup(patcher.stop)
        config.save_runtime_config_payload(build_runtime_config_payload(self.config))
        with patch("main.BotProfileStore", return_value=self.store), patch(
            "main.BotRuntime", side_effect=lambda c, s: BotRuntime(c, s, self.path)
        ):
            self.app = Orchestrator(self.config)
        self.app.preview_runtime_config = AsyncMock(return_value={"addresses": []})
        self.addAsyncCleanup(self.app.bots.close)
        self.client = TestClient(TestServer(UIServer(self.app)._app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        bot = SimpleNamespace(
            get_me=AsyncMock(
                return_value=SimpleNamespace(id=654321, username="demo_bot")
            ),
            session=SimpleNamespace(close=AsyncMock()),
        )
        patcher = patch("bot_profiles.Bot", return_value=bot)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def create(self):
        response = await self.client.post(
            "/api/bots/save",
            json={
                "name": "Second",
                "token": TOKEN_B,
                "chat_id": "-10002",
                "expected_revision": self.store.revision(),
            },
        )
        self.assertEqual(response.status, 200)
        text = await response.text()
        self.assertNotIn(TOKEN_A, text)
        self.assertNotIn(TOKEN_B, text)
        return await response.json()

    async def test_public_endpoints_never_return_tokens(self):
        result = await self.create()
        self.assertEqual(result["profile"]["username"], "demo_bot")
        for path in ("bots", "config", "overview", "diagnostics"):
            response = await self.client.get(f"/api/{path}")
            self.assertEqual(response.status, 200)
            content = await response.text()
            self.assertNotIn(TOKEN_A, content)
            self.assertNotIn(TOKEN_B, content)

    async def test_duplicate_address_validation_does_not_expose_option_secrets(self):
        payload = build_runtime_config_payload(self.config)
        payload["addresses"] *= 2
        response = await self.client.post(
            "/api/config/validate", json={"config": payload}
        )
        self.assertEqual(response.status, 400)
        self.assertNotIn(TOKEN_A, await response.text())
        with self.assertRaises(ValueError) as caught:
            FullConfig(telegram_bot_token=TOKEN_A, addresses=payload["addresses"])
        self.assertNotIn(TOKEN_A, str(caught.exception))

    async def test_malformed_default_token_is_not_public_metadata(self):
        self.app.config.telegram_bot_token = "INVALID_PRIVATE_SECRET"
        response = await self.client.get("/api/bots")
        self.assertNotIn("INVALID_PRIVATE_SECRET", await response.text())

    async def test_unknown_profile_rejected_by_save_and_validation(self):
        payload = build_runtime_config_payload(self.config)
        payload["addresses"][0]["telegram_bot_id"] = "absent"
        for endpoint in ("save", "validate"):
            response = await self.client.post(
                f"/api/config/{endpoint}", json={"config": payload}
            )
            self.assertEqual(response.status, 400)
            self.assertIn("неналаштованого", (await response.json())["message"])

    async def test_delete_checks_saved_and_running_references(self):
        import config

        result = await self.create()
        bot_id = result["profile"]["id"]
        payload = build_runtime_config_payload(self.config)
        payload["addresses"][0]["telegram_bot_id"] = bot_id
        config.save_runtime_config_payload(payload)
        response = await self.client.post(
            "/api/bots/delete",
            json={"id": bot_id, "expected_revision": self.store.revision()},
        )
        self.assertEqual(response.status, 400)
        config.save_runtime_config_payload(build_runtime_config_payload(self.config))
        self.app.config.addresses[0].telegram_bot_id = bot_id
        response = await self.client.post(
            "/api/bots/delete",
            json={"id": bot_id, "expected_revision": self.store.revision()},
        )
        self.assertEqual(response.status, 400)
        self.app.config.addresses[0].telegram_bot_id = "default"
        response = await self.client.post(
            "/api/bots/delete",
            json={"id": bot_id, "expected_revision": self.store.revision()},
        )
        self.assertEqual(response.status, 200)

    async def test_stale_profile_revision_is_http_conflict(self):
        await self.create()
        response = await self.client.post(
            "/api/bots/save", json={"expected_revision": "stale", "token": TOKEN_B}
        )
        self.assertEqual(response.status, 409)
        self.assertNotIn(TOKEN_B, await response.text())

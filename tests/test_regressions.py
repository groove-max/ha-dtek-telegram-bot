"""October 2026 incident, isolation, delivery and freshness regressions."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_group_change_feature as group_tests
import test_power_monitor as monitor_tests
from test_power_monitor import DummyHA, DummyState
import test_power_presence_feature as presence_tests
import test_voltage_feature as voltage_tests
from delivery_journal import DeliveryJournal
from groups import state_groups, select_events
from outage_calendar import extract_calendar_events, build_schedule_lines
from power_monitor import PowerMonitor
from state_store import StateStore
import telegram_service as tg_module


class GroupRecoveryTests(unittest.IsolatedAsyncioTestCase):
    _build_feature = group_tests.GroupChangeFeatureTest._build_feature
    prefix = group_tests.GroupChangeFeatureTest.prefix

    async def transition(self, feature, old, new):
        entity = feature.entity("schedule_group")
        feature.ha.states[entity] = new
        await feature.on_state_change(entity, old, new)

    async def test_morning_history_and_reconnect_once(self):
        feature, telegram, state, _ = self._build_feature()
        await self.transition(feature, {}, {"state": "GPV5.1"})
        await self.transition(feature, {"state": "GPV5.1"}, {"state": "GPV3.1"})
        await self.transition(feature, {"state": "GPV3.1"}, {"state": "unavailable"})
        self.assertEqual(state.get(self.prefix, "current_group"), "GPV3.1")
        await self.transition(feature, {"state": "unavailable"}, {"state": "GPV5.1"})
        await feature.on_tick()
        self.assertEqual(
            telegram.messages,
            ["group_change|GPV5.1->GPV3.1", "group_change|GPV3.1->GPV5.1"],
        )

    async def test_order_is_ignored_but_second_group_addition_is_detected(self):
        feature, telegram, _, _ = self._build_feature()
        first = {
            "state": "GPV5.1",
            "attributes": {"schedule_groups": ["GPV5.1", "GPV3.1"]},
        }
        reordered = {
            "state": "GPV3.1",
            "attributes": {"schedule_groups": ["GPV3.1", "GPV5.1"]},
        }
        await self.transition(feature, {}, first)
        await self.transition(feature, first, reordered)
        self.assertEqual(telegram.messages, [])
        added = {
            "state": "GPV3.1",
            "attributes": {"schedule_groups": ["GPV3.1", "GPV5.1", "GPV6.1"]},
        }
        await self.transition(feature, reordered, added)
        self.assertEqual(len(telegram.messages), 1)
        self.assertIn("GPV6.1", telegram.messages[0])

    async def test_failed_send_does_not_acknowledge_change(self):
        feature, telegram, state, _ = self._build_feature()
        await self.transition(feature, {}, {"state": "GPV3.1"})
        with patch.object(telegram, "send_message", AsyncMock(return_value=None)):
            await self.transition(feature, {}, {"state": "GPV5.1"})
        self.assertEqual(state.get(self.prefix, "notified_groups"), ["GPV3.1"])
        await feature.on_tick()
        self.assertEqual(telegram.messages, ["group_change|GPV3.1->GPV5.1"])


class CalendarContractTests(unittest.TestCase):
    def test_failure_is_not_empty_schedule(self):
        for response in ({}, {"success": False}, {"result": {"response": {}}}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                extract_calendar_events(response, "calendar.test")
        self.assertEqual(
            extract_calendar_events(
                {"result": {"response": {"calendar.test": {"events": []}}}},
                "calendar.test",
            ),
            [],
        )

    def test_untagged_multiple_groups_are_rejected(self):
        with self.assertRaises(ValueError):
            select_events([{"start": "a", "end": "b"}], ["GPV3.1", "GPV5.1"])

    def test_selected_group_and_mismatched_calendar(self):
        events = [{"description": f"Planned; group={g}"} for g in ("GPV3.1", "GPV5.1")]
        self.assertEqual(
            select_events(events, ["GPV3.1", "GPV5.1"], "GPV5.1"), [events[1]]
        )
        with self.assertRaises(ValueError):
            select_events(events, ["GPV3.1"])


class FreshnessTests(unittest.IsolatedAsyncioTestCase):
    _build_config = monitor_tests.PowerMonitorTest._build_config

    async def test_old_numeric_reading_is_unknown_even_when_dtek_says_on(self):
        monitor = PowerMonitor(
            self._build_config(mode="voltage_only", voltage_entities=["sensor.v"]),
            DummyHA(
                {
                    "sensor.v": {
                        "state": "227.41",
                        "last_updated": "2026-08-13T18:20:48+00:00",
                    },
                    "binary_sensor.vulytsia_prykladna_12a_power": {"state": "on"},
                }
            ),
            DummyState(),
        )
        self.assertEqual(await monitor.detect_current_power(), "unknown")
        self.assertIsNone((await monitor.get_voltage_snapshot())["single_voltage"])

    async def test_restored_unavailable_after_ha_restart_is_not_a_power_outage(self):
        monitor = PowerMonitor(
            self._build_config(mode="voltage_only", voltage_entities=["sensor.v"]),
            DummyHA(
                {"sensor.v": {"state": "unavailable", "attributes": {"restored": True}}}
            ),
            DummyState(),
        )
        self.assertEqual(await monitor.detect_current_power(), "unknown")

    async def test_last_reported_allows_constant_but_fresh_voltage(self):
        monitor = PowerMonitor(
            self._build_config(mode="voltage_only", voltage_entities=["sensor.v"]),
            DummyHA(
                {
                    "sensor.v": {
                        "state": "227.41",
                        "last_updated": "2026-08-13T18:20:48+00:00",
                        "last_reported": datetime.now(timezone.utc).isoformat(),
                    }
                }
            ),
            DummyState(),
        )
        self.assertEqual(await monitor.detect_current_power(), "on")


class JournalTests(unittest.TestCase):
    def test_cursor_commit_dedup_and_target_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log.sqlite"
            journal = DeliveryJournal(path)
            event = {
                "channel_post": {
                    "chat": {"id": -1001},
                    "message_id": 8,
                    "text": "feedback",
                }
            }
            journal.ingest(10, event, "-1001")
            journal.ingest(10, event, "-1001")
            journal.ingest(
                11, {"message": {"chat": {"id": -999}, "text": "private"}}, "-1001"
            )
            journal.close()
            journal = DeliveryJournal(path)
            self.assertEqual(journal.offset, 12)
            rows = journal.db.execute("SELECT payload FROM events").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertNotIn("private", rows[0][0])
            journal.close()


class VoltageDelayTests(unittest.IsolatedAsyncioTestCase):
    _build_feature = voltage_tests.VoltageFeatureTest._build_feature
    prefix = voltage_tests.VoltageFeatureTest.prefix
    voltage_entity = voltage_tests.VoltageFeatureTest.voltage_entity

    async def test_repeated_bad_samples_keep_original_timer(self):
        feature, ha, telegram, _ = self._build_feature(delay=0.04)
        try:
            ha.states[self.voltage_entity] = {
                "state": "270",
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
            await feature.on_state_change(
                self.voltage_entity,
                {},
                {
                    "state": "270",
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                },
            )
            timer = feature._pending_alerts[self.voltage_entity]
            for voltage in ("271", "272", "273"):
                await asyncio.sleep(0.01)
                ha.states[self.voltage_entity] = {
                    "state": voltage,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
                await feature.on_state_change(
                    self.voltage_entity,
                    {},
                    {
                        "state": voltage,
                        "last_updated": datetime.now(timezone.utc).isoformat(),
                    },
                )
                self.assertIs(feature._pending_alerts[self.voltage_entity], timer)
            await asyncio.sleep(0.03)
            self.assertEqual(len(telegram.messages), 1)
        finally:
            await feature.on_stop()

    async def test_stale_and_restored_normal_values_cannot_clear_alert(self):
        feature, ha, telegram, state = self._build_feature()
        key = f"voltage_alert_{self.voltage_entity}"
        state.set(self.prefix, key, True)
        state.set(self.prefix, f"voltage_alert_type_{self.voltage_entity}", "high")
        for sample in (
            {"state": "230", "last_updated": "2026-08-13T18:20:48+00:00"},
            {
                "state": "230",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "attributes": {"restored": True},
            },
            {"state": "230"},
        ):
            await feature.on_state_change(self.voltage_entity, {}, sample)
            self.assertTrue(state.get(self.prefix, key))
        self.assertEqual(telegram.messages, [])
        await feature.on_stop()

    async def test_restored_value_during_delay_cannot_trigger_alert(self):
        feature, ha, telegram, state = self._build_feature(delay=0.01)
        sample = {
            "state": "270",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        await feature.on_state_change(self.voltage_entity, {}, sample)
        ha.states[self.voltage_entity] = {**sample, "attributes": {"restored": True}}
        await asyncio.sleep(0.03)
        self.assertEqual(telegram.messages, [])
        await feature.on_stop()


class PowerTickTests(unittest.IsolatedAsyncioTestCase):
    _build_feature = presence_tests.PowerPresenceFeatureTest._build_feature
    prefix = presence_tests.PowerPresenceFeatureTest.prefix
    voltage_entity = presence_tests.PowerPresenceFeatureTest.voltage_entity
    loss_entity = presence_tests.PowerPresenceFeatureTest.loss_entity

    async def test_fresh_zero_can_resolve_unknown_after_delay(self):
        feature, _, ha, telegram, state = self._build_feature(loss_delay=0.03)
        feature.config.power.mode = "voltage_only"
        state.set(self.prefix, "power_state", "unknown")
        ha.states[self.voltage_entity] = {
            "state": "0",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        await feature.on_tick()
        self.assertEqual(state.get(self.prefix, "power_state"), "unknown")
        await asyncio.sleep(0.05)
        self.assertEqual(state.get(self.prefix, "power_state"), "off")
        self.assertEqual(len(telegram.messages), 1)
        await feature.on_stop()

    async def test_tick_cannot_bypass_loss_delay_and_short_dip_is_cancelled(self):
        feature, _, ha, telegram, state = self._build_feature(loss_delay=0.05)
        feature.config.power.mode = "voltage_only"
        await feature.on_start()
        ha.states[self.voltage_entity] = {
            "state": "0",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        await feature.on_tick()
        self.assertEqual(state.get(self.prefix, "power_state"), "on")
        self.assertEqual(telegram.messages, [])
        await asyncio.sleep(0.01)
        ha.states[self.voltage_entity] = {
            "state": "230",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        await feature.on_tick()
        await asyncio.sleep(0.06)
        self.assertEqual(telegram.messages, [])
        await feature.on_stop()


class TelegramIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.journal = DeliveryJournal(Path(self.directory.name) / "journal.sqlite")
        self.bot = AsyncMock()
        self.bot.send_message.return_value = SimpleNamespace(message_id=7)
        self.bot.edit_message_text.return_value = True
        self.bot.pin_chat_message.return_value = True
        self.bot.unpin_chat_message.return_value = True
        with patch.object(tg_module, "Bot", return_value=self.bot):
            self.service = tg_module.TelegramService(
                "123:secret", "production", test_chat_id="test", journal=self.journal
            )

    async def asyncTearDown(self):
        await self.service.close()
        self.directory.cleanup()

    async def test_every_operation_is_forced_to_test_and_old_ids_blocked(self):
        self.assertEqual(
            await self.service.edit_message_result(99, "x", chat_id="production"),
            "not_found",
        )
        self.assertFalse(await self.service.pin_message(99))
        self.assertFalse(await self.service.unpin_message(99))
        await self.service.send_message("test", chat_id="production", pin=True)
        await self.service.edit_message_result(7, "edit", chat_id="production")
        await self.service.unpin_message(7, chat_id="production")
        for method in (
            self.bot.send_message,
            self.bot.edit_message_text,
            self.bot.pin_chat_message,
            self.bot.unpin_chat_message,
        ):
            self.assertTrue(method.called)
            self.assertTrue(
                all(call.kwargs["chat_id"] == "test" for call in method.call_args_list)
            )

    async def test_successful_delivery_is_deduplicated(self):
        first = await self.service.send_message("x", delivery_key="event-1")
        second = await self.service.send_message("x", delivery_key="event-1")
        self.assertEqual((first, second), (7, 7))
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_ambiguous_timeout_is_not_resent(self):
        self.bot.send_message.side_effect = TimeoutError()
        await self.service.send_message("x", delivery_key="event-1")
        await self.service.send_message("x", delivery_key="event-1")
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual(
            self.journal.db.execute("SELECT status FROM deliveries").fetchone()[0],
            "unknown",
        )

    async def test_transient_rejection_is_retried_from_persistent_outbox(self):
        class TelegramServerError(Exception):
            pass

        self.bot.send_message.side_effect = TelegramServerError()
        await self.service.send_message("x", delivery_key="event-1")
        self.journal.db.execute("UPDATE deliveries SET retry_at=0")
        self.journal.db.commit()
        self.bot.send_message.side_effect = None
        state = SimpleNamespace(get=lambda *args: None)
        await self.service.retry_pending(state)
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.assertEqual(
            await self.service.send_message("x", delivery_key="event-1"), 7
        )

    def _upcoming(self):
        from features.upcoming_outage import UpcomingOutageFeature

        base, _, state, _ = group_tests.GroupChangeFeatureTest()._build_feature()
        base.config.upcoming_outage = SimpleNamespace(
            enabled=True, minutes=10, power_filter="always", silent=False
        )
        base.ha.states[base.entity("schedule_group")] = {"state": "GPV5.1"}
        feature = UpcomingOutageFeature(
            base.config, base.ha, self.service, state, base.templates
        )
        feature.templates = SimpleNamespace(
            render=lambda template, **ctx: str(ctx["minutes"])
        )
        now = datetime.now(timezone.utc)
        event = {
            "start": (now + timedelta(minutes=10)).isoformat(),
            "end": (now + timedelta(hours=2)).isoformat(),
            "description": "Planned; group=GPV5.1",
        }
        feature._fetch_upcoming_events = AsyncMock(return_value=[event])
        return feature, now

    async def test_upcoming_timeout_does_not_duplicate_when_countdown_changes(self):
        feature, now = self._upcoming()
        self.bot.send_message.side_effect = TimeoutError()
        with patch("features.upcoming_outage.now_kyiv", return_value=now):
            await feature.on_tick()
        with patch(
            "features.upcoming_outage.now_kyiv", return_value=now + timedelta(minutes=1)
        ):
            await feature.on_tick()
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual(
            self.journal.db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 1
        )

    async def test_upcoming_retry_uses_fresh_countdown_after_revalidation(self):
        feature, now = self._upcoming()

        class TelegramServerError(Exception):
            pass

        self.bot.send_message.side_effect = TelegramServerError()
        with patch("features.upcoming_outage.now_kyiv", return_value=now):
            await feature.on_tick()
        self.journal.db.execute("UPDATE deliveries SET retry_at=0")
        self.journal.db.commit()
        self.bot.send_message.side_effect = None
        await self.service.retry_pending(feature.state)
        self.assertEqual(self.bot.send_message.await_count, 1)
        with patch(
            "features.upcoming_outage.now_kyiv", return_value=now + timedelta(minutes=1)
        ):
            await feature.on_tick()
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.assertEqual(self.bot.send_message.call_args.kwargs["text"], "9")
        self.assertEqual(len(feature.state_get("warned_outages")), 1)

    async def test_cancelled_upcoming_is_not_replayed_from_queue(self):
        feature, now = self._upcoming()

        class TelegramServerError(Exception):
            pass

        self.bot.send_message.side_effect = TelegramServerError()
        with patch("features.upcoming_outage.now_kyiv", return_value=now):
            await feature.on_tick()
        self.journal.db.execute("UPDATE deliveries SET retry_at=0")
        self.journal.db.commit()
        self.bot.send_message.side_effect = None
        feature._fetch_upcoming_events.return_value = []
        await feature.on_tick()
        await self.service.retry_pending(feature.state)
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_started_upcoming_is_not_replayed(self):
        feature, now = self._upcoming()

        class TelegramServerError(Exception):
            pass

        self.bot.send_message.side_effect = TelegramServerError()
        with patch("features.upcoming_outage.now_kyiv", return_value=now):
            await feature.on_tick()
        self.journal.db.execute("UPDATE deliveries SET retry_at=0")
        self.journal.db.commit()
        self.bot.send_message.side_effect = None
        with patch(
            "features.upcoming_outage.now_kyiv",
            return_value=now + timedelta(minutes=11),
        ):
            await feature.on_tick()
        await self.service.retry_pending(feature.state)
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_voltage_phases_have_independent_retry_streams(self):
        class TelegramServerError(Exception):
            pass

        self.bot.send_message.side_effect = TelegramServerError()
        for phase in ("L1", "L2"):
            await self.service.send_message(
                phase,
                delivery_key=phase,
                context={"feature": "voltage", "address": "home", "stream": phase},
            )
        rows = self.journal.db.execute("SELECT status FROM deliveries").fetchall()
        self.assertEqual(rows, [("retry",), ("retry",)])

    async def test_changed_voltage_observation_supersedes_retry(self):
        class TelegramServerError(Exception):
            pass

        self.bot.send_message.side_effect = TelegramServerError()
        await self.service.send_message(
            "voltage low",
            delivery_key="low",
            context={
                "feature": "voltage",
                "address": "home",
                "stream": "L1",
                "guards": {"voltage_observation_L1": "low"},
            },
        )
        self.journal.db.execute("UPDATE deliveries SET retry_at=0")
        self.journal.db.commit()
        state = group_tests.DummyState()
        state.set("home", "voltage_observation_L1", "unknown")
        await self.service.retry_pending(state)
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual(
            self.journal.db.execute("SELECT status FROM deliveries").fetchone()[0],
            "superseded",
        )

    async def test_delivery_revision_survives_restart_before_debounced_save(self):
        base, _, _, _ = group_tests.GroupChangeFeatureTest()._build_feature()
        path = Path(self.directory.name) / "state.json"
        state = StateStore(path)
        base.state = state
        base.tg = self.service
        base.ha.states[base.entity("schedule_group")] = {"state": "GPV5.1"}
        await base.read_groups()
        text = base.render("group_change", old_group="GPV3.1", new_group="GPV5.1")
        await base.send_message(text)
        on_disk = json.loads(path.read_text())["addresses"][base.config.entity_prefix]
        self.assertEqual(on_disk["delivery_revision_group_change"], 1)
        reloaded = StateStore(path)
        reloaded.load()
        restarted = type(base)(
            base.config, base.ha, self.service, reloaded, base.templates
        )
        text = restarted.render("group_change", old_group="GPV3.1", new_group="GPV5.1")
        await restarted.send_message(text)
        self.assertEqual(self.bot.send_message.await_count, 1)
        await state.flush()
        await reloaded.flush()

    async def test_changed_emergency_metadata_supersedes_old_retry(self):
        class TelegramServerError(Exception):
            pass

        self.bot.send_message.side_effect = TelegramServerError()
        await self.service.send_message(
            "old end",
            delivery_key="old-emergency",
            context={
                "feature": "emergency",
                "address": "home",
                "state": {"last_outage_end": "12:00"},
            },
        )
        self.journal.db.execute("UPDATE deliveries SET retry_at=0")
        self.journal.db.commit()
        state = group_tests.DummyState()
        state.set("home", "last_outage_end", "13:00")
        await self.service.retry_pending(state)
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual(
            self.journal.db.execute("SELECT status FROM deliveries").fetchone()[0],
            "superseded",
        )


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_state_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StateStore(path)
            store.set("address", "current_groups", ["GPV3.1"])
            await store.flush()
            other = StateStore(path)
            other.load()
            self.assertEqual(other.get("address", "current_groups"), ["GPV3.1"])

"""Integration upgrades must not duplicate alerts or invent an outage end."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rootfs/app"))
from outage_calendar import (
    normalize_calendar_events, upcoming_schedule_events, format_next_outage,
    warning_key, canonical_warning_key, extract_calendar_events,
)
from schedule_notifications import schedule_signature, legacy_schedule_signature, schedule_signature_matches
from features.schedule_change import ScheduleChangeFeature
from features.upcoming_outage import UpcomingOutageFeature
from features.status_message import StatusMessageFeature
from template_engine import TemplateEngine
from state_store import StateStore
from delivery_journal import DeliveryJournal
from telegram_service import TelegramService
from utils import TZ_KYIV
import test_group_change_feature as fixtures

DAY = datetime(2026, 10, 19, tzinfo=TZ_KYIV)
NOW = DAY + timedelta(hours=22, minutes=55)


def event(start=23, end=26, group="GPV5.1", **extra):
    return {
        "start": (DAY + timedelta(hours=start)).isoformat(),
        "end": (DAY + timedelta(hours=end)).isoformat(),
        "description": "Planned; group=" + group, **extra,
    }


def group_state(groups=("GPV5.1",), end=48):
    return {"state": ", ".join(groups), "attributes": {
        "schedule_groups": list(groups), "provider_id": "dtek_oem", "address_id": "sample-building",
        "schedule_notifications_ready": True, "schedule_snapshot_id": "revision-1",
        "schedule_coverage": [
            {"group_id": g, "kind": "outage", "start": DAY.isoformat(),
             "end": (DAY + timedelta(hours=end)).isoformat()}
            for g in groups
        ],
    }}


class ScheduleSemanticsTests(unittest.TestCase):
    def test_split_merge_and_timezone_notation_have_one_identity(self):
        split = [event(23, 24), event(24, 26)]
        merged = [event()]
        utc = [{**e, **{k: datetime.fromisoformat(e[k]).astimezone(timezone.utc).isoformat() for k in ("start", "end")}} for e in split]
        self.assertEqual(schedule_signature(split), schedule_signature(merged))
        self.assertEqual(schedule_signature(utc), schedule_signature(merged))
        self.assertTrue(schedule_signature_matches(legacy_schedule_signature(split), merged))
        self.assertTrue(schedule_signature_matches(legacy_schedule_signature(merged), split))
        self.assertTrue(schedule_signature_matches(legacy_schedule_signature(utc), merged, utc))

    def test_real_time_group_type_and_source_changes_remain_visible(self):
        initial = [event()]
        for changed in ([event(end=27)], [event(start=23.5)], [event(group="GPV3.1")],
                        [event(description="Possible; group=GPV5.1")], [event(source="weekly")]):
            with self.subTest(changed=changed):
                self.assertNotEqual(schedule_signature(initial), schedule_signature(changed))
                self.assertFalse(schedule_signature_matches(schedule_signature(initial), changed))

    def test_event_order_labels_and_duplicate_records_do_not_change_schedule(self):
        a, b = event(), event(group="GPV3.1")
        self.assertEqual(schedule_signature([a, b]), schedule_signature([b, a, a]))
        self.assertEqual(schedule_signature([a]), schedule_signature([{**a, "summary": "New translated title"}]))

    def test_unacknowledged_snapshot_cannot_hide_real_change(self):
        old, new = [event()], [event(end=27)]
        self.assertFalse(schedule_signature_matches(legacy_schedule_signature(old), new, new))

    def test_notification_signature_ignores_parts_outside_displayed_days(self):
        tomorrow = DAY + timedelta(days=1, minutes=5)
        old = [event(24, 26)]
        self.assertTrue(schedule_signature_matches(legacy_schedule_signature(old), [event()], now=tomorrow))
        self.assertEqual(schedule_signature([event(23, 48)], now=NOW),
                         schedule_signature([event(23, 50)], now=NOW))

    def test_merging_preserves_groups_sources_and_gaps(self):
        for second in (event(24, 26, "GPV3.1"), event(24, 26, source="other"), event(24.5, 26)):
            with self.subTest(second=second):
                self.assertEqual(len(normalize_calendar_events([event(23, 24), second])), 2)

    def test_midnight_continuation_is_not_a_new_start(self):
        events = [event(23, 24), event(24, 26)]
        self.assertEqual(upcoming_schedule_events(events, now=DAY + timedelta(hours=23.5)), [])

    def test_coverage_boundary_has_unknown_end_until_next_day_is_verified(self):
        events = [event(23, 24)]
        for end, confirmed in ((24, False), (48, True)):
            result = upcoming_schedule_events(events, now=NOW, group_state=group_state(end=end))
            self.assertEqual(result[0]["end_confirmed"], confirmed)
        text = format_next_outage(events, now=NOW, group_state=group_state(end=24))
        self.assertIn("від 23:00; завершення невідоме", text)
        self.assertNotIn("00:00", text)

    def test_unrelated_group_coverage_cannot_confirm_an_end(self):
        state = group_state(("GPV5.1", "GPV3.1"))
        state["attributes"]["schedule_coverage"][0]["end"] = (DAY + timedelta(days=1)).isoformat()
        result = upcoming_schedule_events([event(23, 24)], now=NOW, group_state=state, groups=["GPV5.1"])
        self.assertFalse(result[0]["end_confirmed"])
        state["attributes"]["schedule_coverage"].pop(0)
        with self.assertRaises(ValueError):
            upcoming_schedule_events([event()], now=NOW, group_state=state, groups=["GPV5.1", "GPV3.1"])

    def test_gap_stops_next_event_search_and_end_inference(self):
        state = group_state(end=24)
        state["attributes"]["schedule_coverage"].append({
            "group_id": "GPV5.1", "kind": "outage", "start": (DAY + timedelta(hours=25)).isoformat(),
            "end": (DAY + timedelta(hours=48)).isoformat(),
        })
        self.assertEqual(upcoming_schedule_events([event(26, 27)], now=NOW, group_state=state), [])
        result = upcoming_schedule_events([event()], now=NOW, group_state=state)
        self.assertFalse(result[0]["end_confirmed"])

    def test_legacy_without_coverage_keeps_supported_behavior(self):
        result = upcoming_schedule_events([event()], now=NOW, group_state={"state": "GPV5.1"})
        self.assertTrue(result[0]["end_confirmed"])

    def test_malformed_coverage_is_not_assumed_complete(self):
        for coverage in (None, [], [{}], [None], [{"group_id": "GPV5.1", "kind": "outage", "start": "bad", "end": "bad"}]):
            state = group_state()
            state["attributes"]["schedule_coverage"] = coverage
            with self.subTest(coverage=coverage), self.assertRaises(ValueError):
                upcoming_schedule_events([event()], now=NOW, group_state=state)

    def test_repeated_dst_hour_is_compared_by_instant(self):
        start = datetime(2026, 10, 25, 3, 30, tzinfo=TZ_KYIV, fold=0)
        now = datetime(2026, 10, 25, 3, 15, tzinfo=TZ_KYIV, fold=1)
        ev = {**event(), "start": start.isoformat(), "end": datetime(2026, 10, 25, 4, tzinfo=TZ_KYIV).isoformat()}
        self.assertEqual(upcoming_schedule_events([ev], now=now), [])

    def test_invalid_calendar_intervals_fail_closed(self):
        for e in (event(23, 23), event(23, 22)):
            with self.assertRaises(ValueError):
                extract_calendar_events({"result": {"response": {"calendar.x": {"events": [e]}}}}, "calendar.x")

    def test_warning_key_reads_both_legacy_and_current_formats(self):
        e = event()
        key = warning_key("GPV5.1", datetime.fromisoformat(e["start"]))
        self.assertEqual(canonical_warning_key(f"GPV5.1:{e['start']}:{e['end']}"), key)
        self.assertEqual(canonical_warning_key(key), key)
        self.assertIsNone(canonical_warning_key("bad"))


class ScheduleUpgradeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.base, self.telegram, self.state, _ = fixtures.GroupChangeFeatureTest()._build_feature()
        self.base.config.group_change.enabled = False
        self.base.config.schedule_change = SimpleNamespace(enabled=True, silent=False)
        self.base.config.upcoming_outage = SimpleNamespace(enabled=True, silent=False, minutes=10, power_filter="always")
        self.base.config.selected_group = ""
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base.templates = TemplateEngine(Path(self.temp.name))
        self.state.save = AsyncMock()
        self.snapshot = group_state()
        self.base.ha.states[self.base.entity("schedule_group")] = self.snapshot
        self.events = [event()]
        self.now = NOW
        for target in ("features.upcoming_outage.now_kyiv", "features.base.now_kyiv",
                       "features.status_message.now_kyiv", "features.schedule_change.now_kyiv",
                       "schedule_notifications.now_kyiv", "outage_calendar.now_kyiv"):
            patcher = patch(target, side_effect=lambda: self.now)
            patcher.start()
            self.addCleanup(patcher.stop)
        async def fetch(**kwargs):
            start = datetime.fromisoformat(kwargs["data"]["start_date_time"])
            end = datetime.fromisoformat(kwargs["data"]["end_date_time"])
            events = [e for e in self.events if datetime.fromisoformat(e["start"]) < end and datetime.fromisoformat(e["end"]) > start]
            return {"success": True, "result": {"response": {kwargs["entity_id"]: {"events": deepcopy(events)}}}}
        self.base.ha.call_service = AsyncMock(side_effect=fetch)

    def feature(self, cls):
        return cls(self.base.config, self.base.ha, self.telegram, self.state, self.base.templates)

    async def test_upgrade_migrates_old_signature_without_message_then_real_change_notifies(self):
        f = self.feature(ScheduleChangeFeature)
        f.state_set("schedule_signature", legacy_schedule_signature([event(23, 24), event(24, 26)]))
        f.state_set("has_schedule", True)
        await f.on_tick()
        self.assertEqual(self.telegram.messages, [])
        self.assertEqual(f.state_get("schedule_signature"), schedule_signature(self.events))
        self.events = [event(end=27)]
        await f.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)
        await f.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)

    async def test_failed_send_does_not_acknowledge_new_semantic_signature(self):
        f = self.feature(ScheduleChangeFeature)
        before = schedule_signature([event(end=25)])
        f.state_set("schedule_signature", before)
        self.telegram.send_message = AsyncMock(return_value=None)
        await f.on_tick()
        await f.on_tick()
        self.assertEqual(f.state_get("schedule_signature"), before)
        self.assertEqual(self.telegram.send_message.await_count, 2)
        self.telegram.send_message.return_value = 1
        await f.on_tick()
        self.assertEqual(f.state_get("schedule_signature"), schedule_signature(self.events))

    async def test_legacy_warning_is_not_repeated_when_end_changes_or_merges(self):
        f = self.feature(UpcomingOutageFeature)
        e = event(23, 24)
        f.state_set("warned_outages", [f"GPV5.1:{e['start']}:{e['end']}"])
        await f.on_tick()
        self.events = [event(end=27)]
        await f.on_tick()
        self.assertEqual(self.telegram.messages, [])
        self.assertEqual(len(f.state_get("warned_outages")), 1)

    async def test_start_change_and_other_group_still_warn(self):
        f = self.feature(UpcomingOutageFeature)
        await f.on_tick()
        self.events = [event(start=23.01)]
        await f.on_tick()
        self.events = [event(group="GPV3.1")]
        self.snapshot["attributes"]["schedule_groups"].append("GPV3.1")
        self.snapshot["attributes"]["schedule_coverage"].append(group_state(("GPV3.1",))["attributes"]["schedule_coverage"][0])
        await f.on_tick()
        self.assertEqual(len(self.telegram.messages), 3)

    async def test_selected_group_does_not_borrow_other_group_or_require_its_coverage(self):
        self.base.config.selected_group = "GPV5.1"
        self.snapshot["attributes"]["schedule_groups"].append("GPV3.1")
        self.events = [event(), event(group="GPV3.1")]
        await self.feature(UpcomingOutageFeature).on_tick()
        self.assertEqual(len(self.telegram.messages), 1)
        self.assertNotIn("GPV3.1", self.telegram.messages[0])

    async def test_unknown_end_has_same_meaning_in_status_and_warning_with_newline(self):
        self.snapshot["attributes"]["schedule_coverage"][0]["end"] = (DAY + timedelta(days=1)).isoformat()
        self.events = [event(23, 24)]
        f = self.feature(UpcomingOutageFeature)
        await f.on_tick()
        text = self.telegram.messages[0]
        self.assertIn("Завершення за графіком: невідомо\n🕒", text)
        self.assertNotIn("✅ До:", text)
        status = await self.feature(StatusMessageFeature)._get_next_outage()
        self.assertIn("завершення невідоме", status)
        self.assertNotIn("00:00", status)
        self.snapshot["attributes"]["schedule_coverage"][0]["end"] = (DAY + timedelta(days=2)).isoformat()
        self.events = [event()]
        await f.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)
        self.assertIn("20.10 02:00", await self.feature(StatusMessageFeature)._get_next_outage())

    async def test_warning_fetches_midnight_continuation_beyond_countdown_window(self):
        self.now = DAY + timedelta(hours=19, minutes=55)
        self.events = [event(20, 24), event(24, 26)]
        await self.feature(UpcomingOutageFeature).on_tick()
        self.assertIn("✅ До: 20.10.2026 02:00\n🕒", self.telegram.messages[0])
        self.assertNotIn("До: 20.10.2026 00:00", self.telegram.messages[0])

    async def test_ongoing_outage_never_warns_again_at_midnight(self):
        self.now = DAY + timedelta(hours=23, minutes=55)
        self.events = [event(23, 24), event(24, 26)]
        await self.feature(UpcomingOutageFeature).on_tick()
        self.assertEqual(self.telegram.messages, [])

    async def test_concurrent_ticks_send_once(self):
        f = self.feature(UpcomingOutageFeature)
        await asyncio.gather(f.on_tick(), f.on_tick())
        self.assertEqual(len(self.telegram.messages), 1)

    async def test_real_state_reload_keeps_warning_acknowledgement(self):
        self.state = StateStore(Path(self.temp.name) / "state.json")
        f = self.feature(UpcomingOutageFeature)
        await f.on_tick()
        await self.state.flush()
        self.state = StateStore(Path(self.temp.name) / "state.json")
        self.state.load()
        self.events = [event(end=27)]
        await self.feature(UpcomingOutageFeature).on_tick()
        await self.state.flush()
        self.assertEqual(len(self.telegram.messages), 1)

    async def test_status_rejects_revision_changes_during_calendar_request(self):
        async def fetch(**kwargs):
            self.snapshot["attributes"]["schedule_snapshot_id"] = "revision-2"
            return {"result": {"response": {kwargs["entity_id"]: {"events": self.events}}}}
        self.base.ha.call_service.side_effect = fetch
        with self.assertRaises(ValueError):
            await self.feature(StatusMessageFeature)._get_next_outage()

    async def test_preview_and_custom_template_receive_unknown_end_explicitly(self):
        import main
        from config import AddressConfig
        self.snapshot["attributes"]["schedule_coverage"][0]["end"] = (DAY + timedelta(days=1)).isoformat()
        self.events = [event(23, 24)]
        self.base.ha.is_connected = True
        app = object.__new__(main.Orchestrator)
        app.ha, app.state = self.base.ha, self.state
        app._build_address_preview = AsyncMock(return_value={})
        monitor = Mock()
        monitor.get_voltage_snapshot = AsyncMock(return_value={"single_voltage": None, "phases": []})
        address = AddressConfig(entity_prefix=self.base.config.entity_prefix, display_name="Test building")
        with patch("main.now_kyiv", return_value=self.now), patch("main.PowerMonitor", return_value=monitor):
            context = await app._build_template_context(address, "upcoming_outage")
            status = await app._build_template_context(address, "status_on")
        self.assertEqual(context["end"], "невідомо")
        self.assertFalse(context["end_confirmed"])
        self.assertIn("завершення невідоме", status["next_outage"])
        override = self.base.templates.render_source("Від: {{ start }}; До: {{ end }}", **context)
        self.assertIn("До: невідомо", override)
        self.assertNotIn("00:00", override)

    async def test_failed_or_missing_coverage_cannot_publish_old_warning_or_status(self):
        for mutation in ("ready", "coverage"):
            with self.subTest(mutation=mutation):
                self.snapshot["attributes"]["schedule_notifications_ready"] = mutation != "ready"
                self.snapshot["attributes"]["schedule_coverage"] = []
                with self.assertRaises(ValueError):
                    await self.feature(UpcomingOutageFeature).on_tick()
                with self.assertRaises(ValueError):
                    await self.feature(StatusMessageFeature)._get_next_outage()
        self.assertEqual(self.telegram.messages, [])


class WarningJournalUpgradeTests(unittest.IsolatedAsyncioTestCase):
    async def test_migration_preserves_sent_ambiguous_and_retry_outcomes_in_service(self):
        for outcome in ("ok", "unknown", "pending", "retry"):
            with self.subTest(outcome=outcome), TemporaryDirectory() as directory:
                path = Path(directory) / "journal.sqlite"
                journal = DeliveryJournal(path)
                e = event(23, 24)
                stream = f"GPV5.1:{e['start']}:{e['end']}"
                raw_key = "upcoming:building:" + stream
                key = hashlib.sha256(f"test-chat:{raw_key}".encode()).hexdigest()
                context = {"feature": "upcoming_outage", "address": "building", "stream": stream}
                journal.prepare_delivery(key, "old-scope", {"chat": "test-chat", "context": context}, time.time() + 3600)
                if outcome != "pending":
                    journal.finish_delivery(key, outcome, 123 if outcome == "ok" else None)
                journal.db.execute("UPDATE deliveries SET retry_at=0")
                journal.db.commit()
                journal.close()
                journal = DeliveryJournal(path)
                # A second restart is idempotent.
                journal.close()
                journal = DeliveryJournal(path)
                bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=456)))
                with patch("telegram_service.Bot", return_value=bot):
                    service = TelegramService("fixture-token", "test-chat", journal=journal)
                    new_stream = warning_key("GPV5.1", datetime.fromisoformat(e["start"]))
                    result = await service.send_message("new end", delivery_key="upcoming:building:" + new_stream,
                                                        context={**context, "stream": new_stream})
                if outcome == "retry":
                    self.assertEqual(result, 456)
                    bot.send_message.assert_awaited_once()
                else:
                    self.assertEqual(result, 123 if outcome == "ok" else None)
                    bot.send_message.assert_not_awaited()
                journal.close()

    async def test_journal_migration_keeps_chat_and_address_isolation(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite"
            journal = DeliveryJournal(path)
            e = event(23, 24)
            stream = f"GPV5.1:{e['start']}:{e['end']}"
            key = hashlib.sha256(f"chat-a:upcoming:house-a:{stream}".encode()).hexdigest()
            journal.prepare_delivery(key, "scope", {"chat": "chat-a", "context": {
                "feature": "upcoming_outage", "address": "house-a", "stream": stream,
            }}, time.time() + 3600)
            journal.finish_delivery(key, "ok", 123)
            journal.close()
            journal = DeliveryJournal(path)
            canonical = canonical_warning_key(stream)
            for chat, address in (("chat-b", "house-a"), ("chat-a", "house-b")):
                other = hashlib.sha256(f"{chat}:upcoming:{address}:{canonical}".encode()).hexdigest()
                self.assertIsNone(journal.delivery(other))
            journal.close()

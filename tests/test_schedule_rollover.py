"""A new Kyiv date alone is not a new DTEK schedule."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock

import test_schedule_upgrade as fixtures
from features.schedule_change import ScheduleChangeFeature
from schedule_notifications import (
    acknowledge_schedule, schedule_signature, schedule_signature_matches,
)
from state_store import StateStore
from utils import TZ_KYIV


class RolloverSignatureTests(unittest.TestCase):
    def test_rollover_across_month_year_and_dst_in_local_or_utc_notation(self):
        for day in ((2026, 11, 4), (2026, 11, 30), (2026, 12, 31),
                    (2026, 3, 28), (2026, 10, 24)):
            for utc in (False, True):
                with self.subTest(day=day, utc=utc):
                    before = datetime(*day, 23, 55, tzinfo=TZ_KYIV)
                    after = (before + timedelta(days=1)).replace(hour=0, minute=1)
                    events = [
                        {"start": before.replace(hour=15).isoformat(), "end": before.replace(hour=18).isoformat(), "description": "Planned; group=GPV1.1"},
                        {"start": after.replace(hour=10).isoformat(), "end": after.replace(hour=12).isoformat(), "description": "Planned; group=GPV2.1", "source": "daily"},
                    ]
                    current = deepcopy(events[1:])
                    if utc:
                        for event in current:
                            for key in ("start", "end"):
                                event[key] = datetime.fromisoformat(event[key]).astimezone(timezone.utc).isoformat()
                    stored = schedule_signature(events, now=before)
                    self.assertNotEqual(stored, schedule_signature(current, now=after))
                    self.assertTrue(schedule_signature_matches(stored, current, events, now=after, previous_at=before.isoformat()))

    def test_unacknowledged_or_invalid_previous_snapshot_cannot_hide_change(self):
        before, after = fixtures.NOW, fixtures.DAY + timedelta(days=1)
        old, changed = [fixtures.event(23, 26)], [fixtures.event(24, 27)]
        stored = schedule_signature(old, now=before)
        for at in (before.isoformat(), None, "bad", (after + timedelta(days=1)).isoformat()):
            with self.subTest(at=at):
                self.assertFalse(schedule_signature_matches(stored, changed, changed, now=after, previous_at=at))

    def test_unseen_extension_beyond_previous_window_is_a_change(self):
        events = [fixtures.event(23, 50)]
        before, after = fixtures.NOW, fixtures.DAY + timedelta(days=1)
        self.assertFalse(schedule_signature_matches(
            schedule_signature(events, now=before), events, events,
            now=after, previous_at=before.isoformat(),
        ))


class RolloverRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ScheduleUpgradeRuntimeTests.asyncSetUp
    feature = fixtures.ScheduleUpgradeRuntimeTests.feature

    def next_day(self, days=1):
        self.now = fixtures.DAY + timedelta(days=days, seconds=10)
        for part in self.snapshot["attributes"]["schedule_coverage"]:
            part["end"] = (self.now.replace(second=0) + timedelta(days=2)).isoformat()

    async def test_expired_day_is_silent_but_later_time_change_notifies(self):
        self.events = [fixtures.event(22, 23), fixtures.event(28, 30)]
        f = self.feature(ScheduleChangeFeature)
        await f.on_start()
        self.next_day()
        await f.on_tick()
        await f.on_tick()
        self.assertEqual(self.telegram.messages, [])
        self.assertEqual(f.state_get("schedule_signature"), schedule_signature(self.events, now=self.now))
        self.events = [fixtures.event(28, 31)]
        await f.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)

    async def test_cross_midnight_continuation_is_silent(self):
        f = self.feature(ScheduleChangeFeature)
        await f.on_start()  # 23:00–02:00
        self.next_day()
        self.events = [fixtures.event(24, 26)]
        await f.on_tick()
        self.assertEqual(self.telegram.messages, [])

    async def test_expiry_to_empty_does_not_send_all_clear(self):
        self.events = [fixtures.event(23, 24)]
        f = self.feature(ScheduleChangeFeature)
        await f.on_start()
        self.next_day()
        await f.on_tick()
        self.assertEqual(self.telegram.messages, [])
        self.assertFalse(f.state_get("has_schedule"))

    async def test_real_cancellation_at_midnight_still_sends_all_clear(self):
        self.events = [fixtures.event(23, 24), fixtures.event(28, 30)]
        f = self.feature(ScheduleChangeFeature)
        await f.on_start()
        self.next_day()
        self.events = []
        await f.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)
        self.assertIn("не плануються", self.telegram.messages[0])

    async def test_new_tomorrow_publication_still_notifies(self):
        self.events = [fixtures.event(28, 30)]
        f = self.feature(ScheduleChangeFeature)
        await f.on_start()
        self.next_day()
        self.events.append(fixtures.event(52, 54))
        await f.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)

    async def test_two_groups_and_real_change_in_only_one(self):
        self.snapshot["attributes"]["schedule_groups"].append("GPV3.1")
        self.events = [fixtures.event(23, 24), fixtures.event(28, 30), fixtures.event(32, 34, "GPV3.1")]
        f = self.feature(ScheduleChangeFeature)
        await f.on_start()
        self.next_day()
        await f.on_tick()
        self.assertEqual(self.telegram.messages, [])
        self.events[-1] = fixtures.event(32.25, 34, "GPV3.1")
        await f.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)

    async def test_failed_send_then_rollover_retries_real_change(self):
        self.events = [fixtures.event(23, 24), fixtures.event(28, 30)]
        f = self.feature(ScheduleChangeFeature)
        await f.on_start()
        baseline = deepcopy(f.state_get("schedule_baseline"))
        self.events[-1] = fixtures.event(28, 31)
        self.telegram.send_message = AsyncMock(return_value=None)
        await f.on_tick()
        self.assertEqual(f.state_get("schedule_baseline"), baseline)
        self.next_day()
        await f.on_tick()
        self.assertEqual(self.telegram.send_message.await_count, 2)
        self.assertEqual(f.state_get("schedule_baseline"), baseline)
        self.telegram.send_message.return_value = 1
        await f.on_tick()
        await f.on_tick()
        self.assertEqual(self.telegram.send_message.await_count, 3)

    async def test_restart_with_unacknowledged_shared_cache_keeps_real_change(self):
        with TemporaryDirectory() as directory:
            self.state = StateStore(Path(directory) / "state.json")
            self.events = [fixtures.event(23, 24), fixtures.event(28, 30)]
            f = self.feature(ScheduleChangeFeature)
            await f.on_start()
            self.next_day()
            self.events = [fixtures.event(28, 31)]
            await f.calendar_snapshot(f._fetch_outage_events)
            await self.state.flush()
            self.state = StateStore(Path(directory) / "state.json")
            self.state.load()
            await self.feature(ScheduleChangeFeature).on_start()
            self.assertEqual(len(self.telegram.messages), 1)
            await self.state.flush()

    async def test_restart_and_missed_days_expire_old_data_silently(self):
        with TemporaryDirectory() as directory:
            self.state = StateStore(Path(directory) / "state.json")
            self.events = [fixtures.event(23, 24), fixtures.event(28, 30)]
            await self.feature(ScheduleChangeFeature).on_start()
            await self.state.flush()
            self.state = StateStore(Path(directory) / "state.json")
            self.state.load()
            self.next_day(days=3)
            await self.feature(ScheduleChangeFeature).on_start()
            self.assertEqual(self.telegram.messages, [])
            await self.state.flush()

    async def test_group_notification_baseline_survives_midnight(self):
        self.events = [fixtures.event(23, 24), fixtures.event(28, 30)]
        f = self.feature(ScheduleChangeFeature)
        await acknowledge_schedule(self.base, self.events, ["schedule"], now=self.now)
        self.next_day()
        await f.on_tick()
        self.assertEqual(self.telegram.messages, [])

    async def test_upgrade_verifies_previous_cache_before_silent_rollover(self):
        self.events = [fixtures.event(23, 24), fixtures.event(28, 30)]
        f = self.feature(ScheduleChangeFeature)
        f.state_set("schedule_signature", schedule_signature(self.events, now=self.now))
        f.state_set("calendar_snapshot", {"events": deepcopy(self.events), "at": self.now.isoformat()})
        self.next_day()
        await f.on_start()
        self.assertEqual(self.telegram.messages, [])
        self.assertTrue(f.state_get("schedule_baseline"))

    async def test_upgrade_must_not_trust_cache_overwritten_after_failed_send(self):
        self.events = [fixtures.event(23, 24), fixtures.event(28, 30)]
        f = self.feature(ScheduleChangeFeature)
        f.state_set("schedule_signature", schedule_signature(self.events, now=self.now))
        self.events[-1] = fixtures.event(28, 31)
        f.state_set("calendar_snapshot", {"events": deepcopy(self.events), "at": self.now.isoformat()})
        self.next_day()
        await f.on_start()
        self.assertEqual(len(self.telegram.messages), 1)


if __name__ == "__main__":
    unittest.main()

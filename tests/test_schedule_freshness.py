"""Display caches cannot enter automated notifications, even across races."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import test_group_change_feature as fixtures
from features.upcoming_outage import UpcomingOutageFeature
from outage_calendar import extract_calendar_events


class ScheduleFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.feature, self.telegram, self.state, _ = (
            fixtures.GroupChangeFeatureTest()._build_feature()
        )
        self.set_state()
        self.fetch = AsyncMock(return_value=[])

    def set_state(self, *, ready=True, revision="v1", group="GPV5.1"):
        self.feature.ha.states[self.feature.entity("schedule_group")] = {
            "state": group,
            "attributes": {
                "schedule_groups": [group],
                "schedule_notifications_ready": ready,
                "schedule_snapshot_id": revision,
            },
        }

    async def test_unverified_schedule_is_blocked_before_calendar_query(self):
        self.set_state(ready=False)
        with self.assertRaises(ValueError):
            await self.feature.calendar_snapshot(self.fetch)
        self.fetch.assert_not_awaited()
        self.assertEqual(self.telegram.messages, [])

    async def test_same_groups_but_changed_schedule_during_query_is_rejected(self):
        async def fetch():
            self.set_state(revision="v2")
            return []

        with self.assertRaises(ValueError):
            await self.feature.calendar_snapshot(fetch)

    async def test_outage_during_query_is_rejected(self):
        async def fetch():
            self.set_state(ready=False)
            return []

        with self.assertRaises(ValueError):
            await self.feature.calendar_snapshot(fetch)

    async def test_failure_during_state_save_prevents_send(self):
        await self.feature.calendar_snapshot(self.fetch)

        async def save():
            self.set_state(ready=False)

        self.state.save = save
        with self.assertRaises(ValueError):
            await self.feature.send_message("obsolete")
        self.assertEqual(self.telegram.messages, [])

    async def test_schedule_change_during_save_prevents_send_even_for_same_groups(self):
        await self.feature.calendar_snapshot(self.fetch)

        async def save():
            self.set_state(revision="v2")

        self.state.save = save
        with self.assertRaises(ValueError):
            await self.feature.send_message("obsolete")
        self.assertEqual(self.telegram.messages, [])

    async def test_recovery_fetches_new_snapshot_before_sending(self):
        await self.feature.calendar_snapshot(self.fetch)
        self.set_state(ready=False)
        with self.assertRaises(ValueError):
            await self.feature.send_message("obsolete")
        self.set_state(revision="v2")
        with self.assertRaises(ValueError):
            await self.feature.send_message("still obsolete")
        await self.feature.calendar_snapshot(self.fetch)
        await self.feature.send_message("new snapshot")
        self.assertEqual(self.telegram.messages, ["new snapshot"])

    async def test_display_only_calendar_events_are_rejected(self):
        now = datetime.now(timezone.utc)
        event = {
            "start": now.isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
            "description": "usage=display_only; Planned; group=GPV5.1",
        }
        with self.assertRaises(ValueError):
            extract_calendar_events(
                {"result": {"response": {"calendar.x": {"events": [event]}}}},
                "calendar.x",
            )
        with self.assertRaises(ValueError):
            await self.feature.calendar_snapshot(AsyncMock(return_value=[event]))

    async def test_unchanged_successful_recheck_does_not_cancel_send(self):
        await self.feature.calendar_snapshot(self.fetch)
        self.set_state(revision="v1")
        await self.feature.send_message("valid")
        self.assertEqual(self.telegram.messages, ["valid"])

    async def test_recovery_does_not_send_warning_for_already_started_outage(self):
        now = datetime.now(timezone.utc)
        self.feature.config.upcoming_outage = SimpleNamespace(
            enabled=True, minutes=10, power_filter="always", silent=False
        )
        f = UpcomingOutageFeature(
            self.feature.config,
            self.feature.ha,
            self.telegram,
            self.state,
            self.feature.templates,
        )
        f._fetch_upcoming_events = AsyncMock(
            return_value=[
                {
                    "start": (now + timedelta(minutes=5)).isoformat(),
                    "end": (now + timedelta(hours=1)).isoformat(),
                    "description": "Planned; group=GPV5.1",
                }
            ]
        )
        self.set_state(ready=False)
        with self.assertRaises(ValueError):
            await f.on_tick()
        self.set_state(revision="v2")
        with patch(
            "features.upcoming_outage.now_kyiv", return_value=now + timedelta(minutes=6)
        ):
            await f.on_tick()
        self.assertEqual(self.telegram.messages, [])

    async def test_warning_expiring_while_waiting_for_ha_is_not_sent(self):
        await self.feature.calendar_snapshot(self.fetch)
        now = datetime.now(timezone.utc)
        with patch("features.base.now_kyiv", return_value=now + timedelta(minutes=6)):
            result = await self.feature.send_message(
                "old countdown",
                delivery_context={"expires": (now + timedelta(minutes=5)).timestamp()},
            )
        self.assertIsNone(result)
        self.assertEqual(self.telegram.messages, [])

    async def test_provider_change_with_same_groups_and_revision_is_rejected(self):
        state = self.feature.ha.states[self.feature.entity("schedule_group")]
        state['attributes'].update(provider_id='dtek_oem', address_id='address-1')
        async def fetch():
            state['attributes']['provider_id'] = 'dtek_krem'
            return []
        with self.assertRaises(ValueError):
            await self.feature.calendar_snapshot(fetch)
        self.assertEqual(self.telegram.messages, [])

    async def test_address_change_after_fetch_prevents_send(self):
        state = self.feature.ha.states[self.feature.entity("schedule_group")]
        state['attributes'].update(provider_id='dtek_oem', address_id='address-1')
        await self.feature.calendar_snapshot(self.fetch)
        state['attributes']['address_id'] = 'address-2'
        with self.assertRaises(ValueError):
            await self.feature.send_message('wrong address')
        self.assertEqual(self.telegram.messages, [])

    async def test_unchanged_provider_contract_is_backward_compatible(self):
        state = self.feature.ha.states[self.feature.entity("schedule_group")]
        state['attributes'].update(provider_id='dtek_oem', address_id='address-1')
        await self.feature.calendar_snapshot(self.fetch)
        await self.feature.send_message('valid')
        self.assertEqual(self.telegram.messages, ['valid'])

    async def test_coverage_requires_every_requested_day_for_every_group(self):
        from utils import now_kyiv
        day=now_kyiv().replace(hour=0,minute=0,second=0,microsecond=0)
        attrs=self.feature.ha.states[self.feature.entity('schedule_group')]['attributes']
        attrs['schedule_coverage']=[{'group_id':'GPV5.1','kind':'outage','start':day.isoformat(),'end':(day+timedelta(days=1)).isoformat()}]
        await self.feature.calendar_snapshot(self.fetch)
        self.assertTrue(self.feature.has_schedule_coverage(day,day+timedelta(days=1)))
        self.assertFalse(self.feature.has_schedule_coverage(day,day+timedelta(days=2)))
        attrs['schedule_coverage'].append({'group_id':'GPV5.1','kind':'outage','start':(day+timedelta(days=1)).isoformat(),'end':(day+timedelta(days=2)).isoformat()})
        await self.feature.calendar_snapshot(self.fetch)
        self.assertTrue(self.feature.has_schedule_coverage(day,day+timedelta(days=2)))
        self.feature.state_set('current_groups',['GPV5.1','GPV3.1'])
        self.assertFalse(self.feature.has_schedule_coverage(day,day+timedelta(days=2)))

    async def test_empty_calendar_with_unpublished_tomorrow_does_not_send_all_clear(self):
        from features.schedule_change import ScheduleChangeFeature
        from utils import now_kyiv
        day=now_kyiv().replace(hour=0,minute=0,second=0,microsecond=0)
        attrs=self.feature.ha.states[self.feature.entity('schedule_group')]['attributes']
        attrs['schedule_coverage']=[{'group_id':'GPV5.1','kind':'outage','start':day.isoformat(),'end':(day+timedelta(days=1)).isoformat()}]
        self.feature.config.schedule_change=SimpleNamespace(enabled=True,silent=False)
        f=ScheduleChangeFeature(self.feature.config,self.feature.ha,self.telegram,self.state,self.feature.templates)
        f._fetch_outage_events=AsyncMock(return_value=[])
        f.state_set('schedule_signature','previous nonempty schedule')
        f.state_set('has_schedule',True)
        f.state_set('last_schedule_seen_at',now_kyiv().isoformat())
        await f.on_tick()
        self.assertEqual(self.telegram.messages,[])
        self.assertEqual(f.state_get('schedule_signature'),'previous nonempty schedule')
        attrs['schedule_coverage'][0]['end']=(day+timedelta(days=2)).isoformat()
        await f.on_tick()
        self.assertEqual(len(self.telegram.messages),1)


class AssignmentWithoutScheduleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = ScheduleFreshnessTests.asyncSetUp
    set_state = ScheduleFreshnessTests.set_state

    async def test_verified_group_change_is_sent_without_all_clear_claim(self):
        self.set_state(ready=False, group="GPV3.1")
        self.feature.state_set("notified_groups", ["GPV5.1"])
        await self.feature.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)
        self.assertEqual(self.feature.state_get("notified_groups"), ["GPV3.1"])
        self.assertNotIn("немає", self.telegram.messages[0])
        self.assertFalse(self.feature.state_get("schedule_signature"))

    async def test_assignment_change_during_save_blocks_old_notice(self):
        self.set_state(ready=False, group="GPV3.1")
        self.feature.state_set("notified_groups", ["GPV5.1"])
        async def save():
            self.set_state(ready=False, group="GPV4.1")
        self.state.save = save
        with self.assertRaises(ValueError):
            await self.feature.on_tick()
        self.assertEqual(self.telegram.messages, [])
        self.assertEqual(self.feature.state_get("notified_groups"), ["GPV5.1"])

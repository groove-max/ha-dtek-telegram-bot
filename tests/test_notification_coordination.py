"""One group change delivers one schedule, including concurrent and retry paths."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest
import test_group_change_feature as fixtures
from features.schedule_change import ScheduleChangeFeature
from schedule_notifications import schedule_signature
from template_engine import TemplateEngine
from utils import now_kyiv


class NotificationCoordinationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.group, self.telegram, self.state, _ = (
            fixtures.GroupChangeFeatureTest()._build_feature()
        )
        self.group.config.schedule_change = SimpleNamespace(enabled=True, silent=False)
        self.schedule = ScheduleChangeFeature(
            self.group.config,
            self.group.ha,
            self.telegram,
            self.state,
            self.group.templates,
        )
        self.events = [
            {
                "start": (now_kyiv() + timedelta(hours=1)).isoformat(),
                "end": (now_kyiv() + timedelta(hours=2)).isoformat(),
                "description": "Planned; group=GPV3.1",
                "summary": "outage",
            }
        ]
        self.group.ha.call_service = AsyncMock(side_effect=self.calendar)
        self.entity = self.group.entity("schedule_group")
        self.group.ha.states[self.entity] = {
            "state": "GPV3.1",
            "attributes": {"schedule_groups": ["GPV3.1"], "schedule_coverage": []},
        }
        self.group.state_set("notified_groups", ["GPV5.1"])
        self.group.state_set("current_group", "GPV5.1")
        self.group.state_set("schedule_signature", "previous")
        self.group.state_set("has_schedule", True)
        self.group.state_set("last_schedule_seen_at", now_kyiv().isoformat())
        self.state.save = AsyncMock()

    async def calendar(self, **kwargs):
        return {
            "success": True,
            "result": {
                "response": {kwargs["entity_id"]: {"events": deepcopy(self.events)}}
            },
        }

    async def test_group_acknowledges_schedule_and_later_real_change_still_sends(self):
        await self.group.on_tick()
        await self.schedule.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)
        self.assertEqual(
            self.group.state_get("schedule_signature"), schedule_signature(self.events)
        )
        self.state.save.assert_awaited()
        self.events[0]["end"] = (now_kyiv() + timedelta(hours=3)).isoformat()
        await self.schedule.on_tick()
        self.assertEqual(len(self.telegram.messages), 2)

    async def test_schedule_event_arrives_first_waits_for_group_delivery(self):
        await self.schedule.on_tick()
        self.assertFalse(self.telegram.messages)
        await self.group.on_tick()
        await self.schedule.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)

    async def test_concurrent_tick_and_group_event_send_once(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.telegram.send_message

        async def send(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        self.telegram.send_message = send
        task = asyncio.create_task(self.group.on_tick())
        await entered.wait()
        concurrent = asyncio.create_task(self.schedule.on_tick())
        await asyncio.sleep(0)
        self.assertFalse(concurrent.done())
        release.set()
        await asyncio.gather(task, concurrent)
        self.assertEqual(len(self.telegram.messages), 1)

    async def test_failed_group_send_is_not_acknowledged_and_retries(self):
        original = self.telegram.send_message
        self.telegram.send_message = AsyncMock(return_value=None)
        await self.group.on_tick()
        await self.schedule.on_tick()
        self.assertEqual(self.group.state_get("schedule_signature"), "previous")
        self.assertEqual(self.group.state_get("notified_groups"), ["GPV5.1"])
        self.telegram.send_message = original
        await self.group.on_tick()
        await self.schedule.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)

    async def test_disabled_group_feature_does_not_block_schedule_updates(self):
        self.group.config.group_change.enabled = False
        await self.schedule.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)

    async def test_restart_after_ack_does_not_resend_schedule(self):
        await self.group.on_tick()
        restarted = ScheduleChangeFeature(
            self.group.config,
            self.group.ha,
            self.telegram,
            self.state,
            self.group.templates,
        )
        await restarted.on_start()
        self.assertEqual(len(self.telegram.messages), 1)

    async def test_empty_unpublished_dates_do_not_claim_all_clear_or_ack_schedule(self):
        self.events = []
        with TemporaryDirectory() as directory:
            self.group.templates = TemplateEngine(Path(directory))
            await self.group.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)
        text = self.telegram.messages[0]
        self.assertIn("GPV5.1 → GPV3.1", text)
        self.assertNotIn("не плануються", text)
        self.assertNotIn("не опубліковано", text)
        self.assertEqual(self.group.state_get("schedule_signature"), "previous")
        await self.schedule.on_tick()
        self.assertEqual(len(self.telegram.messages), 1)

    async def test_nonempty_group_message_omits_publication_note(self):
        with TemporaryDirectory() as directory:
            self.group.templates = TemplateEngine(Path(directory))
            await self.group.on_tick()
        self.assertIn("Графік відключень", self.telegram.messages[0])
        self.assertNotIn("не опубліковано", self.telegram.messages[0])

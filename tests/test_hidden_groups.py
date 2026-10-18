"""Hidden operator IDs stay in provenance, not in Telegram presentation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rootfs/app"))
from template_engine import TemplateEngine
from config import AddressConfig
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from datetime import timedelta
from unittest.mock import AsyncMock
import unittest
import test_group_change_feature as fixtures
from features.schedule_change import ScheduleChangeFeature
from features.status_message import StatusMessageFeature
from features.upcoming_outage import UpcomingOutageFeature
from groups import display_labels, presentation_context, select_events
from outage_calendar import build_schedule_lines
from utils import now_kyiv
import main


class HiddenGroupsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.group, self.tg, self.state, _ = (
            fixtures.GroupChangeFeatureTest()._build_feature()
        )
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.group.templates = TemplateEngine(Path(self.temp.name))
        self.group.config.selected_group = ""
        self.group.config.schedule_change = SimpleNamespace(enabled=True, silent=False)
        self.group.config.upcoming_outage = SimpleNamespace(
            enabled=True, silent=False, minutes=10, power_filter="always"
        )
        self.snapshot = {
            "state": "GPV29.1",
            "attributes": {
                "schedule_groups": ["GPV29.1", "GPV35.1"],
                "group_labels_visible": False,
            },
        }
        self.group.ha.states[self.group.entity("schedule_group")] = self.snapshot
        self.events = [
            {
                "start": (now_kyiv() + timedelta(minutes=5)).isoformat(),
                "end": (now_kyiv() + timedelta(hours=2)).isoformat(),
                "description": f"Planned; group={g}",
            }
            for g in ["GPV29.1", "GPV35.1"]
        ]

        async def calendar(**kwargs):
            return {
                "success": True,
                "result": {"response": {kwargs["entity_id"]: {"events": self.events}}},
            }

        self.group.ha.call_service = AsyncMock(side_effect=calendar)

    def feature(self, cls):
        return cls(
            self.group.config, self.group.ha, self.tg, self.state, self.group.templates
        )

    async def test_schedule_and_group_change_hide_ids_but_keep_raw_identity(self):
        self.group.state_set("notified_groups", ["GPV30.1"])
        await self.group.on_tick()
        text = self.tg.messages[-1]
        self.assertNotIn("GPV", text)
        self.assertIn("Оновлено прив’язку", text)
        self.assertIn("Варіант 1", text)
        self.assertIn("Варіант 2", text)
        self.assertEqual(
            self.group.state_get("notified_groups"), ["GPV29.1", "GPV35.1"]
        )
        self.group.state_set("schedule_signature", "changed")
        await self.feature(ScheduleChangeFeature).on_tick()
        self.assertNotIn("GPV", self.tg.messages[-1])
        self.assertNotIn("Група Варіант", self.tg.messages[-1])
        self.assertIn("уточнення.\n⛔", self.tg.messages[-1])

    async def test_upcoming_uses_display_variant_and_internal_dedup_key(self):
        feature = self.feature(UpcomingOutageFeature)
        await feature.on_tick()
        self.assertEqual(len(self.tg.messages), 1)
        self.assertNotIn("GPV", self.tg.messages[0])
        self.assertIn("Варіант 1", self.tg.messages[0])
        self.assertIn("GPV29.1", feature.state_get("warned_outage_start"))

    async def test_status_unknown_and_next_outage_do_not_leak_ids(self):
        feature = self.feature(StatusMessageFeature)
        text = await feature._build_status_text("unknown")
        self.assertNotIn("GPV", text)
        self.assertIn("Варіант 2", text)
        text = await feature._get_next_outage()
        self.assertNotIn("GPV", text)
        self.assertIn("Варіант 1", text)

    def test_selected_variant_keeps_number_from_full_address_mapping(self):
        chosen = select_events(self.events, ["GPV29.1", "GPV35.1"], "GPV35.1")
        lines = build_schedule_lines(chosen, group_labels=display_labels(self.snapshot))
        self.assertIn("🔵 Варіант 2", lines)
        self.assertFalse(any("GPV" in line for line in lines))

    def test_legacy_and_public_groups_keep_existing_presentation(self):
        for state in [
            {"state": "GPV5.1"},
            {"state": "GPV5.1", "attributes": {"group_labels_visible": True}},
        ]:
            self.assertIsNone(display_labels(state))
            self.assertEqual(
                presentation_context({"group": "GPV5.1"}, state)["group"], "GPV5.1"
            )

    async def test_preview_uses_the_same_hidden_group_presentation(self):
        address = AddressConfig(entity_prefix="test", display_name="Київ • адреса")
        app = object.__new__(main.Orchestrator)
        app.state = self.state
        app.ha = SimpleNamespace(
            is_connected=True, get_state=AsyncMock(return_value=self.snapshot)
        )
        app._build_address_preview = AsyncMock(return_value={})
        app._fetch_calendar_events_for_address = AsyncMock(return_value=self.events)
        context = await app._build_template_context(address, "schedule_change")
        text = self.group.templates.render("schedule_change", **context)
        self.assertNotIn("GPV", text)
        self.assertIn("Варіант 1", text)
        self.assertIn("Варіант 2", text)

    async def test_same_variant_names_cannot_deduplicate_different_internal_groups(
        self,
    ):
        self.group.state_set("notified_groups", ["GPV30.1"])
        await self.group.on_tick()
        first_identity = self.group.state_get("delivery_identity_group_change")
        self.snapshot["state"] = "GPV41.1"
        self.snapshot["attributes"]["schedule_groups"] = ["GPV41.1", "GPV42.1"]
        for event, group in zip(self.events, ["GPV41.1", "GPV42.1"]):
            event["description"] = f"Planned; group={group}"
        await self.group.on_tick()
        self.assertEqual(len(self.tg.messages), 2)
        self.assertNotEqual(
            self.group.state_get("delivery_identity_group_change"), first_identity
        )
        self.assertNotIn("GPV", self.tg.messages[-1])

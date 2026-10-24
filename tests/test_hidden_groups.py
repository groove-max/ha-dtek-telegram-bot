"""Website-hidden groups retain their source IDs in messages and provenance."""

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
from groups import presentation_context, select_events
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

    async def test_schedule_and_group_change_show_source_ids_and_keep_identity(self):
        self.group.state_set("notified_groups", ["GPV30.1"])
        await self.group.on_tick()
        text = self.tg.messages[-1]
        self.assertIn("Група графіку змінена: GPV30.1 → GPV29.1, GPV35.1", text)
        self.assertNotIn("Варіант", text)
        self.assertEqual(
            self.group.state_get("notified_groups"), ["GPV29.1", "GPV35.1"]
        )
        self.group.state_set("schedule_signature", "changed")
        await self.feature(ScheduleChangeFeature).on_tick()
        self.assertIn("Група GPV29.1, GPV35.1", self.tg.messages[-1])
        self.assertNotIn("Варіант", self.tg.messages[-1])
        self.assertIn("уточнення.\n⛔", self.tg.messages[-1])

    async def test_upcoming_uses_same_group_for_display_and_dedup_key(self):
        feature = self.feature(UpcomingOutageFeature)
        await feature.on_tick()
        self.assertEqual(len(self.tg.messages), 1)
        self.assertIn("Група GPV29.1", self.tg.messages[0])
        self.assertNotIn("Варіант", self.tg.messages[0])
        self.assertIn("GPV29.1", feature.state_get("warned_outage_start"))
        self.snapshot["attributes"]["group_labels_visible"] = True
        await feature.on_tick()
        self.assertEqual(len(self.tg.messages), 2)  # The second group still needs its own warning.
        self.assertIn("Група GPV35.1", self.tg.messages[1])
        self.snapshot["attributes"]["group_labels_visible"] = False
        await feature.on_tick()
        self.assertEqual(len(self.tg.messages), 2)

    async def test_status_unknown_and_next_outage_show_source_groups(self):
        feature = self.feature(StatusMessageFeature)
        text = await feature._build_status_text("unknown")
        self.assertIn("Група GPV29.1, GPV35.1", text)
        text = await feature._get_next_outage()
        self.assertIn("GPV29.1", text)
        self.assertNotIn("Варіант", text)

    def test_selected_group_keeps_source_id(self):
        chosen = select_events(self.events, ["GPV29.1", "GPV35.1"], "GPV35.1")
        lines = build_schedule_lines(chosen)
        self.assertIn("🔵 GPV35.1", lines)
        self.assertFalse(any("GPV29.1" in line for line in lines))

    def test_legacy_and_public_groups_keep_existing_presentation(self):
        for state in [
            {"state": "GPV5.1"},
            {"state": "GPV5.1", "attributes": {"group_labels_visible": True}},
            {"state": "GPV5.1", "attributes": {"group_labels_visible": False}},
        ]:
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
        self.assertIn("Група GPV29.1, GPV35.1", text)
        self.assertNotIn("Варіант", text)
        self.assertFalse(context["group_labels_visible"])

    async def test_different_source_groups_keep_distinct_notification_identities(
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
        self.assertIn("GPV29.1, GPV35.1 → GPV41.1, GPV42.1", self.tg.messages[-1])

    async def test_website_visibility_change_alone_does_not_send_group_change(self):
        self.group.state_set("notified_groups", ["GPV29.1", "GPV35.1"])
        for visible in (False, True, False):
            self.snapshot["attributes"]["group_labels_visible"] = visible
            await self.group.on_tick()
        self.assertEqual(self.tg.messages, [])

    async def test_previously_acknowledged_schedule_does_not_repeat_for_new_labels(self):
        feature = self.feature(ScheduleChangeFeature)
        feature.state_set("schedule_signature", feature._compute_signature(self.events, now=now_kyiv()))
        for visible in (False, True):
            self.snapshot["attributes"]["group_labels_visible"] = visible
            await feature.on_tick()
        self.assertEqual(self.tg.messages, [])

    def test_saved_templates_use_group_headings_without_rewriting_user_content(self):
        source = "{% if groups_public %}Група {{ old_group }} → {{ new_group }}{% else %}Приховано{% endif %}"
        path = Path(self.temp.name) / 'group_change.j2'
        path.write_text(source)
        context = {"old_group": "GPV30.1", "new_group": "GPV29.1, GPV35.1"}
        rendered = presentation_context(context, self.snapshot)
        self.assertFalse(rendered["group_labels_visible"])
        self.assertFalse(self.snapshot["attributes"]["group_labels_visible"])
        self.assertEqual(context, {"old_group": "GPV30.1", "new_group": "GPV29.1, GPV35.1"})
        self.assertEqual(
            self.group.templates.render('group_change', **rendered),
            'Група GPV30.1 → GPV29.1, GPV35.1',
        )
        self.assertEqual(path.read_text(), source)

    def test_private_bridge_can_preserve_legacy_labels_and_report_website_visibility(self):
        state = {"state": "GPV29.1", "attributes": {
            "group_labels_visible": True,
            "source_group_labels_visible": False,
        }}
        context = presentation_context({"group": "GPV29.1"}, state)
        self.assertEqual(context["group"], "GPV29.1")
        self.assertTrue(context["groups_public"])
        self.assertFalse(context["group_labels_visible"])

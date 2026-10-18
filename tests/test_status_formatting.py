"""Exercise the actual Jinja templates and status-calendar presentation together."""

import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rootfs/app"))
from template_engine import TemplateEngine
from outage_calendar import format_next_outage
from utils import TZ_KYIV
import test_status_message_feature as fixtures


class StatusFormattingTests(unittest.IsolatedAsyncioTestCase):
    now = datetime(2026, 10, 17, 18, tzinfo=TZ_KYIV)

    async def asyncSetUp(self):
        self.feature, _, _ = fixtures.StatusMessageFeatureTest()._build_feature(
            edit_result="ok"
        )
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.feature.templates = TemplateEngine(Path(self.directory.name))
        self.snapshot = {
            "state": "GPV3.1",
            "attributes": {"schedule_groups": ["GPV3.1"]},
        }
        self.feature.ha.states[self.feature.entity("schedule_group")] = self.snapshot
        self.events = [
            {
                "start": "2026-10-17T19:00:00+03:00",
                "end": "2026-10-17T22:00:00+03:00",
                "description": "Planned; group=GPV3.1",
            }
        ]

        async def calendar(**kwargs):
            return {
                "success": True,
                "result": {"response": {kwargs["entity_id"]: {"events": self.events}}},
            }

        self.feature.ha.call_service = AsyncMock(side_effect=calendar)

    async def render(self, power="on"):
        with patch("features.status_message.now_kyiv", return_value=self.now):
            return await self.feature._build_status_text(power)

    async def test_single_group_is_only_in_header_and_interval_has_own_line(self):
        for power in ["on", "partial"]:
            text = await self.render(power)
            self.assertNotIn("[Template error", text)
            self.assertEqual(text.count("GPV3.1"), 1)
            self.assertIn("📅 Найближче планове:\n17.10 — 19:00–22:00\n🕒", text)

    async def test_multiple_groups_keep_interval_provenance(self):
        self.snapshot["attributes"]["schedule_groups"].append("GPV5.1")
        text = await self.render()
        self.assertIn("Група GPV3.1, GPV5.1\n", text)
        self.assertIn("📅 Найближче планове:\n🔵 GPV3.1\n17.10 — 19:00–22:00", text)

    async def test_selected_group_is_not_repeated(self):
        self.snapshot["attributes"]["schedule_groups"].append("GPV5.1")
        self.feature.config.selected_group = "GPV3.1"
        text = await self.render()
        self.assertEqual(text.count("GPV3.1"), 1)
        self.assertNotIn("GPV5.1", text)

    async def test_hidden_variants_keep_labels_without_leaking_ids(self):
        self.snapshot["attributes"]["group_labels_visible"] = False
        text = await self.render()
        self.assertEqual(text.count("Варіант 1"), 1)
        self.assertNotIn("GPV", text)
        self.snapshot["attributes"]["schedule_groups"].append("GPV5.1")
        text = await self.render()
        self.assertIn("📅 Найближче планове:\n🔵 Варіант 1\n", text)
        self.assertNotIn("GPV", text)

    async def test_empty_and_unavailable_calendar_keep_separate_status_lines(self):
        self.events = []
        text = await self.render()
        self.assertNotIn("Найближче планове", text)
        self.assertIn("\n🕒", text)
        self.feature.ha.call_service.side_effect = ValueError("unavailable")
        text = await self.render()
        self.assertIn("📅 Найближче планове:\nГрафік тимчасово недоступний\n🕒", text)

    def test_equivalent_utc_event_dates_preserve_all_matching_group_labels(self):
        self.events += [
            {
                "start": "2026-10-17T16:00:00Z",
                "end": "2026-10-17T19:00:00Z",
                "description": "Planned; group=GPV5.1",
            }
        ]
        text = format_next_outage(
            self.events, header_group="GPV3.1, GPV5.1", now=self.now
        )
        self.assertEqual(text, "🔵 GPV3.1, GPV5.1\n17.10 — 19:00–22:00")

    def test_next_day_end_is_explicit(self):
        self.events[0]["end"] = "2026-10-18T01:00:00+03:00"
        self.assertEqual(
            format_next_outage(self.events, header_group="GPV3.1", now=self.now),
            "17.10 — 19:00–18.10 01:00",
        )

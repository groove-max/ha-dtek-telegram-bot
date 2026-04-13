import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1] / "rootfs" / "app"
sys.path.insert(0, str(APP_ROOT))

import outage_calendar
from utils import TZ_KYIV


class OutageCalendarTest(unittest.TestCase):
    def test_build_schedule_lines_merges_overlaps_and_caps_midnight(self) -> None:
        fixed_now = datetime(2026, 3, 9, 10, 0, tzinfo=TZ_KYIV)
        events = [
            {
                "start": "2026-03-09T09:00:00+02:00",
                "end": "2026-03-09T11:00:00+02:00",
            },
            {
                "start": "2026-03-09T10:30:00+02:00",
                "end": "2026-03-09T12:00:00+02:00",
            },
            {
                "start": "2026-03-10T20:00:00+02:00",
                "end": "2026-03-11T00:30:00+02:00",
            },
        ]

        with patch.object(outage_calendar, "now_kyiv", return_value=fixed_now):
            lines = outage_calendar.build_schedule_lines(events)

        self.assertEqual(
            lines,
            [
                "📅 09.03 — 09:00–12:00",
                "📅 10.03 — 20:00–00:00",
            ],
        )

    def test_extract_calendar_events_can_skip_emergency(self) -> None:
        result = {
            "result": {
                "response": {
                    "calendar.test": {
                        "events": [
                            {"start": "2026-03-10T10:00:00+02:00", "description": "normal"},
                            {"start": "2026-03-09T10:00:00+02:00", "description": "emergency"},
                        ]
                    }
                }
            }
        }

        events = outage_calendar.extract_calendar_events(
            result,
            "calendar.test",
            exclude_emergency=True,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["description"], "normal")

    def test_next_planned_outage_returns_first_future_event(self) -> None:
        now = datetime(2026, 3, 9, 12, 0, tzinfo=TZ_KYIV)
        events = [
            {
                "start": "2026-03-09T11:00:00+02:00",
                "end": "2026-03-09T12:30:00+02:00",
            },
            {
                "start": "2026-03-09T13:00:00+02:00",
                "end": "2026-03-09T15:00:00+02:00",
            },
        ]

        upcoming = outage_calendar.next_planned_outage(events, now=now)

        self.assertIsNotNone(upcoming)
        start_dt, end_dt = upcoming
        self.assertEqual(start_dt.hour, 13)
        self.assertEqual(end_dt.hour, 15)


if __name__ == "__main__":
    unittest.main()

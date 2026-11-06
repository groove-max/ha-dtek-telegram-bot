"""Full-day pictures keep exact ranges, true time and readable short outages."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import patch
import unittest

from PIL import Image, ImageColor, ImageDraw
from test_grid_members import large_grid
from schedule_image import (
    BAR_HEIGHT,
    COLORS,
    GRID_TOP,
    GRID_LEFT,
    GRID_RIGHT,
    PAGE_WIDTH,
    FULL_WIDTH,
    KYIV,
    NOW_COLOR,
    PAST_FADE,
    current_minute,
    interval_summaries,
    render_grid,
    time_label,
)
from schedule_pages import render_page, select_page


def dated_grid(date="2026-11-05"):
    data = large_grid(1)
    day = data["days"][0]
    day["date"] = date
    start = datetime.fromisoformat(date).replace(tzinfo=KYIV)
    stop = (start + timedelta(days=1)).astimezone(timezone.utc)
    cursor = start.astimezone(timezone.utc)
    slots = []
    while cursor < stop:
        local = cursor.astimezone(KYIV)
        slots.append(
            {
                "label": local.strftime("%H:%M"),
                "offset": local.strftime("%z"),
                "start": cursor.isoformat(),
                "end": (cursor + timedelta(minutes=30)).isoformat(),
            }
        )
        cursor += timedelta(minutes=30)
    day["slots"] = slots
    day["rows"][0].update(home=True, selected=True, cells=["outage"] * len(slots))
    return data


class DayImageTests(unittest.TestCase):
    def test_landscape_table_is_uninterrupted_and_ranges_are_below_it(self):
        data = dated_grid()
        day = data["days"][0]
        day["rows"] = [
            dict(deepcopy(day["rows"][0]), id=f"GPV{i+1}.1") for i in range(12)
        ]
        original = ImageDraw.ImageDraw.text
        ranges = []

        def record(draw, position, value, *args, **kwargs):
            if "Відключення:" in value:
                ranges.append(position)
            self.assertNotIn("Блідіша", value)
            return original(draw, position, value, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record), patch(
            "schedule_image.paint_timeline",
            wraps=__import__("schedule_image").paint_timeline,
        ) as paint:
            png = render_page(
                select_page(data), now=datetime(2026, 11, 5, 12, tzinfo=KYIV)
            )
        self.assertEqual(len(ranges), 12)
        self.assertTrue(all(y > GRID_TOP + BAR_HEIGHT * 12 for x, y in ranges))
        self.assertEqual(
            [c.args[4] for c in paint.call_args_list],
            [GRID_TOP + i * BAR_HEIGHT for i in range(12)],
        )
        with Image.open(BytesIO(png)) as image:
            self.assertGreater(image.width, image.height)

    def test_general_images_exclude_address_and_all_personal_highlights(self):
        data = dated_grid()
        data["address"] = "PRIVATE EXAMPLE ADDRESS"
        original = deepcopy(data)
        unassigned = deepcopy(data)
        unassigned["days"][0]["rows"][0].update(home=False, selected=False)
        now = datetime(2026, 11, 5, 12, tzinfo=KYIV)
        text = ImageDraw.ImageDraw.text
        labels = []

        def record(draw, position, value, *args, **kwargs):
            labels.append(value)
            return text(draw, position, value, *args, **kwargs)

        for render in (
            lambda d: render_page(
                select_page(d, personal=True, scope="general"), now=now
            ),
            lambda d: render_grid(d, personal=True, general=True, now=now),
        ):
            with patch.object(ImageDraw.ImageDraw, "text", record):
                png = render(data)
            self.assertEqual(png, render(unassigned))
        self.assertNotIn(data["address"], labels)
        self.assertFalse(
            any("Виділено" in label or "Вашу групу" in label for label in labels)
        )
        self.assertEqual(data, original)

    def test_long_friendly_label_never_hides_group_id(self):
        data = dated_grid()
        data["days"][0]["rows"][0].update(id="GPV29.1", label="Черга відключень 29.1")
        original = ImageDraw.ImageDraw.text
        labels = []

        def record(draw, position, value, *args, **kwargs):
            if position == (30, GRID_TOP + 5):
                labels.append(value)
            return original(draw, position, value, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record):
            render_page(select_page(data))
        self.assertEqual(labels, ["GPV29.1"])

    def test_full_day_has_one_band_and_all_exact_time_ranges(self):
        data = dated_grid()
        row = data["days"][0]["rows"][0]
        row["segments"] = [
            dict(start=0, end=735, kind="clear"),
            dict(start=735, end=750, kind="outage"),
            dict(start=750, end=1297, kind="clear"),
            dict(start=1297, end=1440, kind="outage"),
        ]
        self.assertEqual(
            interval_summaries(data["days"][0], row),
            [("outage", "Відключення:", ["12:15–12:30", "21:37–24:00"])],
        )
        original = deepcopy(data)
        with patch(
            "schedule_image.paint_timeline",
            wraps=__import__("schedule_image").paint_timeline,
        ) as paint:
            png = render_page(
                select_page(data), now=datetime(2026, 11, 5, 12, tzinfo=KYIV)
            )
            self.assertEqual(paint.call_count, 1)
            self.assertNotIn("start", paint.call_args.kwargs)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(original, data)

    def test_both_images_fade_only_past_bands_and_mark_same_instant(self):
        data = dated_grid()
        now = datetime(2026, 11, 5, 12, tzinfo=KYIV)
        for png, left, width in (
            (
                render_grid(data, now=now),
                GRID_LEFT,
                FULL_WIDTH - GRID_LEFT - GRID_RIGHT,
            ),
            (
                render_page(select_page(data), now=now),
                GRID_LEFT,
                PAGE_WIDTH - GRID_LEFT - GRID_RIGHT,
            ),
        ):
            with Image.open(BytesIO(png)) as image:
                y = GRID_TOP + BAR_HEIGHT // 2
                before = image.getpixel((round(left + 220 / 1440 * width), y))
                after = image.getpixel((round(left + 1000 / 1440 * width), y))
                self.assertEqual(after, ImageColor.getrgb(COLORS["outage"]))
                self.assertEqual(
                    before, tuple(int(c + (255 - c) * PAST_FADE) for c in after)
                )
                self.assertEqual(
                    image.getpixel((round(left + width / 2), y)),
                    ImageColor.getrgb(NOW_COLOR),
                )

    def test_tomorrow_and_other_dates_have_neither_current_line_nor_fade(self):
        data = dated_grid()
        for now in (
            datetime(2026, 11, 4, 23, 59, tzinfo=KYIV),
            datetime(2026, 11, 6, tzinfo=KYIV),
        ):
            self.assertIsNone(current_minute(data["days"][0], now))
            with Image.open(BytesIO(render_page(select_page(data), now=now))) as image:
                self.assertEqual(
                    image.getpixel((GRID_LEFT + 35, GRID_TOP + 15)),
                    ImageColor.getrgb(COLORS["outage"]),
                )

    def test_current_line_uses_kyiv_date_and_preserves_repeated_dst_hour(self):
        day = dated_grid()["days"][0]
        self.assertEqual(
            current_minute(day, datetime(2026, 11, 4, 22, 30, tzinfo=timezone.utc)), 30
        )
        self.assertEqual(
            current_minute(day, datetime(2026, 11, 5, 23, 59, tzinfo=KYIV)), 1439
        )
        day = dated_grid("2026-10-25")["days"][0]
        self.assertEqual(len(day["slots"]), 50)
        first = datetime(2026, 10, 25, 3, 30, tzinfo=KYIV, fold=0)
        second = first.replace(fold=1)
        self.assertEqual(current_minute(day, second) - current_minute(day, first), 60)
        self.assertEqual(time_label(day, 210), "03:30 UTC+03:00")
        self.assertEqual(time_label(day, 270), "03:30 UTC+02:00")
        self.assertEqual(time_label(day, 1500), "24:00")
        spring = dated_grid("2026-03-29")["days"][0]
        self.assertEqual(len(spring["slots"]), 46)
        self.assertEqual(time_label(spring, 180), "04:00")
        self.assertEqual(
            current_minute(spring, datetime(2026, 3, 29, 4, tzinfo=KYIV)), 180
        )

    def test_possible_forecast_unknown_never_become_confirmed_outage_text(self):
        day = dated_grid()["days"][0]
        row = day["rows"][0]
        row["segments"] = [
            dict(start=0, end=30, kind="outage"),
            dict(start=30, end=60, kind="possible"),
            dict(start=60, end=90, kind="forecast"),
            dict(start=90, end=1440, kind="unknown"),
        ]
        self.assertEqual(
            interval_summaries(day, row),
            [
                ("outage", "Відключення:", ["00:00–00:30"]),
                ("possible", "Можливі:", ["00:30–01:00"]),
                ("forecast", "Прогноз:", ["01:00–01:30"]),
                ("unknown", "Немає даних:", ["01:30–24:00"]),
            ],
        )
        row["segments"] = [dict(start=0, end=1440, kind="forecast_clear")]
        self.assertEqual(
            interval_summaries(day, row),
            [("forecast_clear", "Прогноз: без відключень", [])],
        )

    def test_dense_intervals_wrap_inside_image_instead_of_overlapping(self):
        data = dated_grid()
        row = data["days"][0]["rows"][0]
        row["segments"] = [
            dict(start=i, end=i + 15, kind="outage" if i % 30 else "clear")
            for i in range(0, 1440, 15)
        ]
        original = ImageDraw.ImageDraw.text
        measured = []

        def text(draw, position, value, *args, **kwargs):
            if position[0] == GRID_LEFT and position[1] > GRID_TOP + BAR_HEIGHT:
                measured.append(
                    (
                        position,
                        value,
                        kwargs["font"],
                        draw.textlength(value, font=kwargs["font"]),
                    )
                )
            return original(draw, position, value, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", text):
            png = render_page(
                select_page(data), now=datetime(2026, 11, 5, 12, tzinfo=KYIV)
            )
        self.assertGreater(len(measured), 1)
        with Image.open(BytesIO(png)) as image:
            for (x, y), _, font, width in measured:
                self.assertLessEqual(x + width, image.width - 20)
                self.assertLess(y + font.size, image.height)


if __name__ == "__main__":
    unittest.main()

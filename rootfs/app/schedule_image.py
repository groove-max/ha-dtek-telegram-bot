"""Render the integration's canonical grid; never infer outages from colours."""

from io import BytesIO
from pathlib import Path
from datetime import datetime
import math
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

KYIV = ZoneInfo("Europe/Kyiv")
NOW_COLOR = "#d73a59"
PAST_FADE = 0.48
PAGE_WIDTH = 1600
FULL_WIDTH = 1920
GRID_LEFT = 156
GRID_RIGHT = 28
GRID_TOP = 210
BAR_HEIGHT = 34

COLORS = {
    "outage": "#e9a63b",
    "possible": "#f6dca9",
    "clear": "#edf4f4",
    "forecast": "#b9b9d2",
    "forecast_clear": "#f0f0f6",
    "unknown": "#d4d8dc",
}
LABELS = {
    "outage": "Відключення",
    "possible": "Можливе відключення",
    "clear": "Без планових відключень",
    "forecast": "Прогноз: відключення",
    "forecast_clear": "Прогноз: без відключень",
    "unknown": "Немає даних",
}


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for root in (
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/ttf-dejavu",
        "/usr/share/fonts/dejavu",
    ):
        path = Path(root) / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    # Missing Cyrillic fonts is a build/configuration problem, never a blank legend.
    raise ValueError("Cyrillic font unavailable")


from utils import format_date, format_datetime

def stamp(value):
    try:
        return format_datetime(datetime.fromisoformat(value))
    except (ValueError, TypeError):
        return "—"


def timeline_segments(day, row):
    """Exact elapsed-minute bands, or the old integration's half-hour cells."""
    duration = len(day["slots"]) * 30
    if "segments" in row:
        parts = row["segments"]
        if not isinstance(parts, list) or not 1 <= len(parts) <= 4096:
            raise ValueError("Invalid timeline segments")
        cursor = 0
        for part in parts:
            if (
                not isinstance(part, dict)
                or any(
                    type(part.get(k)) not in (int, float) or not math.isfinite(part[k])
                    for k in ("start", "end")
                )
                or part["start"] != cursor
                or not cursor < part["end"] <= duration
                or part.get("kind") not in COLORS
            ):
                raise ValueError("Invalid timeline segments")
            cursor = part["end"]
        if cursor != duration:
            raise ValueError("Incomplete timeline segments")
        return parts
    result = []
    for i, kind in enumerate(row["cells"]):
        if result and result[-1]["kind"] == kind:
            result[-1]["end"] = (i + 1) * 30
        else:
            result.append(dict(start=i * 30, end=(i + 1) * 30, kind=kind))
    return result


def paint_timeline(draw, day, row, x, y, width, height, *, start=0, end=None):
    """Paint proportional bands, then hour lines; never expand to a whole cell."""
    end = len(day["slots"]) * 30 if end is None else end
    scale = width / (end - start)
    for part in timeline_segments(day, row):
        left, right = max(start, part["start"]), min(end, part["end"])
        if left >= right:
            continue
        x0, x1 = round(x + (left - start) * scale), round(x + (right - start) * scale)
        if x1 <= x0:
            continue
        draw.rectangle((x0, y, x1 - 1, y + height - 1), fill=COLORS[part["kind"]])
        if part["kind"] not in ("outage", "clear"):
            for shift in range(-height, x1 - x0, 8):
                a, b = max(0, shift), min(x1 - x0 - 1, shift + height - 1)
                if a <= b:
                    draw.line(
                        (
                            x0 + a,
                            y + height - 1 - (a - shift),
                            x0 + b,
                            y + height - 1 - (b - shift),
                        ),
                        fill="#8f9aa8",
                    )
    for minute in range(int(start), int(end) + 1, 60):
        edge = round(x + (minute - start) * scale)
        draw.line((edge, y, edge, y + height), fill="#adbcc3")
    draw.rectangle((x, y, x + width, y + height), outline="#adbcc3")


def time_label(day, minute):
    """Civil clock labels, preserving repeated DST hours and exact minutes."""
    slots = day["slots"]
    if minute == len(slots) * 30:
        return "24:00"
    slot = slots[int(minute // 30)]
    hours, minutes = map(int, slot["label"].split(":"))
    seconds = round((hours * 60 + minutes + minute % 30) * 60)
    label = f"{seconds // 3600:02}:{seconds % 3600 // 60:02}"
    if seconds % 60:
        label += f":{seconds % 60:02}"
    if sum(s["label"][:2] == slot["label"][:2] for s in slots) > 2:
        offset = slot.get("offset", "")
        label += " UTC" + (
            offset[:3] + ":" + offset[3:] if len(offset) == 5 else offset
        )
    return label


def current_minute(day, now):
    """Place the current instant on today's elapsed-time axis, never tomorrow's."""
    local = now.astimezone(KYIV)
    if day["date"] != local.date().isoformat():
        return None
    start = day["slots"][0].get("start")
    start = (
        datetime.fromisoformat(start)
        if start
        else local.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    if start.tzinfo is None:
        raise ValueError("Schedule timestamps require a timezone")
    minute = (now.timestamp() - start.timestamp()) / 60
    return minute if 0 <= minute < len(day["slots"]) * 30 else None


def interval_summaries(day, row):
    """Text ranges also cover narrow bands and distinguish forecasts/unknowns."""
    parts = timeline_segments(day, row)
    labels = {
        "outage": "Відключення",
        "possible": "Можливі",
        "forecast": "Прогноз",
        "unknown": "Немає даних",
    }
    result = []
    for kind, title in labels.items():
        ranges = [
            f"{time_label(day, p['start'])}–{time_label(day, p['end'])}"
            for p in parts
            if p["kind"] == kind
        ]
        if ranges:
            result.append((kind, title + ":", ranges))
    if not result:
        forecast = any(p["kind"] == "forecast_clear" for p in parts)
        result.append(
            (
                "forecast_clear" if forecast else "clear",
                "Прогноз: без відключень" if forecast else "Без планових відключень",
                [],
            )
        )
    return result


def _text_lines(draw, day, row, width, text_font):
    result = []
    for kind, title, ranges in interval_summaries(day, row):
        line = title
        for label in ranges:
            candidate = line + (" " if line == title else " · ") + label
            if draw.textlength(candidate, font=text_font) > width:
                result.append((kind, line))
                line = label
            else:
                line = candidate
        result.append((kind, line))
    return result


def _fit_text(draw, value, width, text_font):
    value = str(value)
    if draw.textlength(value, font=text_font) <= width:
        return value
    while value and draw.textlength(value + "…", font=text_font) > width:
        value = value[:-1]
    return value + "…"


def render_schedule(
    data,
    day,
    rows,
    *,
    personal=False,
    scope="Усі групи",
    page=None,
    now=None,
    width=PAGE_WIDTH,
    general=False,
):
    """An uninterrupted day table, followed by a separate per-group range key."""
    from provider_notice import image_notice
    now = now or datetime.now(KYIV)
    notice = image_notice(day, now=now)
    grid_top = GRID_TOP + (48 if notice else 0)
    marker = current_minute(day, now)
    left, right = GRID_LEFT, GRID_RIGHT
    plot_width = width - left - right
    duration = len(day["slots"]) * 30
    text_font = font(22)
    line_height = 29
    column_width = (width - 64) // 2
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    summaries = [
        _text_lines(measure, day, row, column_width - 136, text_font) for row in rows
    ]
    # Keep the compact table whole. Range descriptions form a two-column key
    # below it; a long/fragmented day's text never splits a timeline row.
    detail_heights = [
        18 + line_height * max(len(lines) for lines in summaries[i : i + 2])
        for i in range(0, len(summaries), 2)
    ]
    table_bottom = grid_top + BAR_HEIGHT * len(rows)
    legend_top = table_bottom + 22
    detail_top = legend_top + 104
    footer = detail_top + sum(detail_heights) + 16
    height = footer + 70
    if height > 20000:
        raise ValueError("Schedule image is too large")
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title = (
        f"ДТЕК · {format_date(day['date'])} · {scope}"
    )
    draw.text(
        (24, 16),
        _fit_text(draw, title, width - 48, font(32, True)),
        font=font(32, True),
        fill="#243442",
    )
    subtitle = "Усі групи оператора" if general else data.get("address", "")
    draw.text(
        (24, 58),
        _fit_text(draw, subtitle, width - 48, font(22)),
        font=font(22),
        fill="#526273",
    )
    draw.text(
        (24, 91),
        f"ДТЕК: {stamp(data.get('source_updated_at'))} · Перевірено: {stamp(data.get('verified_at'))}",
        font=font(19),
        fill="#526273",
    )

    if notice:
        draw.text((24, 151), _fit_text(draw, notice, width - 48, font(20, True)), font=font(20, True), fill="#9b4b00")

    def highlighted(row):
        return not general and bool(
            row.get("selected") if personal else row.get("home")
        )

    if general:
        note = "Загальний графік"
    elif not any(highlighted(row) for row in day["rows"]):
        note = "Вашу групу ще не підтверджено"
    elif any(highlighted(row) for row in rows):
        note = "Виділено вашу групу" if personal else "Виділено групи будинку"
    else:
        note = "Ваші групи — на іншій сторінці"
    if page is not None:
        note += f" · {page[0] + 1} / {page[1]}"
    draw.text((24, 120), note, font=font(19), fill="#006b8d")
    draw.text((30, grid_top - 25), "Група", font=font(19), fill="#526273")
    for i, slot in enumerate(day["slots"]):
        if not slot["label"].endswith(":00"):
            continue
        x = left + i * 30 / duration * plot_width
        draw.text(
            (x, grid_top - 25),
            slot["label"][:2],
            font=font(19),
            fill="#526273",
            anchor="mt",
        )
        if sum(s["label"] == slot["label"] for s in day["slots"]) > 1:
            draw.text(
                (x, grid_top - 7),
                slot.get("offset", "")[:3],
                font=font(9),
                fill="#526273",
                anchor="mt",
            )
    draw.text(
        (width - right, grid_top - 25), "24", font=font(19), fill="#526273", anchor="mt"
    )

    for i, row in enumerate(rows):
        y = grid_top + i * BAR_HEIGHT
        highlight = highlighted(row)
        draw.rectangle(
            (24, y, left, y + BAR_HEIGHT), fill="#e7f5fb" if highlight else "#f5f8fa"
        )
        size = 22
        while (
            size > 12
            and draw.textlength(str(row["id"]), font=font(size, highlight)) > left - 40
        ):
            size -= 1
        label_font = font(size, highlight)
        draw.text(
            (30, y + 5),
            _fit_text(draw, row["id"], left - 40, label_font),
            font=label_font,
            fill="#006b8d" if highlight else "#243442",
        )
        paint_timeline(draw, day, row, left, y, plot_width, BAR_HEIGHT)
        draw.line((24, y, left, y), fill="#d4dfe4")

    cursor_x = (
        round(left + marker / duration * plot_width) if marker is not None else None
    )
    if cursor_x is not None:
        if cursor_x > left:
            box = (left, grid_top, cursor_x, table_bottom + 1)
            image.paste(
                Image.blend(
                    image.crop(box),
                    Image.new(
                        "RGB", (cursor_x - left, table_bottom + 1 - grid_top), "white"
                    ),
                    PAST_FADE,
                ),
                box,
            )
        label = "Зараз " + now.astimezone(KYIV).strftime("%H:%M")
        label_width = draw.textlength(label, font=font(19, True)) + 18
        # A dedicated line keeps the badge clear of the page note and hour labels.
        label_x = max(
            left, min(cursor_x - label_width / 2, width - right - label_width)
        )
        draw.rounded_rectangle(
            (label_x, 150, label_x + label_width, 178), radius=6, fill=NOW_COLOR
        )
        draw.text((label_x + 9, 153), label, font=font(19, True), fill="white")
        draw.line(
            (cursor_x, grid_top - 4, cursor_x, table_bottom), fill=NOW_COLOR, width=2
        )
    for i, row in enumerate(rows):
        if highlighted(row):
            y = grid_top + i * BAR_HEIGHT
            draw.rectangle(
                (24, y, width - right, y + BAR_HEIGHT), outline="#008fb3", width=2
            )
    draw.rectangle((24, grid_top, width - right, table_bottom), outline="#91a8b4")

    for i, (kind, label) in enumerate(LABELS.items()):
        x, y = 24 + (i % 3) * ((width - 48) // 3), legend_top + (i // 3) * 28
        draw.rectangle((x, y + 2, x + 18, y + 20), fill=COLORS[kind], outline="#94a3b8")
        draw.text((x + 28, y), label, font=font(19), fill="#526273")
    draw.text(
        (24, detail_top - 38),
        "Час відключень за групами",
        font=font(24, True),
        fill="#243442",
    )
    y = detail_top
    for start in range(0, len(rows), 2):
        row_height = detail_heights[start // 2]
        for column, (row, lines) in enumerate(
            zip(rows[start : start + 2], summaries[start : start + 2])
        ):
            x = 24 + column * (column_width + 16)
            draw.text(
                (x, y),
                _fit_text(draw, row["id"], 120, font(22, True)),
                font=font(22, True),
                fill="#006b8d" if highlighted(row) else "#243442",
            )
            for n, (kind, line) in enumerate(lines):
                draw.text(
                    (x + 132, y + n * line_height),
                    line,
                    font=text_font,
                    fill="#75501b" if kind == "outage" else "#536371",
                )
        y += row_height
    draw.line((24, footer - 8, width - 24, footer - 8), fill="#d4dfe4")
    draw.text(
        (24, footer),
        "Сформовано: " + format_datetime(now),
        font=font(19),
        fill="#526273",
    )
    draw.text(
        (24, footer + 29),
        "При аварійних відключеннях графік може не діяти.",
        font=font(19),
        fill="#526273",
    )
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def render_grid(data, offset=0, personal=False, *, now=None, general=False):
    if data.get("status") != "ready" or type(offset) is not int or offset not in (0, 1):
        raise ValueError("Schedule grid unavailable")
    days = data.get("days", [])
    if len(days) <= offset:
        raise ValueError("Date unavailable")
    day = days[offset]
    rows = day.get("rows", [])
    slots = day.get("slots", [])
    if (
        not 1 <= len(rows) <= 100
        or not 1 <= len(slots) <= 50
        or any(
            len(row.get("cells", [])) != len(slots)
            or any(c not in COLORS for c in row["cells"])
            for row in rows
        )
    ):
        raise ValueError("Invalid grid size or cells")
    if all(p["kind"] == "unknown" for row in rows for p in timeline_segments(day, row)):
        raise ValueError("No published schedule for date")
    return render_schedule(
        data,
        day,
        rows,
        personal=personal,
        now=now,
        width=FULL_WIDTH,
        general=general,
        scope="Усі групи · без адреси" if general else "Усі групи",
    )

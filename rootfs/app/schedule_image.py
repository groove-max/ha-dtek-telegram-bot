"""Render the integration's canonical grid; never infer outages from colours."""

from io import BytesIO
from pathlib import Path
from datetime import datetime
import math
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

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


def stamp(value):
    try:
        return (
            datetime.fromisoformat(value)
            .astimezone(ZoneInfo("Europe/Kyiv"))
            .strftime("%d.%m %H:%M")
        )
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
            if not isinstance(part, dict) or any(
                type(part.get(k)) not in (int, float) or not math.isfinite(part[k])
                for k in ("start", "end")
            ) or part["start"] != cursor or not cursor < part["end"] <= duration or part.get("kind") not in COLORS:
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
                    draw.line((x0+a, y+height-1-(a-shift), x0+b, y+height-1-(b-shift)), fill="#8f9aa8")
    for minute in range(int(start), int(end) + 1, 60):
        edge = round(x + (minute - start) * scale)
        draw.line((edge, y, edge, y + height), fill="#adbcc3")
    draw.rectangle((x, y, x + width, y + height), outline="#adbcc3")


def render_grid(data, offset=0, personal=False):
    if data.get("status") != "ready" or type(offset) is not int or offset not in (0, 1):
        raise ValueError("Schedule grid unavailable")
    days = data.get("days", [])
    if len(days) <= offset:
        raise ValueError("Date unavailable")
    day = days[offset]
    rows = day.get("rows", [])
    slots = day.get("slots", [])
    if not 1 <= len(rows) <= 100 or not 1 <= len(slots) <= 50:
        raise ValueError("Invalid grid size")
    if any(
        len(row.get("cells", [])) != len(slots)
        or any(c not in COLORS for c in row["cells"])
        for row in rows
    ):
        raise ValueError("Invalid schedule cells")
    if all(p["kind"] == "unknown" for row in rows for p in timeline_segments(day, row)):
        raise ValueError("No published schedule for date")
    cell = 28
    left = 210
    top = 160
    rh = 42
    width = left + cell * len(slots) + 24
    height = top + rh * len(rows) + 190
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    normal = font(17)
    small = font(13)
    title = font(25, True)
    draw.text((20, 18), f"ДТЕК · Усі групи · {day['date']}", font=title, fill="#243442")
    # The shared table includes the building, never an apartment/account number.
    draw.text((20, 55), str(data.get("address", ""))[:110], font=normal, fill="#243442")
    draw.text(
        (20, 86),
        f"Оновлено ДТЕК: {stamp(data.get('source_updated_at'))} · Перевірено: {stamp(data.get('verified_at'))}",
        font=small,
        fill="#334155",
    )
    has_selection = any(row.get("selected") for row in rows)
    draw.text(
        (20, 111),
        (
            "Час: Europe/Kyiv · Виділено вашу групу"
            if has_selection
            else "Час: Europe/Kyiv · Ваша група зараз недоступна"
        )
        if personal
        else "Час: Europe/Kyiv · Виділено групи будинку",
        font=small,
        fill="#334155",
    )
    repeated = {
        s["label"] for s in slots if sum(t["label"] == s["label"] for t in slots) > 1
    }
    for i, slot in enumerate(slots):
        if i % 2 == 0 or slot["label"] in repeated:
            draw.text(
                (left + i * cell, top - 22), slot["label"], font=font(9), fill="#334155"
            )
            if slot["label"] in repeated:
                # UTC offsets on a second line distinguish repeated hours without overlap.
                draw.text(
                    (left + i * cell, top - 12),
                    slot.get("offset", ""),
                    font=font(8),
                    fill="#334155",
                )
    for r, row in enumerate(rows):
        y = top + r * rh
        highlight = bool(row.get("selected") if personal else row.get("home"))
        draw.rectangle(
            (20, y, left - 1, y + rh), fill="#dff3fb" if highlight else "#f5f7f8"
        )
        draw.text(
            (30, y + 11),
            str(row.get("label", row["id"]))[:22],
            font=font(17, highlight),
            fill="#006b8d" if highlight else "#243442",
        )
        paint_timeline(draw, day, row, left, y, cell * len(slots), rh)
        if highlight:
            draw.rectangle(
                (20, y, left + len(slots) * cell, y + rh), outline="#008fb3", width=3
            )
    y = top + len(rows) * rh + 20
    for i, (kind, label) in enumerate(LABELS.items()):
        x = 20 + (i % 3) * (width // 3)
        line = y + (i // 3) * 34
        draw.rectangle(
            (x, line, x + 22, line + 20), fill=COLORS[kind], outline="#94a3b8"
        )
        draw.text((x + 30, line), label, font=small, fill="#243442")
    draw.text(
        (20, y + 82),
        "Плановий графік. Аварійні відключення можуть відрізнятися.",
        font=normal,
        fill="#334155",
    )
    draw.text(
        (20, y + 112),
        "Прогноз та відсутність даних не є підтвердженим денним графіком.",
        font=small,
        fill="#334155",
    )
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()

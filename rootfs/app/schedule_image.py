"""Render the integration's canonical grid; never infer outages from colours."""

from io import BytesIO
from pathlib import Path
from datetime import datetime
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
    if all(c == "unknown" for row in rows for c in row["cells"]):
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
        for c, value in enumerate(row["cells"]):
            x = left + c * cell
            draw.rectangle(
                (x, y, x + cell, y + rh), fill=COLORS[value], outline="#adbcc3"
            )
            if value in ("possible", "forecast", "forecast_clear", "unknown"):
                for shift in range(0, rh, 8):
                    draw.line(
                        (x, y + shift, x + cell, y + max(0, shift - 12)),
                        fill="#8f9aa8",
                        width=1,
                    )
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

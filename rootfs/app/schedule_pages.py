"""Readable private-chat pages over the unchanged canonical operator grid."""

from dataclasses import dataclass
from io import BytesIO
from math import ceil

from PIL import Image, ImageDraw

from schedule_image import COLORS, LABELS, font, stamp, timeline_segments, paint_timeline

PAGE_SIZE = 12
SCOPES = {"mine": "Моя група", "home": "Мій будинок", "all": "Усі групи"}


@dataclass
class GridPage:
    data: dict
    day: dict
    rows: list
    scope: str
    page: int
    pages: int
    personal: bool
    unassigned: bool


def select_page(data, offset=0, personal=False, scope="auto", page=0):
    if data.get("status") != "ready" or type(offset) is not int or offset not in (0, 1):
        raise ValueError("Schedule unavailable")
    if scope not in {"auto", *SCOPES} or type(page) is not int or page < 0:
        raise ValueError("Invalid grid view")
    days = data.get("days", [])
    if offset >= len(days):
        raise ValueError("Date unavailable")
    day = days[offset]
    rows, slots = day.get("rows", []), day.get("slots", [])
    if not 1 <= len(rows) <= 100 or not 1 <= len(slots) <= 50 or any(
        len(row.get("cells", [])) != len(slots)
        or any(cell not in COLORS for cell in row["cells"]) for row in rows
    ):
        raise ValueError("Invalid grid")
    for row in rows:
        timeline_segments(day, row)
    selected, home = any(r.get("selected") for r in rows), any(r.get("home") for r in rows)
    unassigned = not (selected if personal else home)
    if scope == "auto":
        scope = "mine" if personal and selected else "home" if home else "all"
    filtered = [r for r in rows if scope == "all" or r.get("selected" if scope == "mine" else "home")]
    pages = max(1, ceil(len(filtered) / PAGE_SIZE))
    page = min(page, pages - 1)
    return GridPage(data, day, filtered[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], scope, page, pages, personal, unassigned)


def render_page(view):
    """Two half-day panels with exact bands, hourly marks and DST-aware slots."""
    if not view.rows:
        raise ValueError("No groups confirmed for this view")
    slots = view.day["slots"]
    # A civil-day split includes all 46/48/50 slots, including repeated hours.
    panels = [[i for i, slot in enumerate(slots) if (int(slot["label"][:2]) < 12) == morning] for morning in (True, False)]
    panels = [indices for indices in panels if indices]
    width, left, right, rh = 840, 150, 20, 38
    top = 180
    panel_height = 56 + len(view.rows) * rh
    height = top + len(panels) * panel_height + 225
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 16), f"ДТЕК · {view.day['date']} · {SCOPES[view.scope]}", font=font(26, True), fill="#243442")
    draw.text((20, 53), f"Сторінка {view.page + 1} із {view.pages} · Europe/Kyiv · годинна шкала", font=font(20), fill="#334155")
    draw.text((20, 83), f"Перевірено: {stamp(view.data.get('verified_at'))}", font=font(19), fill="#334155")
    highlighted = any(row.get("selected") if view.personal else row.get("home") for row in view.rows)
    note = (
        "Вашу групу ще не підтверджено" if view.unassigned
        else ("Виділено вашу групу" if view.personal else "Виділено групи будинку") if highlighted
        else "Ваша група — на іншій сторінці" if view.personal
        else "Групи будинку — на іншій сторінці"
    )
    draw.text((20, 112), note, font=font(20), fill="#006b8d")
    repeated = {s["label"] for s in slots if sum(t["label"] == s["label"] for t in slots) > 1}
    for panel, indices in enumerate(panels):
        y0 = top + panel * panel_height
        cell = (width - left - right) / len(indices)
        for c, index in enumerate(indices):
            slot = slots[index]
            x = left + c * cell
            if slot["label"].endswith(":00"):
                draw.text((x, y0 - 46), slot["label"][:2], font=font(19), fill="#334155")
                if slot["label"] in repeated:
                    draw.text((x, y0 - 22), slot.get("offset", ""), font=font(10), fill="#334155")
        for r, row in enumerate(view.rows):
            y = y0 + r * rh
            highlight = bool(row.get("selected") if view.personal else row.get("home"))
            draw.rectangle((20, y, left, y + rh), fill="#dff3fb" if highlight else "#f5f7f8")
            label = str(row.get("label", row["id"]))
            size = 21
            while size > 12 and draw.textlength(label, font=font(size, highlight)) > left - 34:
                size -= 1
            draw.text((26, y + 6), label, font=font(size, highlight), fill="#006b8d" if highlight else "#243442")
            paint_timeline(draw, view.day, row, left, y, width-left-right, rh,
                           start=indices[0]*30, end=(indices[-1]+1)*30)
            if highlight:
                draw.rectangle((20, y, width - right, y + rh), outline="#008fb3", width=3)
    y = top + len(panels) * panel_height
    for i, (kind, label) in enumerate(LABELS.items()):
        x, line = 20 + (i % 2) * 410, y + (i // 2) * 32
        draw.rectangle((x, line, x + 20, line + 20), fill=COLORS[kind], outline="#94a3b8")
        draw.text((x + 28, line), label, font=font(18), fill="#243442")
    draw.text((20, y + 110), "Плановий графік. Аварійні відключення можуть відрізнятися.", font=font(18), fill="#334155")
    draw.text((20, y + 141), "Сірі клітинки — немає даних; фіолетові — тижневий прогноз.", font=font(18), fill="#334155")
    draw.text((20, y + 172), "Оновлено ДТЕК: " + stamp(view.data.get("source_updated_at")), font=font(17), fill="#334155")
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()

"""Readable private-chat pages over the unchanged canonical operator grid."""

from dataclasses import dataclass
from math import ceil

from schedule_image import COLORS, timeline_segments, render_schedule

PAGE_SIZE = 12
SCOPES = {
    "mine": "Моя група",
    "home": "Мій будинок",
    "all": "Усі групи",
    "general": "Усі групи · без адреси",
}
SCOPE_BUTTONS = {
    "mine": "👤 Моя група",
    "home": "🏠 Мій будинок",
    "all": "📊 Усі групи",
}


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
    if (
        not 1 <= len(rows) <= 100
        or not 1 <= len(slots) <= 50
        or any(
            len(row.get("cells", [])) != len(slots)
            or any(cell not in COLORS for cell in row["cells"])
            for row in rows
        )
    ):
        raise ValueError("Invalid grid")
    for row in rows:
        timeline_segments(day, row)
    selected, home = any(r.get("selected") for r in rows), any(
        r.get("home") for r in rows
    )
    unassigned = scope != "general" and not (selected if personal else home)
    if scope == "auto":
        scope = "mine" if personal and selected else "home" if home else "all"
    filtered = [
        r
        for r in rows
        if scope in ("all", "general")
        or r.get("selected" if scope == "mine" else "home")
    ]
    pages = max(1, ceil(len(filtered) / PAGE_SIZE))
    page = min(page, pages - 1)
    return GridPage(
        data,
        day,
        filtered[page * PAGE_SIZE : (page + 1) * PAGE_SIZE],
        scope,
        page,
        pages,
        personal,
        unassigned,
    )


def render_page(view, *, now=None):
    """Keep every group's full day on one line, with explicit interval labels."""
    if not view.rows:
        raise ValueError("No groups confirmed for this view")
    return render_schedule(
        view.data,
        view.day,
        view.rows,
        personal=view.personal,
        scope=SCOPES[view.scope],
        page=(view.page, view.pages),
        now=now,
        general=view.scope == "general",
    )

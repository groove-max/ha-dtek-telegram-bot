"""Utility functions for DTEK Telegram Bot."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

TZ_KYIV = ZoneInfo("Europe/Kyiv")

OUTAGE_TYPE_UA: dict[str, str] = {
    "emergency": "Екстрене відключення",
    "stabilization": "Стабілізаційне відключення",
    "planned": "Планове відключення",
    "ok": "Норма",
}

OUTAGE_SEVERITY: dict[str, int] = {
    "emergency": 3,
    "stabilization": 2,
    "planned": 1,
    "ok": 0,
}

_CONDITION_RE = re.compile(r"^([<>]=?|==)?\s*(.+)$")


def format_duration(seconds: int | float) -> str:
    """Format duration in seconds to Ukrainian string with months/days/hours/minutes."""
    total = int(seconds)
    if total < 0:
        total = 0

    total_minutes = total // 60
    month_minutes = 30 * 24 * 60
    day_minutes = 24 * 60

    months, remainder = divmod(total_minutes, month_minutes)
    days, remainder = divmod(remainder, day_minutes)
    hours, minutes = divmod(remainder, 60)

    parts: list[str] = []
    if months:
        parts.append(f"{months} міс")
    if days:
        parts.append(f"{days} дн")
    if hours:
        parts.append(f"{hours} год")
    if minutes or not parts:
        parts.append(f"{minutes} хв")

    return " ".join(parts)


def format_datetime(dt: datetime | str | None) -> str:
    """Format datetime to 'DD.MM.YYYY HH:MM' in Kyiv timezone."""
    if dt is None:
        return "—"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except (ValueError, TypeError):
            return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_KYIV)
    return dt.astimezone(TZ_KYIV).strftime("%d.%m.%Y %H:%M")


def format_date_short(dt: datetime | str | None) -> str:
    """Format datetime to 'DD.MM' in Kyiv timezone."""
    if dt is None:
        return "—"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except (ValueError, TypeError):
            return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_KYIV)
    return dt.astimezone(TZ_KYIV).strftime("%d.%m")


def format_time_short(dt: datetime | str | None) -> str:
    """Format datetime to 'HH:MM' in Kyiv timezone."""
    if dt is None:
        return "—"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except (ValueError, TypeError):
            return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_KYIV)
    return dt.astimezone(TZ_KYIV).strftime("%H:%M")


def now_kyiv() -> datetime:
    """Get current time in Kyiv timezone."""
    return datetime.now(TZ_KYIV)


def format_phase_summary(phases: list[dict[str, Any]] | None) -> str:
    """Format configured phases into one Telegram-friendly summary line."""
    if not phases:
        return ""

    parts: list[str] = []
    for phase in phases:
        available = phase.get("available")
        if available is True:
            icon = "🟢"
        elif available is False:
            icon = "🔴"
        else:
            icon = "⚪"

        label = str(phase.get("label", "—"))
        voltage = phase.get("voltage")
        if voltage is None:
            value = "—"
        else:
            try:
                value = f"{float(voltage):.1f} В"
            except (TypeError, ValueError):
                value = str(voltage)

        parts.append(f"{icon} {label}: {value}")

    return "🔌 " + "  ".join(parts)


def parse_condition(condition_str: str, value: Any) -> bool:
    """Evaluate a condition string against a value.

    Supported formats:
        "0" or "123.5"   — exact numeric comparison
        "<50" / ">190"   — threshold comparison
        "unavailable"     — string equality (for HA entity states)
    """
    condition_str = condition_str.strip()

    if condition_str.lower() in ("unavailable", "unknown", "none"):
        return str(value).lower() == condition_str.lower()

    match = _CONDITION_RE.match(condition_str)
    if not match:
        return False

    operator, threshold_str = match.group(1), match.group(2)

    try:
        threshold = float(threshold_str.strip())
        val = float(value)
    except (ValueError, TypeError):
        return False

    if operator is None or operator == "==" or operator == "":
        return val == threshold
    if operator == "<":
        return val < threshold
    if operator == ">":
        return val > threshold
    if operator == "<=":
        return val <= threshold
    if operator == ">=":
        return val >= threshold

    return False

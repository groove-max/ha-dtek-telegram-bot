"""Shared helpers for outage calendar event processing."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from utils import TZ_KYIV, format_date_short, format_time_short, now_kyiv


def parse_event_time(value: str | datetime) -> datetime | None:
    """Parse an event time string to datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=TZ_KYIV)
        return value
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_KYIV)
        return dt
    except (ValueError, TypeError):
        return None


def extract_calendar_events(
    result: dict[str, Any],
    calendar_entity: str,
    *,
    exclude_emergency: bool = False,
) -> list[dict[str, Any]]:
    """Extract and sort calendar events from a HA service response."""
    response = result.get("result", {}).get("response", {})
    cal_data = response.get(calendar_entity, {})
    events = cal_data.get("events", [])
    if exclude_emergency:
        events = [
            event
            for event in events
            if event.get("description", "").lower() != "emergency"
        ]
    return sorted(events, key=lambda e: e.get("start", ""))


def build_schedule_lines(events: list[dict[str, Any]]) -> list[str]:
    """Build formatted schedule lines grouped by day."""
    now = now_kyiv()
    now_ts = now.timestamp()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    lines: list[str] = []

    for day_offset in range(2):
        day_s = today_start + timedelta(days=day_offset)
        day_e = day_s + timedelta(days=1)
        day_s_ts = day_s.timestamp()
        day_e_ts = day_e.timestamp()

        if day_e_ts <= now_ts:
            continue

        segments: list[tuple[float, float]] = []
        for event in events:
            ev_start = parse_event_time(event.get("start", ""))
            ev_end = parse_event_time(event.get("end", ""))
            if ev_start is None or ev_end is None:
                continue

            seg_s = max(ev_start.timestamp(), day_s_ts)
            seg_e = min(ev_end.timestamp(), day_e_ts)
            if seg_e > seg_s and seg_e > now_ts:
                segments.append((seg_s, seg_e))

        if not segments:
            continue

        segments.sort()
        merged: list[tuple[float, float]] = [segments[0]]
        for start_ts, end_ts in segments[1:]:
            if start_ts <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end_ts))
            else:
                merged.append((start_ts, end_ts))

        parts: list[str] = []
        for start_ts, end_ts in merged:
            start_dt = datetime.fromtimestamp(start_ts, tz=TZ_KYIV)
            end_dt = datetime.fromtimestamp(end_ts, tz=TZ_KYIV)
            start_hm = format_time_short(start_dt)
            end_hm = "00:00" if end_ts >= day_e_ts else format_time_short(end_dt)
            parts.append(f"{start_hm}–{end_hm}")

        lines.append(f"📅 {format_date_short(day_s)} — {', '.join(parts)}")

    return lines


def next_planned_outage(
    events: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """Return the next future planned outage event."""
    current = now or now_kyiv()
    for event in events:
        start_dt = parse_event_time(event.get("start", ""))
        end_dt = parse_event_time(event.get("end", ""))
        if start_dt is None or end_dt is None:
            continue
        if start_dt > current:
            return start_dt, end_dt
    return None

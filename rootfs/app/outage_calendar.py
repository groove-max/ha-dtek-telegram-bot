"""Shared helpers for outage calendar event processing."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from groups import event_group
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
        if isinstance(value, str) and value.endswith("Z"):
            value = value[:-1] + "+00:00"
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
    if not isinstance(result, dict) or result.get("success") is False:
        raise ValueError("HA calendar service failed")
    response = (result.get("result") or {}).get("response")
    if not isinstance(response, dict) or not isinstance(
        response.get(calendar_entity), dict
    ):
        raise ValueError("HA calendar response is missing")
    events = response[calendar_entity].get("events")
    if not isinstance(events, list) or any(
        not isinstance(e, dict)
        or parse_event_time(e.get("start")) is None
        or parse_event_time(e.get("end")) is None
        for e in events
    ):
        raise ValueError("HA calendar events are malformed")
    if any("usage=display_only" in str(e.get("description", "")) for e in events):
        raise ValueError("Saved schedules are for display only, never notifications")
    if exclude_emergency:
        events = [
            event
            for event in events
            if str(event.get("description", "")).lower().split(";", 1)[0] != "emergency"
        ]
    return sorted(events, key=lambda e: e.get("start", ""))


def build_schedule_lines(
    events: list[dict[str, Any]],
    *,
    header_group: str = "",
    group_labels: dict[str, str] | None = None,
) -> list[str]:
    """Group intervals by day and group, without repeating a single-group header."""
    groups = sorted({event_group(event) for event in events if event_group(event)})
    if groups:
        lines = []
        for group in groups:
            subset = [
                dict(event, description="")
                for event in events
                if event_group(event) == group
            ]
            group_lines = build_schedule_lines(subset)
            if group_lines:
                if len(groups) > 1 or group != header_group:
                    label = (
                        group
                        if group_labels is None
                        else group_labels.get(group, "Графік адреси")
                    )
                    lines.append(f"🔵 {label}")
                lines.extend(group_lines)
        return lines
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


def format_next_outage(
    events: list[dict[str, Any]],
    *,
    header_group: str = "",
    group_labels: dict[str, str] | None = None,
    now: datetime | None = None,
) -> str | None:
    """Format status/preview identically, retaining only necessary group labels."""
    upcoming = next_planned_outage(events, now=now)
    if upcoming is None:
        return None
    start, end = upcoming
    groups = sorted(
        {
            event_group(event)
            for event in events
            if event_group(event)
            and parse_event_time(event.get("start")) == start
            and parse_event_time(event.get("end")) == end
        }
    )
    lines = []
    if groups and not (len(groups) == 1 and groups[0] == header_group):
        labels = [
            group if group_labels is None else group_labels.get(group, "Графік адреси")
            for group in groups
        ]
        lines.append("🔵 " + ", ".join(labels))
    start, end = start.astimezone(TZ_KYIV), end.astimezone(TZ_KYIV)
    end_label = end.strftime("%H:%M" if start.date() == end.date() else "%d.%m %H:%M")
    lines.append(f"{start:%d.%m} — {start:%H:%M}–{end_label}")
    return "\n".join(lines)

"""Shared helpers for outage calendar event processing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

from groups import event_group
from utils import TZ_KYIV, format_date_short, format_time_short, now_kyiv


class ScheduleUnavailable(ValueError):
    """The source explicitly has no verified schedule for notifications yet."""


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
    if any(parse_event_time(e["end"]).timestamp() <= parse_event_time(e["start"]).timestamp() for e in events):
        raise ValueError("HA calendar interval is empty or reversed")
    return sorted(events, key=lambda e: parse_event_time(e["start"]).timestamp())


def normalize_calendar_events(events: list[dict]) -> list[dict]:
    """Canonical intervals, independent of timezone notation and daily splitting.

    Description retains the machine-readable group/type. Source, when supplied,
    is also part of the identity; unrelated events never share a merged window.
    """
    buckets: dict[tuple[str, str], list[tuple[float, float, dict]]] = {}
    for event in events:
        start, end = parse_event_time(event.get("start")), parse_event_time(event.get("end"))
        if start is None or end is None or start.timestamp() >= end.timestamp():
            raise ValueError("Invalid calendar interval")
        identity = (str(event.get("description", "")), str(event.get("source", "")))
        buckets.setdefault(identity, []).append((start.timestamp(), end.timestamp(), event))
    result = []
    for intervals in buckets.values():
        merged = []
        for start, end, event in sorted(intervals, key=lambda item: item[:2]):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]), merged[-1][2])
            else:
                merged.append((start, end, event))
        result.extend(
            {**event, "start": datetime.fromtimestamp(start, TZ_KYIV).isoformat(),
             "end": datetime.fromtimestamp(end, TZ_KYIV).isoformat()}
            for start, end, event in merged
        )
    return sorted(result, key=lambda e: (parse_event_time(e["start"]).timestamp(),
                                        parse_event_time(e["end"]).timestamp(), str(e.get("description", ""))))


def warning_key(group: str, start: datetime) -> str:
    """One warning for a group's start; end-time changes belong to schedule updates."""
    return f"{group}:{start.astimezone(timezone.utc).isoformat()}"


def canonical_warning_key(value: str) -> str | None:
    """Read both the current start-only key and old group:start:end keys."""
    if not isinstance(value, str):
        return None
    parts = re.split(r":(?=\d{4}-\d{2}-\d{2}T)", value, maxsplit=2)
    if len(parts) not in (2, 3) or not parts[0]:
        return None
    start = parse_event_time(parts[1])
    if start is None or (len(parts) == 3 and parse_event_time(parts[2]) is None):
        return None
    return warning_key(parts[0], start)


def verified_coverage_end(group_state, groups, now, kind="outage"):
    """Continuous common coverage; None means an older integration lacks metadata."""
    attrs = (group_state or {}).get("attributes", {})
    if attrs.get("schedule_notifications_ready", True) is not True:
        raise ScheduleUnavailable("Schedule is unverified")
    if "schedule_coverage" not in attrs:
        return None
    coverage = attrs["schedule_coverage"]
    if not isinstance(coverage, list) or not groups:
        raise ValueError("Schedule coverage is missing")
    horizons = []
    try:
        for group in groups:
            ranges = []
            for part in coverage:
                if part["group_id"] != group or part["kind"] != kind:
                    continue
                left, right = datetime.fromisoformat(part["start"]), datetime.fromisoformat(part["end"])
                if left.tzinfo is None or right.tzinfo is None or left.timestamp() >= right.timestamp():
                    raise ValueError("Invalid schedule coverage interval")
                ranges.append((left.timestamp(), right.timestamp()))
            cursor = now.timestamp()
            for left, right in sorted(ranges):
                if left > cursor:
                    break
                cursor = max(cursor, right)
            horizons.append(cursor)
    except (KeyError, TypeError, AttributeError) as err:
        raise ValueError("Invalid schedule coverage") from err
    return datetime.fromtimestamp(min(horizons), timezone.utc)


def upcoming_schedule_events(events, *, now=None, group_state=None, groups=None):
    """Future starts within coverage, with explicit confidence in their end times."""
    now = now or now_kyiv()
    events = normalize_calendar_events(events)
    if groups is None:
        groups = sorted({event_group(e) for e in events if event_group(e)})
    horizon = verified_coverage_end(group_state, groups, now)
    if horizon is not None and horizon.timestamp() <= now.timestamp():
        raise ScheduleUnavailable("No verified schedule at the current time")
    result = []
    for event in events:
        start, end = parse_event_time(event["start"]), parse_event_time(event["end"])
        if start.timestamp() <= now.timestamp() or (horizon and start.timestamp() >= horizon.timestamp()):
            continue
        end_confirmed = horizon is None or end.timestamp() < horizon.timestamp()
        if any(
            event_group(other) == event_group(event)
            and parse_event_time(other["start"]).timestamp() <= end.timestamp() < parse_event_time(other["end"]).timestamp()
            for other in events
        ):
            end_confirmed = False
        result.append({**event, "end_confirmed": end_confirmed})
    return result


def calendar_query_end(group_state, now):
    """Fetch enough of HA's finite calendar to see continuations after midnight."""
    bounds = [now.timestamp() + 2 * 86400]
    coverage = (group_state or {}).get("attributes", {}).get("schedule_coverage", [])
    if isinstance(coverage, list):
        for part in coverage:
            end = parse_event_time(part.get("end")) if isinstance(part, dict) else None
            if end:
                bounds.append(end.timestamp())
    return datetime.fromtimestamp(min(max(bounds), now.timestamp() + 8 * 86400), timezone.utc)


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
    for event in upcoming_schedule_events(events, now=now):
        return parse_event_time(event["start"]), parse_event_time(event["end"])
    return None


def format_next_outage(
    events: list[dict[str, Any]],
    *,
    header_group: str = "",
    group_labels: dict[str, str] | None = None,
    now: datetime | None = None,
    group_state: dict | None = None,
    groups: list[str] | None = None,
) -> str | None:
    """Format status/preview identically, retaining only necessary group labels."""
    events = upcoming_schedule_events(events, now=now, group_state=group_state, groups=groups)
    if not events:
        return None
    first = events[0]
    start, end = parse_event_time(first["start"]), parse_event_time(first["end"])
    confirmed = first["end_confirmed"]
    groups = sorted(
        {
            event_group(event)
            for event in events
            if event_group(event)
            and parse_event_time(event.get("start")) == start
            and parse_event_time(event.get("end")) == end
            and event["end_confirmed"] == confirmed
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
    lines.append(
        f"{start:%d.%m} — {start:%H:%M}–{end_label}"
        if confirmed else f"{start:%d.%m} — від {start:%H:%M}; завершення невідоме"
    )
    return "\n".join(lines)

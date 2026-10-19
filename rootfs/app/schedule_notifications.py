"""Shared delivery acknowledgement for group and schedule notifications."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta
from outage_calendar import normalize_calendar_events, parse_event_time
from utils import TZ_KYIV, now_kyiv


def notification_lock(state, address: str) -> asyncio.Lock:
    """Serialize both notification paths per address, including ticks and HA events.

    Locks belong to the store instance, not its persisted JSON state.
    """
    locks = getattr(state, "_schedule_notification_locks", None)
    if locks is None:
        state._schedule_notification_locks = locks = {}
    return locks.setdefault(address, asyncio.Lock())


def legacy_schedule_signature(events: list[dict]) -> str:
    """Original raw-event fingerprint, retained only for safe state migration."""
    parts = [
        f"{e.get('start', '')}-{e.get('end', '')}-{e.get('description', '')}"
        for e in events
    ]
    raw = "|".join(sorted(parts)) if parts else "none"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def schedule_signature(events: list[dict], *, now=None) -> str:
    """Hash logical intervals using legacy Kyiv day slices for upgrade continuity."""
    parts = []
    first = now.astimezone(TZ_KYIV).replace(hour=0, minute=0, second=0, microsecond=0) if now else None
    last = first + timedelta(days=2) if first else None
    for event in normalize_calendar_events(events):
        start = parse_event_time(event["start"]).astimezone(TZ_KYIV)
        end = parse_event_time(event["end"]).astimezone(TZ_KYIV)
        if first is not None:
            start = max((start, first), key=lambda value: value.timestamp())
            end = min((end, last), key=lambda value: value.timestamp())
        while start.timestamp() < end.timestamp():
            midnight = start.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            stop = min((midnight, end), key=lambda value: value.timestamp())
            description = str(event.get("description", ""))
            if event.get("source"):
                description += "; source=" + str(event["source"])
            parts.append({"start": start.isoformat(), "end": stop.isoformat(), "description": description})
            start = stop
    return "v2:" + legacy_schedule_signature(parts)


def schedule_signature_matches(stored, events, previous_events=None, *, now=None):
    """Migrate only equivalent data; an unacknowledged fetched snapshot is not proof."""
    signature = schedule_signature(events, now=now)
    if isinstance(stored, str) and stored.startswith("v2:"):
        return stored == signature
    if stored in (signature[3:], legacy_schedule_signature(events),
                  legacy_schedule_signature(normalize_calendar_events(events))):
        return True
    if isinstance(previous_events, list):
        try:
            return (
                stored == legacy_schedule_signature(previous_events)
                and signature == schedule_signature(previous_events, now=now)
            )
        except (ValueError, TypeError, AttributeError):
            pass
    return False


async def acknowledge_schedule(feature, events: list[dict], lines: list[str]) -> None:
    """Record exactly the schedule delivered successfully by either feature."""
    feature.state_set("schedule_signature", schedule_signature(events, now=now_kyiv()))
    feature.state_set("has_schedule", bool(lines))
    if lines:
        feature.state_set("last_schedule_seen_at", now_kyiv().isoformat())
    save = getattr(feature.state, "save", None)
    if save is not None:
        await save()

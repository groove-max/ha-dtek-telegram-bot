"""Shared delivery acknowledgement for group and schedule notifications."""

from __future__ import annotations

import asyncio
import hashlib
from utils import now_kyiv


def notification_lock(state, address: str) -> asyncio.Lock:
    """Serialize both notification paths per address, including ticks and HA events.

    Locks belong to the store instance, not its persisted JSON state.
    """
    locks = getattr(state, "_schedule_notification_locks", None)
    if locks is None:
        state._schedule_notification_locks = locks = {}
    return locks.setdefault(address, asyncio.Lock())


def schedule_signature(events: list[dict]) -> str:
    """Keep the existing signature format so upgrades do not resend schedules."""
    parts = [
        f"{e.get('start', '')}-{e.get('end', '')}-{e.get('description', '')}"
        for e in events
    ]
    raw = "|".join(sorted(parts)) if parts else "none"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


async def acknowledge_schedule(feature, events: list[dict], lines: list[str]) -> None:
    """Record exactly the schedule delivered successfully by either feature."""
    feature.state_set("schedule_signature", schedule_signature(events))
    feature.state_set("has_schedule", bool(lines))
    if lines:
        feature.state_set("last_schedule_seen_at", now_kyiv().isoformat())
    save = getattr(feature.state, "save", None)
    if save is not None:
        await save()

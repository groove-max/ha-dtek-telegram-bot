"""Optional all-groups images for an already delivered schedule notification."""

import asyncio
import json
from datetime import timedelta

from ha_client import HACommandError
from schedule_notifications import schedule_parts
from schedule_pages import select_page, render_page
from utils import now_kyiv, format_date, format_datetime
from schedule_image_state import image_binding, image_content_hash


async def building_grid_entity(ha, profile):
    buildings = await ha.command("dtek_monitor/buildings")
    matches = {b["status_entity"] for b in buildings
               if b.get("entity") == f"binary_sensor.{profile.entity_prefix}_power"
               and b.get("status_entity")}
    if len(matches) != 1:
        raise HACommandError("not_available")
    return matches.pop()


async def fetch_grid(ha, profile):
    if getattr(profile, "mode", "building") == "personal":
        return await ha.command("dtek_monitor/bot_account", entity_id=profile.account_entity, action="grid")
    entity = await building_grid_entity(ha, profile)
    return await ha.command("dtek_monitor/schedule_grid", entity_id=entity)


async def render_day_images(data, offset, personal, now):
    first = select_page(data, offset, personal, "all")
    pages = []
    for page in range(first.pages):
        view = select_page(data, offset, personal, "all", page)
        png = await asyncio.to_thread(render_page, view, now=now)
        caption = f"📊 Усі групи · {format_date(view.day['date'])}"
        if first.pages > 1:
            caption += f" · {page + 1}/{first.pages}"
        caption += f"\n🕒 Сформовано: {format_datetime(now)}"
        pages.append((png, f"dtek-{view.day['date']}-{page+1}.png", caption))
    return pages


def changed_dates(events, previous, now):
    def by_date(items):
        result = {}
        for item in schedule_parts(items or [], now=now):
            result.setdefault(item["start"][:10], set()).add(json.dumps(item, sort_keys=True))
        return result
    old, new = by_date(previous), by_date(events)
    changed = {day for day in old.keys() | new.keys() if old.get(day) != new.get(day)}
    return changed or {(now + timedelta(days=n)).date().isoformat() for n in (0, 1)}


async def attach_schedule(feature, message_id, events, previous):
    """True means finished/skipped, False requests a fresh feature-level retry.

    Text is authoritative and has already been delivered. Missing/unsupported
    grids never hide it. A definite temporary Telegram rejection can retry the
    images, without repeating text or previously accepted albums.
    """
    if not getattr(getattr(feature.config, "schedule_change", None), "include_image", True):
        return True
    expected = getattr(feature, "_verified_calendar", None)
    if expected is None:
        return True

    async def current():
        groups = await feature.read_groups(require_schedule=True)
        return expected == (tuple(groups), feature._schedule_revision(), feature._schedule_source())

    try:
        async with asyncio.timeout(20):
            if not await current():
                return True
            personal = getattr(feature.config, "mode", "building") == "personal"
            data = await fetch_grid(feature.ha, feature.config)
            if not await current():
                return True
            now = now_kyiv()
            dates = changed_dates(events, previous, now)
            albums = []
            for offset, day in enumerate(data.get("days", [])[:2]):
                if day.get("date") not in dates:
                    continue
                pages = await render_day_images(data, offset, personal, now)
                albums.append((day["date"], pages, image_content_hash(data, offset, personal)))
    except Exception as exc:
        feature.log.warning("Schedule image unavailable; text retained (%s)", type(exc).__name__)
        return True

    complete = True
    for day, pages, content_hash in albums:
        try:
            if not await current():
                return True
            complete &= await feature.tg.send_schedule_images(
                pages, chat_id=feature.telegram_chat_id, reply_to=message_id,
                delivery_key=f"{feature._delivery_key}:grid:{day}",
                # The parent text controls alerts; its images are quiet follow-ups.
                disable_notification=True,
                schedule={"date": day, "binding": image_binding(feature.config), "groups": list(expected[0]),
                          "content_hash": content_hash,
                          "interval": getattr(getattr(feature.config, "schedule_image", None), "update_interval", 300)},
                context={"feature": feature.name, "address": feature.profile_key, "stream": f"grid:{day}"},
            )
        except Exception as exc:
            # No blind fallback send after an uncertain network result.
            feature.log.warning("Schedule image skipped (%s)", type(exc).__name__)
    return complete

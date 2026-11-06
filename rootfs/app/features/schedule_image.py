"""Keep the latest automatic schedule current on data changes and timer ticks."""

import asyncio

from features.base import Feature
from schedule_attachments import fetch_grid, render_day_images
from schedule_image_state import image_binding, image_content_hash
from schedule_notifications import notification_lock
from utils import now_kyiv


class ScheduleImageFeature(Feature):
    name = "schedule_image"

    @property
    def enabled(self):
        option = getattr(self.config, "schedule_image", None)
        return bool(option and option.enabled and self.config.schedule_change.include_image
                    and (self.config.schedule_change.enabled or self.config.group_change.enabled))

    def get_watched_entities(self):
        entities = [self.entity("schedule_changed"), *self.entity_candidates("schedule_group")]
        if self.config.mode == "personal" and self.config.account_entity:
            entities.append(self.config.account_entity)
        return entities

    async def on_state_change(self, *args):
        # Events are wake-ups only. Fetch verified data instead of trusting a
        # queued payload, including attribute-only revisions and cabinet groups.
        await self.on_tick()

    async def on_start(self):
        await self.on_tick()

    async def on_tick(self):
        if not self.enabled or not self.tg.journal:
            return
        async with notification_lock(self.state, self.profile_key):
            await self._refresh()

    async def _refresh(self):
        now = now_kyiv()
        store = self.tg.journal.schedule_images
        record = store.latest(self.effective_telegram_chat_id, self.profile_key, now.date().isoformat())
        if record is None or record['binding'] != image_binding(self.config) or record['retry_after'] > now.timestamp():
            return
        interval = self.config.schedule_image.update_interval
        try:
            async with asyncio.timeout(20):
                groups = await self.read_groups(require_schedule=True)
                if groups != record['groups']:
                    return  # Wait for the notification of the new assignment.
                expected = (tuple(groups), self._schedule_revision(), self._schedule_source())
                data = await fetch_grid(self.ha, self.config)
                offset = next((i for i, d in enumerate(data.get('days', [])[:2]) if d['date'] == record['day']), None)
                if offset is None:
                    return
                personal = self.config.mode == 'personal'
                content_hash = image_content_hash(data, offset, personal)
                if record['next_attempt'] > now.timestamp() and content_hash == record['content_hash']:
                    return
                pages = await render_day_images(data, offset, personal, now)
                if len(pages) != len(record['ids']):
                    return  # Never truncate an album or add new messages on a timer.
            # Separate the normal image interval from failure/Telegram backoff.
            # Persist the latter before uploading, also covering an abrupt stop.
            store.defer(record, now.timestamp() + interval)
            for message_id, image in zip(record['ids'], pages):
                groups = await self.read_groups(require_schedule=True)
                if (expected != (tuple(groups), self._schedule_revision(), self._schedule_source())
                        or now_kyiv().date().isoformat() != record['day'] or not store.matches(record)):
                    store.resume(record)
                    return
                result, delay = await self.tg.edit_schedule_image(
                    message_id, image, chat_id=record['chat'],
                    context={'feature': self.name, 'address': self.profile_key, 'stream': record['day']})
                if result == 'gone':
                    store.forget(record)
                    return
                if result != 'ok':
                    store.defer(record, now_kyiv().timestamp() + max(interval, delay))
                    return
            store.applied(record, content_hash, now_kyiv().timestamp() + interval)
        except Exception as exc:
            store.defer(record, now_kyiv().timestamp() + interval)
            self.log.warning('Schedule image refresh paused (%s)', type(exc).__name__)

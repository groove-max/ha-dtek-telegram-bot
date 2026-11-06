"""Persisted photo edits, route safety, timer pacing and display dates."""
import aiogram
import jinja2
import asyncio
import sqlite3
from datetime import datetime, timedelta
import logging
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rootfs/app'))
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest
from aiogram.methods import EditMessageMedia
from aiogram.types import InputMediaPhoto
from config import AddressConfig, ScheduleImageConfig
from delivery_journal import DeliveryJournal
from features.schedule_image import ScheduleImageFeature
from schedule_image_state import image_binding, image_content_hash, ScheduleImageStore
from schedule_attachments import render_day_images
from schedule_image import stamp
from telegram_service import TelegramService
from utils import format_datetime, format_date, TZ_KYIV
from test_schedule_change_feature import DummyState
from test_grid_members import large_grid

NOW = datetime(2026, 11, 6, 12, tzinfo=TZ_KYIV)


class LiveScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'journal.db'
        self.journal = DeliveryJournal(self.path)
        self.store = self.journal.schedule_images
        self.config = AddressConfig(entity_prefix='demo', telegram_chat_id='123',
                                    schedule_image={'enabled': True, 'update_interval': 300})
        self.tg = SimpleNamespace(journal=self.journal, resolve_chat_id=lambda chat: chat or '123',
                                 edit_schedule_image=AsyncMock(return_value=('ok', 0)))
        self.feature = ScheduleImageFeature(address_config=self.config, ha_client=SimpleNamespace(),
                telegram=self.tg, state_store=DummyState(), templates=None)
        self.feature.read_groups = AsyncMock(return_value=['GPV1.1'])
        self.feature._schedule_revision = lambda: 'v1'
        self.feature._schedule_source = lambda: ('region', 'address')
        data = large_grid(13)
        for i, d in enumerate(data['days']): d['date'] = (NOW + timedelta(days=i)).date().isoformat()
        self.fetch = patch('features.schedule_image.fetch_grid', AsyncMock(return_value=data)).start()
        self.render = patch('features.schedule_image.render_day_images', AsyncMock(return_value=[(b'png','page1.png','date'), (b'png','page2.png','date')])).start()
        self.clock = patch('features.schedule_image.now_kyiv', return_value=NOW).start()
        self.addCleanup(patch.stopall)
        self.remember([51, 52])

    def remember(self, ids, day=None, chat='123', address='demo'):
        self.store.remember(chat, address, day or NOW.date().isoformat(), image_binding(self.config), ids, ['GPV1.1'])
        self.store.db.execute('UPDATE schedule_images SET next_attempt=0')
        self.store.db.commit()

    async def asyncTearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    async def test_edits_every_page_and_throttles_without_new_posts(self):
        await self.feature.on_tick()
        self.assertEqual([a.args[0] for a in self.tg.edit_schedule_image.await_args_list], [51, 52])
        await self.feature.on_tick()
        self.assertEqual(self.tg.edit_schedule_image.await_count, 2)
        self.assertEqual(self.fetch.await_count, 2)
        self.clock.return_value = NOW + timedelta(minutes=5)
        await self.feature.on_tick()
        self.assertEqual(self.tg.edit_schedule_image.await_count, 4)

    async def test_newer_graph_replaces_previous_and_other_profile_isolated(self):
        self.remember([91, 92], address='another')
        self.remember([71, 72])
        await self.feature.on_tick()
        self.assertEqual([a.args[0] for a in self.tg.edit_schedule_image.await_args_list], [71, 72])

    async def test_restart_keeps_references_and_cooldown(self):
        await self.feature.on_tick()
        self.journal.close()
        self.journal = DeliveryJournal(self.path)
        self.tg.journal = self.journal
        await self.feature.on_start()
        self.assertEqual(self.tg.edit_schedule_image.await_count, 2)
        self.clock.return_value = NOW + timedelta(minutes=5)
        await self.feature.on_tick()
        self.assertEqual([a.args[0] for a in self.tg.edit_schedule_image.await_args_list], [51,52,51,52])

    async def test_tomorrow_becomes_current_after_midnight_yesterday_is_frozen(self):
        self.remember([81,82], day='2026-11-07')
        await self.feature.on_tick()
        self.clock.return_value = NOW + timedelta(days=1)
        await self.feature.on_tick()
        self.assertEqual([a.args[0] for a in self.tg.edit_schedule_image.await_args_list], [51,52,81,82])
        self.assertEqual(self.render.await_args.args[1], 1)

    async def test_disabled_different_binding_and_route_do_not_fetch(self):
        self.config.schedule_image.enabled = False
        await self.feature.on_tick()
        self.config.schedule_image.enabled = True
        self.config.entity_prefix = 'different'
        await self.feature.on_tick()
        self.config.entity_prefix = 'demo'
        self.config.telegram_chat_id = '999'
        await self.feature.on_tick()
        self.fetch.assert_not_awaited()

    async def test_grid_unavailable_preserves_image_and_limits_attempts(self):
        self.fetch.side_effect = TimeoutError()
        await self.feature.on_tick()
        await self.feature.on_tick()
        self.tg.edit_schedule_image.assert_not_awaited()
        self.assertEqual(self.fetch.await_count, 1)
        self.assertIsNotNone(self.store.latest('123','demo','2026-11-06'))

    async def test_changed_group_or_revision_never_edits_mismatched_graph(self):
        self.feature.read_groups.return_value = ['GPV2.1']
        await self.feature.on_tick()
        self.fetch.assert_not_awaited()
        self.remember([51,52])
        self.feature.read_groups.side_effect = [['GPV1.1'], ['GPV2.1']]
        await self.feature.on_tick()
        self.tg.edit_schedule_image.assert_not_awaited()

    async def test_album_size_changed_does_not_create_or_drop_pages(self):
        self.render.return_value = [(b'png','page.png','caption')]
        await self.feature.on_tick()
        self.tg.edit_schedule_image.assert_not_awaited()

    async def test_midnight_during_render_stops_editing_previous_day(self):
        self.clock.side_effect = [NOW, NOW + timedelta(days=1)]
        await self.feature.on_tick()
        self.tg.edit_schedule_image.assert_not_awaited()

    async def test_new_message_during_render_is_not_overwritten(self):
        async def render(*args):
            self.remember([71,72])
            return [(b'png','p1','c'), (b'png','p2','c')]
        self.render.side_effect = render
        await self.feature.on_tick()
        self.tg.edit_schedule_image.assert_not_awaited()

    async def test_deleted_photo_stops_updates_without_recreating_it(self):
        self.tg.edit_schedule_image.return_value = ('gone',0)
        await self.feature.on_tick()
        self.clock.return_value = NOW + timedelta(minutes=10)
        await self.feature.on_tick()
        self.assertEqual(self.tg.edit_schedule_image.await_count,1)
        self.assertIsNone(self.store.latest('123','demo','2026-11-06'))

    async def test_retry_after_is_persisted_and_stops_rest_of_album(self):
        self.tg.edit_schedule_image.return_value = ('retry',900)
        await self.feature.on_tick()
        self.clock.return_value = NOW + timedelta(minutes=5)
        await self.feature.on_tick()
        self.assertEqual(self.tg.edit_schedule_image.await_count,1)
        self.assertEqual(self.store.latest('123','demo','2026-11-06')['next_attempt'],NOW.timestamp()+900)

    async def test_schedule_event_refreshes_immediately_then_resets_timer(self):
        await self.feature.on_tick()
        self.clock.return_value = NOW + timedelta(seconds=30)
        self.fetch.return_value['days'][0]['rows'][0]['cells'][0] = 'outage'
        await self.feature.on_state_change(self.feature.entity('schedule_changed'), {}, {})
        self.assertEqual(self.tg.edit_schedule_image.await_count, 4)
        self.assertEqual(self.store.latest('123','demo','2026-11-06')['next_attempt'], NOW.timestamp()+330)
        self.clock.return_value = NOW + timedelta(seconds=300)
        await self.feature.on_tick()
        self.assertEqual(self.tg.edit_schedule_image.await_count, 4)
        self.clock.return_value = NOW + timedelta(seconds=330)
        await self.feature.on_tick()
        self.assertEqual(self.tg.edit_schedule_image.await_count, 6)

    async def test_changes_outside_home_groups_also_refresh_all_groups_table(self):
        await self.feature.on_tick()
        self.fetch.return_value['days'][0]['rows'][-1]['cells'][0] = 'outage'
        await self.feature.on_state_change(self.feature.entity('schedule_group'), {}, {})
        self.assertEqual(self.tg.edit_schedule_image.await_count, 4)

    async def test_repeated_events_and_poll_times_do_not_render_or_upload(self):
        await self.feature.on_tick()
        self.fetch.return_value['verified_at'] = (NOW + timedelta(seconds=10)).isoformat()
        self.fetch.return_value['source_updated_at'] = (NOW + timedelta(seconds=10)).isoformat()
        self.fetch.return_value['revision'] = 'rechecked'
        for _ in range(3):
            await self.feature.on_state_change(self.feature.entity('schedule_changed'), {}, {})
        self.assertEqual(self.tg.edit_schedule_image.await_count, 2)
        self.assertEqual(self.render.await_count, 1)

    async def test_tomorrow_only_changes_do_not_force_refresh_today(self):
        await self.feature.on_tick()
        self.fetch.return_value['days'][1]['rows'][0]['cells'][0] = 'outage'
        await self.feature.on_state_change(self.feature.entity('schedule_changed'), {}, {})
        self.assertEqual(self.tg.edit_schedule_image.await_count, 2)

    async def test_new_notification_already_has_current_content_no_redundant_edit(self):
        digest = image_content_hash(self.fetch.return_value, 0, False)
        with patch('schedule_image_state.time.time', return_value=NOW.timestamp()):
            self.store.remember('123','demo','2026-11-06',image_binding(self.config),[71,72],['GPV1.1'],content_hash=digest)
        await self.feature.on_state_change(self.feature.entity('schedule_changed'), {}, {})
        self.tg.edit_schedule_image.assert_not_awaited()
        self.render.assert_not_awaited()

    async def test_changed_data_on_restart_does_not_wait_for_periodic_deadline(self):
        await self.feature.on_tick()
        self.journal.close()
        self.journal = DeliveryJournal(self.path)
        self.tg.journal = self.journal
        self.fetch.return_value['days'][0]['rows'][0]['cells'][0] = 'outage'
        await self.feature.on_start()
        self.assertEqual(self.tg.edit_schedule_image.await_count, 4)

    async def test_events_do_not_bypass_persisted_telegram_retry_after(self):
        self.tg.edit_schedule_image.return_value = ('retry',900)
        await self.feature.on_tick()
        self.fetch.return_value['days'][0]['rows'][0]['cells'][0] = 'outage'
        self.clock.return_value = NOW + timedelta(minutes=1)
        await self.feature.on_state_change(self.feature.entity('schedule_changed'), {}, {})
        self.assertEqual(self.tg.edit_schedule_image.await_count, 1)
        self.journal.close()
        self.journal = DeliveryJournal(self.path)
        self.tg.journal = self.journal
        await self.feature.on_start()
        self.assertEqual(self.tg.edit_schedule_image.await_count, 1)
        self.tg.edit_schedule_image.return_value = ('ok',0)
        self.clock.return_value = NOW + timedelta(minutes=15)
        await self.feature.on_tick()
        self.assertEqual(self.tg.edit_schedule_image.await_count, 3)

    async def test_parallel_event_and_tick_edit_each_page_once(self):
        await asyncio.gather(self.feature.on_tick(), self.feature.on_state_change('entity', {}, {}))
        self.assertEqual(self.tg.edit_schedule_image.await_count, 2)

    async def test_revision_change_during_render_does_not_delay_new_snapshot(self):
        async def render(*args):
            self.feature._schedule_revision = lambda: 'v2'
            return [(b'png','p1','c'),(b'png','p2','c')]
        self.render.side_effect = render
        await self.feature.on_tick()
        self.tg.edit_schedule_image.assert_not_awaited()
        self.render.side_effect = None
        await self.feature.on_state_change('entity', {}, {})
        self.assertEqual(self.tg.edit_schedule_image.await_count, 2)

    def test_watches_attribute_changes_and_personal_account(self):
        entities = self.feature.get_watched_entities()
        self.assertIn(self.feature.entity('schedule_changed'), entities)
        self.assertIn(self.feature.entity('schedule_group'), entities)
        self.config.mode = 'personal'
        self.config.account_entity = 'sensor.account'
        self.assertIn('sensor.account', self.feature.get_watched_entities())

    def test_legacy_store_migration_preserves_retry_and_message_ids(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE schedule_images (chat TEXT,address TEXT,day TEXT,binding TEXT,ids TEXT,groups_json TEXT,next_attempt REAL,PRIMARY KEY(chat,address,day))')
        db.execute("INSERT INTO schedule_images VALUES('123','demo','2026-11-06','old','[51, 52]','[\"GPV1.1\"]',900)")
        store = ScheduleImageStore(db)
        record = store.latest('123','demo','2026-11-06')
        self.assertEqual(record['ids'],[51,52])
        self.assertEqual(record['retry_after'],900)
        self.assertEqual(record['content_hash'],'')
        store.applied(record,'new',1200)
        self.assertEqual(ScheduleImageStore(db).latest('123','demo','2026-11-06')['retry_after'],0)

    def test_default_opt_out_and_interval_validation(self):
        self.assertFalse(ScheduleImageConfig().enabled)
        for value in (0, 59, 3601):
            with self.assertRaises(ValueError): ScheduleImageConfig(update_interval=value)

    def test_stored_references_are_bounded_even_when_updater_is_disabled(self):
        for day in range(7, 13):
            self.remember([51, 52], day=f'2026-11-{day:02}')
        dates = [r[0] for r in self.store.db.execute('SELECT day FROM schedule_images ORDER BY day')]
        self.assertEqual(dates, ['2026-11-10', '2026-11-11', '2026-11-12'])


class MediaEditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = DeliveryJournal(Path(self.tmp.name)/'journal.db')
        self.bot = SimpleNamespace(edit_message_media=AsyncMock(return_value=SimpleNamespace(message_id=44)),
            send_photo=AsyncMock(return_value=SimpleNamespace(message_id=44)),
            send_media_group=AsyncMock(return_value=[SimpleNamespace(message_id=51),SimpleNamespace(message_id=52)]))
        with patch('telegram_service.Bot',return_value=self.bot):
            self.service=TelegramService('123:fake','123',journal=self.journal)
        self.journal.db.execute("INSERT INTO owned_messages VALUES('123', 44)")
        self.journal.db.commit()
        self.image=(b'png','day.png','06.11.2026 12:00')
        self.context={'feature':'schedule_image','address':'demo','stream':'2026-11-06'}

    async def asyncTearDown(self):
        self.journal.close();self.tmp.cleanup()

    async def edit(self):
        return await self.service.edit_schedule_image(44,self.image,chat_id='123',context=self.context)

    async def test_edits_in_place_without_send_and_checks_ownership_and_route(self):
        self.assertEqual(await self.edit(),('ok',0))
        kwargs=self.bot.edit_message_media.await_args.kwargs
        self.assertEqual(kwargs['message_id'],44)
        self.assertEqual(kwargs['media'].caption,'06.11.2026 12:00')
        self.bot.send_photo.assert_not_awaited()
        self.service._test_chat_id='999'
        self.assertEqual(await self.edit(),('gone',0))
        self.service._test_chat_id=''
        self.journal.db.execute('DELETE FROM owned_messages')
        self.assertEqual(await self.edit(),('gone',0))
        self.assertEqual(self.bot.edit_message_media.await_count,1)

    async def test_error_classification_no_sleep_or_fallback_send(self):
        method=EditMessageMedia(chat_id=123,message_id=44,media=InputMediaPhoto(media='file'))
        for error,result in [
            (TelegramRetryAfter(method=method,message='rate limit',retry_after=900),('retry',900)),
            (TelegramBadRequest(method=method,message='message to edit not found'),('gone',0)),
            (TelegramBadRequest(method=method,message='message is not modified'),('ok',0)),
            (TimeoutError(),('retry',0))]:
            self.bot.edit_message_media.side_effect=error
            self.assertEqual(await self.edit(),result)
        self.assertEqual(self.bot.edit_message_media.await_count,4)
        self.bot.send_photo.assert_not_awaited()

    async def test_album_delivery_remembers_every_owned_page_only_once(self):
        images=[self.image,self.image]
        kwargs=dict(chat_id='123',reply_to=44,delivery_key='test',disable_notification=True,
            context=self.context,schedule={'date':'2026-11-06','binding':'same','groups':['GPV1.1']})
        self.assertTrue(await self.service.send_schedule_images(images,**kwargs))
        record=self.journal.schedule_images.latest('123','demo','2026-11-06')
        self.assertEqual(record['ids'],[51,52])
        self.assertTrue(all(self.journal.owns('123',i) for i in record['ids']))
        self.assertTrue(await self.service.send_schedule_images(images,**kwargs))
        self.assertEqual(self.journal.schedule_images.latest('123','demo','2026-11-06'),record)
        self.bot.send_media_group.assert_awaited_once()

    async def test_ambiguous_send_is_not_registered_for_edits(self):
        self.bot.send_photo.side_effect=TimeoutError()
        self.assertTrue(await self.service.send_schedule_images([self.image], chat_id='123',reply_to=44,delivery_key='ambiguous',
            disable_notification=True,context=self.context,schedule={'date':'2026-11-06','binding':'same','groups':['GPV1.1']}))
        self.assertIsNone(self.journal.schedule_images.latest('123','demo','2026-11-06'))


class DatePresentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_grid_caption_full_dates_no_city_suffix(self):
        data=large_grid(2);data['days'][0]['date']='2026-11-06'
        with patch('schedule_attachments.render_page',return_value=b'png'):
            pages=await render_day_images(data,0,False,NOW)
        self.assertEqual(pages[0][2],'📊 Усі групи · 06.11.2026\n🕒 Сформовано: 06.11.2026 12:00')

    def test_dates_share_format_and_convert_utc_including_dst(self):
        self.assertEqual(format_date('2026-11-06'),'06.11.2026')
        for source,expected in [('2026-11-06T10:00:00Z','06.11.2026 12:00'),
                                ('2026-07-05T22:30:00Z','06.07.2026 01:30'),
                                ('2026-11-06T12:00:00','06.11.2026 12:00')]:
            self.assertEqual(format_datetime(source),expected)
            self.assertEqual(stamp(source),expected)

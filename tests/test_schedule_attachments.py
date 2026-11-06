"""Automatic grids stay optional, correctly routed, and safe to retry."""
import aiogram
import jinja2
import hashlib
import logging
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rootfs/app'))
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest
from aiogram.methods import SendPhoto
from config import ScheduleChangeConfig
from delivery_journal import DeliveryJournal
from schedule_attachments import attach_schedule, changed_dates, building_grid_entity
from telegram_service import TelegramService
from utils import TZ_KYIV
from test_grid_members import large_grid
import test_schedule_change_feature as feature_fixtures

NOW = datetime(2026, 10, 25, 12, tzinfo=TZ_KYIV)


def event(day=25, start=14, end=16):
    return {'start': f'2026-10-{day}T{start:02}:00:00+02:00',
            'end': f'2026-10-{day}T{end:02}:00:00+02:00', 'summary': 'GPV1.1'}


class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    def feature(self, personal=False):
        ha = SimpleNamespace(command=AsyncMock(return_value=large_grid(13)))
        if not personal:
            ha.command.side_effect = lambda command, **kw: ([{'entity': 'binary_sensor.demo_power', 'status_entity': 'sensor.renamed'}]
                if command.endswith('/buildings') else large_grid(13))
        return SimpleNamespace(config=SimpleNamespace(mode='personal' if personal else 'building',
                entity_prefix='demo', account_entity='sensor.account', schedule_change=ScheduleChangeConfig()),
            ha=ha, tg=SimpleNamespace(send_schedule_images=AsyncMock(return_value=True)),
            _verified_calendar=(('GPV1.1',), 'v1', 'published'),
            read_groups=AsyncMock(return_value=['GPV1.1']), _schedule_revision=lambda: 'v1',
            _schedule_source=lambda: 'published', log=logging.getLogger('test'),
            telegram_chat_id='123', profile_key='demo', _delivery_key='notice', name='schedule_change')

    async def test_tomorrow_only_all_groups_paged_and_highlight_preserved(self):
        f = self.feature()
        with patch('schedule_attachments.now_kyiv', return_value=NOW), patch('schedule_attachments.render_page', return_value=b'png') as render:
            self.assertTrue(await attach_schedule(f, 44, [event(), event(26, 15)], [event(), event(26)]))
        calls = f.tg.send_schedule_images.await_args_list
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0].args[0]), 2)
        self.assertIn('2026-10-26', calls[0].kwargs['delivery_key'])
        self.assertEqual(calls[0].kwargs['reply_to'], 44)
        self.assertTrue(calls[0].kwargs['disable_notification'])
        self.assertEqual([c.args[0].scope for c in render.call_args_list], ['all', 'all'])
        self.assertEqual(sum(len(c.args[0].rows) for c in render.call_args_list), 13)
        f.ha.command.assert_any_await('dtek_monitor/schedule_grid', entity_id='sensor.renamed')

    async def test_personal_uses_authorized_account_grid(self):
        f = self.feature(True)
        with patch('schedule_attachments.now_kyiv', return_value=NOW), patch('schedule_attachments.render_page', return_value=b'png'):
            await attach_schedule(f, 44, [event()], [])
        f.ha.command.assert_awaited_once_with('dtek_monitor/bot_account', entity_id='sensor.account', action='grid')

    async def test_disabled_and_unverified_never_fetch(self):
        for disabled in (True, False):
            f = self.feature()
            if disabled: f.config.schedule_change.include_image = False
            else: f._verified_calendar = None
            self.assertTrue(await attach_schedule(f, 44, [], []))
            f.ha.command.assert_not_awaited()
            f.tg.send_schedule_images.assert_not_awaited()

    async def test_unavailable_grid_retains_text_and_finishes(self):
        for payload in ({'status': 'unpublished', 'days': []}, {'days': [{'date': '2026-10-25'}]}):
            f = self.feature(True)
            f.ha.command.return_value = payload
            with patch('schedule_attachments.now_kyiv', return_value=NOW):
                self.assertTrue(await attach_schedule(f, 44, [event()], []))
            f.tg.send_schedule_images.assert_not_awaited()
        f.ha.command.side_effect = TimeoutError()
        self.assertTrue(await attach_schedule(f, 44, [], []))

    async def test_revision_change_during_fetch_or_render_never_sends(self):
        for changed_at in (2, 3):
            f = self.feature()
            f.read_groups.side_effect = [['GPV1.1']] * (changed_at - 1) + [['GPV2.1']]
            with patch('schedule_attachments.now_kyiv', return_value=NOW), patch('schedule_attachments.render_page', return_value=b'png'):
                self.assertTrue(await attach_schedule(f, 44, [event()], []))
            f.tg.send_schedule_images.assert_not_awaited()

    async def test_both_days_and_transient_failure_requests_retry(self):
        f = self.feature()
        f.tg.send_schedule_images.side_effect = [True, False]
        with patch('schedule_attachments.now_kyiv', return_value=NOW), patch('schedule_attachments.render_page', return_value=b'png'):
            self.assertFalse(await attach_schedule(f, 44, [event(), event(26)], []))
        self.assertEqual(f.tg.send_schedule_images.await_count, 2)
        self.assertTrue(all(call.kwargs['disable_notification'] for call in f.tg.send_schedule_images.await_args_list))

    async def test_follow_up_photos_are_silent_even_when_parent_alerts_are_audible(self):
        for feature_name in ('schedule_change', 'group_change'):
            for count in (12, 13):  # One photo or a paginated album, for both dates.
                f = self.feature(True)
                f.name = feature_name
                f.config.schedule_change.silent = False
                f.config.group_change = SimpleNamespace(silent=False)
                f.ha.command.return_value = large_grid(count)
                with patch('schedule_attachments.now_kyiv', return_value=NOW), patch('schedule_attachments.render_page', return_value=b'png'):
                    self.assertTrue(await attach_schedule(f, 44, [event(), event(26)], []))
                calls = f.tg.send_schedule_images.await_args_list
                self.assertEqual(len(calls), 2)
                self.assertTrue(all(call.kwargs['disable_notification'] for call in calls))
                self.assertEqual([len(call.args[0]) for call in calls], [1, 1] if count == 12 else [2, 2])
                self.assertFalse(f.config.schedule_change.silent)
                self.assertFalse(f.config.group_change.silent)

    async def test_ambiguous_building_mapping_rejected(self):
        ha = SimpleNamespace(command=AsyncMock(return_value=[
            {'entity': 'binary_sensor.demo_power', 'status_entity': 'sensor.one'},
            {'entity': 'binary_sensor.demo_power', 'status_entity': 'sensor.two'}]))
        with self.assertRaises(Exception):
            await building_grid_entity(ha, SimpleNamespace(entity_prefix='demo'))

    def test_defaults_and_date_selection_including_cancellation(self):
        self.assertTrue(ScheduleChangeConfig().include_image)
        self.assertFalse(ScheduleChangeConfig(include_image=False).include_image)
        self.assertEqual(changed_dates([], [event()], NOW), {'2026-10-25'})
        self.assertEqual(changed_dates([event(), event(26, 15)], [event(), event(26)], NOW), {'2026-10-26'})
        self.assertEqual(changed_dates([], [], NOW), {'2026-10-25', '2026-10-26'})

    def test_pending_schedule_identity_is_independent_of_power_changes(self):
        f, tg, state = feature_fixtures.ScheduleChangeFeatureTest()._build_feature()
        f.render('schedule_change', schedule_lines=['14:00–16:00'])
        key = f._delivery_key
        f.state_set('power_last_change', '2026-10-25T15:00:00+02:00')
        f.render('schedule_change', schedule_lines=['14:00–16:00'])
        self.assertEqual(f._delivery_key, key)
        f.render('schedule_change', schedule_lines=['14:00–17:00'])
        self.assertNotEqual(f._delivery_key, key)

    async def test_feature_acknowledges_only_after_attachment_finished(self):
        f, tg, state = feature_fixtures.ScheduleChangeFeatureTest()._build_feature()
        f._fetch_outage_events = AsyncMock(return_value=[event()])
        f.send_message = AsyncMock(return_value=44)
        state.set(f.profile_key, 'schedule_signature', 'previous')
        with patch('features.schedule_change.now_kyiv', return_value=NOW), patch('outage_calendar.now_kyiv', return_value=NOW), patch('schedule_attachments.attach_schedule', AsyncMock(side_effect=[False, True])) as attach:
            await f._check_and_notify()
            self.assertEqual(state.get(f.profile_key, 'schedule_signature'), 'previous')
            await f._check_and_notify()
            self.assertNotEqual(state.get(f.profile_key, 'schedule_signature'), 'previous')
            self.assertEqual(attach.await_count, 2)
            self.assertFalse(f.send_message.await_args.kwargs['disable_notification'])


class MediaDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = DeliveryJournal(Path(self.tmp.name) / 'journal.db')
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=44)),
                                   send_photo=AsyncMock(return_value=SimpleNamespace(message_id=45)),
                                   send_media_group=AsyncMock(return_value=[SimpleNamespace(message_id=46), SimpleNamespace(message_id=47)]))
        with patch('telegram_service.Bot', return_value=self.bot):
            self.service = TelegramService('123:fake', '123', journal=self.journal)
        # Real parent delivery, including ownership and the retry key.
        self.text = 'Довге повідомлення ' * 100
        self.parent = await self.service.send_message(self.text, delivery_key='notice')
        self.images = [(b'png', 'today.png', '📊 Усі групи')]
        self.context = {'feature': 'schedule_change', 'address': 'demo', 'stream': 'grid:today'}

    async def asyncTearDown(self):
        self.journal.db.close()
        self.tmp.cleanup()

    async def send(self, **kw):
        return await self.service.send_schedule_images(kw.pop('images', self.images), chat_id='123', reply_to=self.parent,
            delivery_key=kw.pop('key', 'notice:grid:today'), disable_notification=True, context=self.context, **kw)

    async def test_photo_replies_to_full_text_and_does_not_repeat(self):
        self.assertTrue(await self.send())
        self.assertTrue(await self.send())
        self.bot.send_photo.assert_awaited_once()
        args = self.bot.send_photo.await_args.kwargs
        self.assertEqual(args['reply_parameters'].message_id, 44)
        self.assertEqual(args['chat_id'], '123')
        self.assertTrue(args['disable_notification'])
        self.assertEqual(self.bot.send_message.await_args.kwargs['text'], self.text)
        self.assertNotIn('reply_markup', args)

    async def test_album_and_second_day_retry_do_not_repeat_first_or_text(self):
        self.assertTrue(await self.send(images=self.images * 2))
        self.bot.send_media_group.assert_awaited_once()
        retry = TelegramRetryAfter(method=SendPhoto(chat_id=123, photo='file'), message='rate limited', retry_after=1)
        self.bot.send_photo.side_effect = [retry, SimpleNamespace(message_id=48)]
        self.assertFalse(await self.send(key='notice:grid:tomorrow'))
        self.assertFalse(await self.send(key='notice:grid:tomorrow'))
        self.assertEqual(self.bot.send_photo.await_count, 1)
        self.journal.db.execute('UPDATE deliveries SET retry_at=0')
        self.journal.db.commit()
        self.assertTrue(await self.send(images=self.images * 2))
        self.assertTrue(await self.send(key='notice:grid:tomorrow'))
        self.assertEqual(self.bot.send_media_group.await_count, 1)
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual(self.bot.send_photo.await_count, 2)

    async def test_ambiguous_or_permanent_error_never_retried(self):
        for i, error in enumerate([ConnectionError(), TelegramBadRequest(method=SendPhoto(chat_id=123, photo='file'), message='no photos')]):
            self.bot.send_photo.side_effect = error
            self.assertTrue(await self.send(key=f'error{i}'))
            self.assertTrue(await self.send(key=f'error{i}'))
        self.assertEqual(self.bot.send_photo.await_count, 2)

    async def test_expired_retry_finishes_without_send(self):
        self.bot.send_photo.side_effect = TelegramRetryAfter(method=SendPhoto(chat_id=123, photo='file'), message='rate limited', retry_after=3600)
        self.assertFalse(await self.send())
        self.journal.db.execute('UPDATE deliveries SET expires=0')
        self.journal.db.commit()
        self.assertTrue(await self.send())
        self.assertEqual(self.bot.send_photo.await_count, 1)

    async def test_test_route_cannot_reply_to_production_parent(self):
        self.service._test_chat_id = '-100999'
        self.assertTrue(await self.send())
        self.bot.send_photo.assert_not_awaited()
        self.parent = await self.service.send_message('test', delivery_key='test')
        self.assertTrue(await self.send())
        self.assertEqual(self.bot.send_photo.await_args.kwargs['chat_id'], '-100999')

    async def test_success_survives_restart(self):
        self.assertTrue(await self.send())
        self.journal.db.close()
        self.journal = DeliveryJournal(Path(self.tmp.name) / 'journal.db')
        self.service.journal = self.journal
        self.assertTrue(await self.send())
        self.assertEqual(self.bot.send_photo.await_count, 1)

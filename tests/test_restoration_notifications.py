"""Early, silent schedule ETA and complete-only power duration presentation."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_emergency_feature as fixtures
from features.emergency import EmergencyFeature
from features.schedule_change import ScheduleChangeFeature
from schedule_notifications import schedule_signature
from schedule_restoration import changed_restoration_end
from template_engine import TemplateEngine
from utils import TZ_KYIV


class RestorationNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f, self.ha, self.tg, self.state = fixtures.EmergencyFeatureTest()._build_feature()
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.f.templates = TemplateEngine(Path(folder.name))
        self.now = datetime(2026, 11, 6, 10, 36, tzinfo=TZ_KYIV)
        self.clock = patch('features.emergency.now_kyiv', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.addAsyncCleanup(self.f.on_stop)
        self.state.save = AsyncMock()
        self.tg.send_message = AsyncMock(return_value=31)
        self.ha.states[self.f.entity('status')] = {'state': 'stabilization'}
        self.ha.states[self.f.entity('outage_start')] = {'state': self.at(8, 1)}
        self.ha.states[self.f.entity('outage_end')] = {'state': self.at(12)}
        self.f.state_set('last_emergency_status', 'stabilization')
        self.f.state_set('last_outage_end', self.at(12))
        self.groups = ['GPV3.1']
        self.group_state = {'state': 'GPV3.1', 'attributes': {
            'schedule_groups': self.groups,
            'schedule_notifications_ready': True,
            'address_verified': True,
            'provider_id': 'odesa', 'address_id': 'building',
            'schedule_snapshot_id': 'new',
            'schedule_coverage': [{'group_id': 'GPV3.1', 'kind': 'outage',
                'start': self.at(0), 'end': self.at(0, day=8)}],
        }}
        self.ha.states[self.f.entity('schedule_group')] = self.group_state
        await self.f.read_groups(require_schedule=True)
        self.old = [self.event(8, 12)]
        self.new = [self.event(8, 11)]
        self.sender = SimpleNamespace(_verified_calendar=(tuple(self.groups), 'new', ('odesa', 'building')))

    def at(self, hour, minute=0, day=6):
        return self.now.replace(day=day, hour=hour, minute=minute).isoformat()

    def event(self, start, end, group='GPV3.1'):
        return {'start': self.at(start), 'end': self.at(end), 'description': 'Planned; group='+group}

    async def announce(self):
        return await self.f.on_schedule_notification(self.sender, self.new, self.old)

    async def operator_update(self, end, reason=False):
        self.f._pending_update = {'end_changed': True, 'end_old': self.at(12), 'end_new': end,
                                  'reason_changed': reason, 'reason_new': 'Changed reason'}
        await self.f._flush_pending_update()

    async def test_schedule_early_eta_is_silent_and_late_operator_echo_is_suppressed(self):
        self.assertTrue(await self.announce())
        call = self.tg.send_message.call_args
        self.assertIn('за графіком', call.args[0])
        self.assertIn('06.11.2026 11:00', call.args[0])
        self.assertTrue(call.kwargs['disable_notification'])
        await self.operator_update(self.at(11))
        self.assertEqual(self.tg.send_message.await_count, 1)
        await self.operator_update(self.at(13))
        self.assertEqual(self.tg.send_message.await_count, 2)
        self.assertTrue(self.tg.send_message.call_args.kwargs['disable_notification'])

    async def test_duplicate_is_suppressed_after_restart_and_timezone_normalization(self):
        await self.announce()
        restarted = EmergencyFeature(self.f.config, self.ha, self.tg, self.state, self.f.templates)
        self.f = restarted
        await self.f.on_start()
        await self.operator_update('2026-11-06T09:00:00Z')
        self.assertEqual(self.tg.send_message.await_count, 1)

    async def test_actual_new_outage_with_same_end_is_not_suppressed(self):
        await self.announce()
        self.ha.states[self.f.entity('outage_start')] = {'state': self.at(9)}
        await self.operator_update(self.at(11))
        self.assertEqual(self.tg.send_message.await_count, 2)

    async def test_matching_eta_does_not_suppress_real_reason_change(self):
        await self.announce()
        await self.operator_update(self.at(11), reason=True)
        self.assertIn('Changed reason', self.tg.send_message.call_args.args[0])
        self.assertNotIn('Новий орієнтовний час', self.tg.send_message.call_args.args[0])

    async def test_operator_end_only_is_silent_even_without_schedule(self):
        await self.operator_update(self.at(14))
        self.assertTrue(self.tg.send_message.call_args.kwargs['disable_notification'])

    async def test_failed_send_does_not_acknowledge_and_retries(self):
        self.tg.send_message.return_value = None
        self.assertFalse(await self.announce())
        self.assertIsNone(self.f.state_get('announced_restoration'))
        key = self.tg.send_message.call_args.kwargs['delivery_key']
        self.tg.send_message.return_value = 31
        self.assertTrue(await self.announce())
        self.assertEqual(key, self.tg.send_message.call_args.kwargs['delivery_key'])
        self.assertTrue(await self.announce())
        self.assertEqual(self.tg.send_message.await_count, 2)

    async def test_concurrent_callbacks_send_one_eta(self):
        await asyncio.gather(self.announce(), self.announce())
        self.assertEqual(self.tg.send_message.await_count, 1)

    async def test_emergency_repairs_unknown_or_ok_do_not_borrow_hourly_eta(self):
        for status in ('emergency', 'planned', 'unknown', 'unavailable', 'ok'):
            self.ha.states[self.f.entity('status')] = {'state': status}
            self.assertTrue(await self.announce())
        self.tg.send_message.assert_not_awaited()

    async def test_does_not_repeat_an_already_current_operator_eta(self):
        self.ha.states[self.f.entity('outage_end')] = {'state': self.at(11)}
        await self.announce()
        self.tg.send_message.assert_not_awaited()

    async def test_new_tomorrow_schedule_does_not_repeat_today_eta(self):
        self.new[0]['end'] = self.at(12)
        self.new.append({'start': self.at(8, day=7), 'end': self.at(10, day=7),
                         'description': 'Planned; group=GPV3.1'})
        await self.announce()
        self.tg.send_message.assert_not_awaited()

    async def test_extension_just_after_previous_eta_is_announced(self):
        self.old[0]['end'] = self.at(10, 30)
        await self.announce()
        self.tg.send_message.assert_awaited_once()

    async def test_missing_or_unknown_current_interval_is_not_a_restoration_promise(self):
        self.new = []
        await self.announce()
        self.tg.send_message.assert_not_awaited()

    async def test_revision_or_outage_change_during_persistence_prevents_stale_send(self):
        for change in ('revision', 'status', 'source'):
            with self.subTest(change=change):
                self.ha.states[self.f.entity('status')] = {'state': 'stabilization'}
                self.group_state['attributes']['schedule_snapshot_id'] = 'new'
                self.group_state['attributes']['address_id'] = 'building'
                async def save():
                    if change == 'status':
                        self.ha.states[self.f.entity('status')] = {'state': 'ok'}
                    else:
                        self.group_state['attributes']['schedule_snapshot_id' if change == 'revision' else 'address_id'] = 'changed'
                self.state.save.side_effect = save
                self.assertFalse(await self.announce())
        self.tg.send_message.assert_not_awaited()

    async def test_schedule_text_then_eta_then_images(self):
        cfg = self.f.config
        cfg.schedule_change = SimpleNamespace(enabled=True, silent=False)
        cfg.group_change = SimpleNamespace(enabled=False)
        schedule = ScheduleChangeFeature(cfg, self.ha, self.tg, self.state, self.f.templates)
        schedule.set_emergency(self.f)
        schedule._fetch_outage_events = AsyncMock(return_value=self.new)
        schedule.state_set('schedule_signature', schedule_signature(self.old))
        schedule.state_set('schedule_baseline', {'events': self.old, 'at': self.now.isoformat()})
        order = []
        async def send(text, **kwargs):
            order.append('eta' if 'Час відновлення за графіком' in text else 'schedule')
            return len(order)
        self.tg.send_message.side_effect = send
        async def attach(*args):
            order.append('images')
            return True
        with patch('features.schedule_change.now_kyiv', return_value=self.now), patch('outage_calendar.now_kyiv', return_value=self.now), patch('schedule_attachments.attach_schedule', side_effect=attach):
            await schedule.on_tick()
        self.assertEqual(order, ['schedule', 'eta', 'images'])

    async def test_incomplete_or_ambiguous_coverage_has_no_eta(self):
        for end in (self.at(11), self.at(10)):
            self.group_state['attributes']['schedule_coverage'][0]['end'] = end
            self.assertIsNone(changed_restoration_end(self.new, self.old, self.groups, self.group_state, self.now))
        legacy = deepcopy(self.group_state)
        del legacy['attributes']['schedule_coverage']
        self.assertIsNone(changed_restoration_end(self.new, self.old, self.groups, legacy, self.now))

    async def test_multiple_groups_must_agree_but_selected_group_can_be_used(self):
        self.groups.append('GPV5.1')
        self.group_state['attributes']['schedule_coverage'].append({
            'group_id':'GPV5.1','kind':'outage','start':self.at(0),'end':self.at(0,day=8)})
        self.old.append(self.event(8, 12, 'GPV5.1'))
        self.new.append(self.event(8, 13, 'GPV5.1'))
        self.assertIsNone(changed_restoration_end(self.new, self.old, self.groups, self.group_state, self.now))
        self.assertEqual(changed_restoration_end(self.new[:1], self.old[:1], ['GPV3.1'], self.group_state, self.now).hour, 11)
        self.new[-1]['end'] = self.at(11)
        self.assertEqual(changed_restoration_end(self.new, self.old, self.groups, self.group_state, self.now).hour, 11)

    async def test_continuous_midnight_slices_merge_to_the_actual_end(self):
        now = self.now.replace(hour=23)
        def span(start, end):
            return {'start': start, 'end': end, 'description':'Planned; group=GPV3.1'}
        old = [span(self.at(22), self.at(0,day=7)), span(self.at(0,day=7),self.at(2,day=7))]
        new = [span(self.at(22), self.at(0,day=7)), span(self.at(0,day=7),self.at(3,day=7))]
        self.assertEqual(changed_restoration_end(new,old,self.groups,self.group_state,now).hour,3)
        self.group_state['attributes']['schedule_coverage'][0]['end'] = self.at(0,day=7)
        self.assertIsNone(changed_restoration_end(new[:1],old,self.groups,self.group_state,now))


class PowerDurationTemplateTests(unittest.TestCase):
    def test_missing_duration_omits_entire_line_and_preserves_timestamp_and_phases(self):
        with TemporaryDirectory() as folder:
            templates = TemplateEngine(Path(folder))
            for name in ('power_restored', 'power_lost'):
                for house in ('on', 'partial'):
                    context = dict(display_name='Example', house_state=house, duration=None,
                                   missing_phases=['L2'] if house == 'partial' else [], unknown_phases=[],
                                   timestamp='06.11.2026 11:00')
                    text = templates.render(name, **context)
                    self.assertNotIn('світла було', text.lower())
                    self.assertNotIn('світло було', text.lower())
                    self.assertNotIn('None', text)
                    self.assertNotIn('Template error', text)
                    self.assertIn('\n🕒 06.11.2026 11:00', text)
                    if house == 'partial' and name == 'power_restored':
                        self.assertIn('Відсутні фази: L2', text)
                    context['duration'] = '2 год 7 хв'
                    text = templates.render(name, **context)
                    self.assertIn('було: 2 год 7 хв.\n', text)

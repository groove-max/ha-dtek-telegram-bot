"""Voltage incident acknowledgement, slow delivery and per-recipient recovery."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_voltage_feature as fixtures
from delivery_journal import DeliveryJournal
from features.voltage import VoltageFeature
from state_store import StateStore
from telegram_service import TelegramService
from template_engine import TemplateEngine
from config import VoltageConfig


class VoltageDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f, self.ha, _, _ = fixtures.VoltageFeatureTest()._build_feature()
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.journal = DeliveryJournal(Path(self.folder.name)/'telegram.db')
        self.addCleanup(self.journal.close)
        self.store = StateStore(Path(self.folder.name)/'state.json')
        self.addAsyncCleanup(self.store.flush)
        self.bot = SimpleNamespace(send_message=AsyncMock(side_effect=self.accept))
        with patch('telegram_service.Bot', return_value=self.bot):
            self.tg = TelegramService('123:fake', '123', journal=self.journal)
        self.f.tg, self.f.state = self.tg, self.store
        self.addAsyncCleanup(self.stop)
        self.entity = fixtures.VoltageFeatureTest.voltage_entity

    def accept(self, **kwargs):
        mid = self.bot.send_message.await_count
        return SimpleNamespace(message_id=mid, model_dump=lambda **kwargs: {'message_id':mid})

    async def stop(self):
        await self.f.on_stop()

    def put(self, voltage, entity=None):
        entity = entity or self.entity
        sample = {'state': str(voltage), 'last_updated': datetime.now(timezone.utc).isoformat()}
        self.ha.states[entity] = sample
        return sample

    async def observe(self, voltage, entity=None):
        entity = entity or self.entity
        await self.f.on_state_change(entity, {}, self.put(voltage, entity))

    async def bad(self, voltage=190):
        await self.observe(voltage)
        task = self.f._pending_alerts.get(self.entity)
        if task:
            await task

    def texts(self):
        return [c.kwargs['text'].split('|')[0] for c in self.bot.send_message.await_args_list]

    async def test_new_reading_does_not_cancel_warning_while_telegram_is_slow(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def slow(**kwargs):
            started.set()
            await release.wait()
            return self.accept(**kwargs)
        self.bot.send_message.side_effect = slow
        await self.observe(190)
        warning = self.f._pending_alerts[self.entity]
        await asyncio.wait_for(started.wait(), 1)
        next_sample = asyncio.create_task(self.observe(191))
        await asyncio.sleep(0)
        self.assertFalse(warning.done())
        release.set()
        await warning
        await next_sample
        await self.observe(202)
        await self.observe(202.6)
        self.assertEqual(self.texts(), ['voltage_low','voltage_normal'])

    async def test_uncertain_warning_does_not_produce_an_orphan_normal_message(self):
        self.bot.send_message.side_effect = TimeoutError()
        await self.bad()
        await self.observe(191)
        self.bot.send_message.side_effect = self.accept
        await self.observe(202)
        await self.observe(202.6)
        self.assertEqual(self.texts(), ['voltage_low'])
        self.assertFalse(self.f.state_get(self.f._alert_keys(self.entity)[0]))

    async def test_definite_failure_retries_same_incident_with_fresh_value_then_normalizes_once(self):
        class TelegramServerError(Exception): pass
        self.bot.send_message.side_effect = TelegramServerError()
        await self.bad()
        await self.observe(191)
        self.assertEqual(self.bot.send_message.await_count,1)
        self.journal.db.execute('UPDATE deliveries SET retry_at=0')
        self.journal.db.commit()
        self.bot.send_message.side_effect = self.accept
        await self.observe(192)
        await self.observe(202)
        await self.observe(202.6)
        self.assertEqual(self.texts(), ['voltage_low','voltage_low','voltage_normal'])
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0],2)

    async def test_new_sample_invalidates_delayed_warning_before_send(self):
        self.store.save = AsyncMock(side_effect=lambda: self.put(230))
        await self.bad()
        self.bot.send_message.assert_not_awaited()
        await self.observe(230)
        self.bot.send_message.assert_not_awaited()

    async def test_opposite_extreme_is_not_normalization(self):
        await self.bad(190)
        await self.bad(270)
        await self.observe(230)
        self.assertEqual(self.texts(), ['voltage_low','voltage_high','voltage_normal'])

    async def test_normal_message_identity_survives_restart_and_value_change(self):
        await self.bad()
        original = self.f._clear_alert_state
        def interrupted(entity):
            raise RuntimeError('simulate interruption after Telegram acknowledgement')
        self.f._clear_alert_state = interrupted
        with self.assertRaises(RuntimeError):
            await self.observe(202)
        await self.store.flush()
        await self.f.on_stop()
        loaded = StateStore(self.store._path)
        loaded.load()
        self.addAsyncCleanup(loaded.flush)
        self.f = VoltageFeature(self.f.config,self.ha,self.tg,loaded,self.f.templates)
        await self.observe(202.6)
        self.assertEqual(self.texts(), ['voltage_low','voltage_normal'])
        self.assertFalse(self.f.state_get(self.f._alert_keys(self.entity)[0]))

    async def test_confirmed_warning_is_recovered_from_journal_after_state_save_gap(self):
        await self.bad()
        key = f'voltage_incident_{self.entity}'
        self.f.state_set(key, {**self.f.state_get(key), 'notified':False})
        await self.observe(202)
        self.assertEqual(self.texts(), ['voltage_low','voltage_normal'])

    async def test_ambiguous_normal_delivery_is_not_repeated_and_does_not_hide_next_incident(self):
        await self.bad()
        self.bot.send_message.side_effect = TimeoutError()
        await self.observe(202)
        self.bot.send_message.side_effect = self.accept
        await self.observe(202.6)
        await self.bad(190)
        self.assertEqual(self.texts(), ['voltage_low','voltage_normal','voltage_low'])

    async def test_normal_sound_is_separate_from_low_high_alert_sound(self):
        self.assertTrue(VoltageConfig().normal_silent)
        await self.bad()
        await self.observe(202)
        self.assertEqual([c.kwargs['disable_notification'] for c in self.bot.send_message.await_args_list],[False,True])
        self.f.config.voltage.silent = True
        self.f.config.voltage.normal_silent = False
        await self.bad()
        await self.observe(202)
        self.assertEqual([c.kwargs['disable_notification'] for c in self.bot.send_message.await_args_list][-2:],[True,False])

    async def test_normal_retry_uses_same_identity_and_is_not_replayed_by_generic_queue(self):
        await self.bad()
        class TelegramServerError(Exception): pass
        self.bot.send_message.side_effect = TelegramServerError()
        await self.observe(202)
        await self.observe(202.6)
        self.assertEqual(self.bot.send_message.await_count,2)
        self.journal.db.execute('UPDATE deliveries SET retry_at=0')
        self.journal.db.commit()
        self.bot.send_message.side_effect = self.accept
        await self.tg.retry_pending(self.store)
        self.assertEqual(self.bot.send_message.await_count,2)
        await self.observe(203)
        await self.observe(204)
        self.assertEqual(self.texts(), ['voltage_low','voltage_normal','voltage_normal'])
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0],2)

    async def test_legacy_confirmed_warning_can_still_normalize(self):
        await self.tg.send_message('old alert',delivery_key='old:1',context={
            'feature':'voltage','stream':self.entity,'address':self.f.profile_key,
            'guards':{f'voltage_observation_{self.entity}':'low'}})
        self.f.state_set('delivery_identity_voltage','old')
        self.f.state_set('delivery_revision_voltage',1)
        self.f.state_set(self.f._alert_keys(self.entity)[0],True)
        self.f.state_set(self.f._alert_keys(self.entity)[1],'low')
        await self.observe(202)
        self.assertEqual(self.texts(), ['old alert','voltage_normal'])

    async def test_legacy_unconfirmed_flag_does_not_send_normal(self):
        self.f.state_set(self.f._alert_keys(self.entity)[0],True)
        self.f.state_set(self.f._alert_keys(self.entity)[1],'low')
        await self.observe(202)
        self.bot.send_message.assert_not_awaited()

    async def test_single_sensor_has_no_phase_name_and_multi_phase_keeps_names(self):
        self.f.templates = TemplateEngine(Path(self.folder.name)/'templates')
        self.f.config.voltage.entities[0].label = 'Мережа'
        self.f = VoltageFeature(self.f.config,self.ha,self.tg,self.store,self.f.templates)
        await self.bad()
        await self.observe(202)
        for call in self.bot.send_message.await_args_list:
            self.assertNotIn('на фазі',call.kwargs['text'])
            self.assertNotIn('Мережа',call.kwargs['text'])
        self.assertIn('✅ Напруга в нормі: 202.0 В',self.bot.send_message.await_args.kwargs['text'])
        self.f.config.voltage.entities[0].label = 'L1'
        self.f.config.voltage.entities.append(SimpleNamespace(entity='sensor.second',label='L2'))
        self.f = VoltageFeature(self.f.config,self.ha,self.tg,self.store,self.f.templates)
        await self.bad()
        await self.observe(202)
        self.assertIn('на фазі L1',self.bot.send_message.await_args.kwargs['text'])

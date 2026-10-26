"""Notifications use current evidence even if persistence or delivery was slow."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from test_power_presence_feature import DummyHA, DummyState, DummyTelegram, DummyTemplates
from config import AddressConfig
from features.power_presence import PowerPresenceFeature
from power_monitor import PowerMonitor


class PowerNotificationCurrentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.time = 0
        self.base = datetime(2026, 10, 26, 8, tzinfo=timezone.utc)
        self.patches = [patch(module + '.now_kyiv', self.now) for module in (
            'power_monitor', 'power_sources', 'features.power_presence', 'features.base')]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def now(self):
        return self.base + timedelta(seconds=self.time)

    def build(self, mode='sources', kinds=('voltage',), previous='on'):
        power = dict(enabled=True, mode=mode, loss_delay=10, restore_delay=30,
                     telemetry_notifications=False)
        if mode == 'sources':
            power['sources'] = [dict(entity=('binary_sensor.' if k == 'binary' else 'sensor.') + f'input_{i}', kind=k)
                                for i, k in enumerate(kinds)]
        elif mode == 'loss_plus_voltage':
            power.update(loss_entity='sensor.input_1', loss_state='<1')
        self.config = AddressConfig(entity_prefix='example', power=power,
            voltage=dict(entities=[dict(entity='sensor.input_0')]))
        self.ha, self.store, self.tg = DummyHA({}), DummyState(), DummyTelegram()
        self.ha.is_connected = True
        self.ha.connected_at = self.base - timedelta(seconds=1)
        self.monitor = PowerMonitor(self.config, self.ha, self.store)
        self.feature = PowerPresenceFeature(address_config=self.config, ha_client=self.ha,
            telegram=self.tg, state_store=self.store, templates=DummyTemplates())
        self.feature.set_power_monitor(self.monitor)
        self.feature._monotonic = lambda: self.time
        self.feature._arm = lambda deadlines: None
        self.store.set('example', 'power_state', previous)
        self.store.set('example', 'power_last_confirmed_state', previous)
        self.addAsyncCleanup(self.feature.on_stop)

    def report(self, index, value, *, binary=False):
        entity = ('binary_sensor.' if binary else 'sensor.') + f'input_{index}'
        self.ha.states[entity] = dict(state=str(value), last_reported=self.now().isoformat())

    async def test_loss_is_cancelled_if_power_returns_while_saving(self):
        for mode in ('sources', 'voltage_only', 'loss_plus_voltage'):
            with self.subTest(mode=mode):
                self.time = 0
                self.build(mode)
                self.report(0, 0)
                self.report(1, 0)
                await self.feature.on_tick()
                self.time = 10

                async def save():
                    self.report(0, 230)
                    self.report(1, 50)

                self.store.save = save
                await self.feature.on_tick()
                self.assertEqual(self.tg.messages, [])
                self.assertEqual(self.store.get('example', 'power_last_confirmed_state'), 'on')
                self.time = 50
                await self.feature.on_tick()
                self.assertEqual(self.tg.messages, [])

    async def test_restore_is_cancelled_if_pair_disagrees_while_saving(self):
        for kinds, positive in ((('voltage', 'voltage'), (230, 231)),
                                (('frequency', 'frequency'), (50, 50)),
                                (('voltage', 'frequency'), (230, 50))):
            with self.subTest(kinds=kinds):
                self.time = 0
                self.build(kinds=kinds, previous='off')
                for i, v in enumerate(positive):
                    self.report(i, v)
                await self.feature.on_tick()
                self.time = 30
                for i, v in enumerate(positive):
                    self.report(i, v)

                async def save():
                    self.report(1, 0)

                self.store.save = save
                await self.feature.on_tick()
                self.assertEqual(self.tg.messages, [])
                self.assertEqual(self.store.get('example', 'power_state'), 'unknown')
                self.assertEqual(self.store.get('example', 'power_last_confirmed_state'), 'off')

    async def test_single_numeric_restore_needs_a_new_report_after_delay(self):
        for mode in ('sources', 'voltage_only'):
            with self.subTest(mode=mode):
                self.time = 0
                self.build(mode, previous='off')
                self.report(0, 230)
                await self.feature.on_tick()
                self.time = 30
                await self.feature.on_tick()
                self.assertEqual(self.tg.messages, [])
                self.report(0, 230)
                await self.feature.on_tick()
                self.assertEqual(len(self.tg.messages), 1)
                self.assertTrue(self.tg.messages[0].startswith('power_restored|'))

    async def test_event_driven_binary_does_not_need_periodic_reports(self):
        self.build(kinds=('binary',), previous='off')
        self.report(0, 'on', binary=True)
        await self.feature.on_tick()
        self.time = 30
        await self.feature.on_tick()
        self.assertEqual(len(self.tg.messages), 1)
        self.assertTrue(self.tg.messages[0].startswith('power_restored|'))

    async def test_failed_loss_is_not_retried_after_recovery(self):
        self.build()
        self.report(0, 0)
        await self.feature.on_tick()
        self.time = 10

        async def failure(*args, **kwargs):
            return None

        with patch.object(self.tg, 'send_message', failure):
            await self.feature.on_tick()
        self.report(0, 230)
        await self.feature.on_tick()
        self.time = 40
        self.report(0, 230)
        await self.feature.on_tick()
        self.assertEqual(self.tg.messages, [])
        self.assertEqual(self.store.get('example', 'power_state'), 'on')

"""Replay slow, conflicting meters without HA, Telegram writes or real clocks."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_power_presence_feature import DummyHA, DummyState, DummyTelegram, DummyTemplates
from config import AddressConfig
from features.power_presence import PowerPresenceFeature
from power_monitor import PowerMonitor


class PowerRestoreEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def replay(self, mode, timeline):
        base = datetime(2026, 10, 25, 7, 50, tzinfo=timezone.utc)
        seconds = 0
        now = lambda: base + timedelta(seconds=seconds)
        power = dict(enabled=True, mode=mode, loss_delay=10, restore_delay=30,
                     confirm_timeout=15, telemetry_notifications=False)
        if mode == "sources":
            power['sources'] = [
                dict(entity='sensor.grid', kind='frequency', max_age_seconds=150),
                dict(entity='sensor.voltage', kind='voltage', max_age_seconds=150,
                     availability='mains_powered'),
            ]
        else:
            power.update(loss_entity='sensor.grid', loss_state='<1')
        config = AddressConfig(entity_prefix='example', power=power,
            voltage=dict(entities=[dict(entity='sensor.voltage')], max_age_seconds=150))
        ha = DummyHA({})
        ha.is_connected = True
        ha.connected_at = base - timedelta(seconds=1)
        store, tg = DummyState(), DummyTelegram()
        monitor = PowerMonitor(config, ha, store)
        monitor._loss_fresh_after = ha.connected_at
        feature = PowerPresenceFeature(address_config=config, ha_client=ha,
            telegram=tg, state_store=store, templates=DummyTemplates())
        feature.set_power_monitor(monitor)
        feature._arm = lambda deadlines: None
        feature._monotonic = lambda: seconds
        store.set('example', 'power_state', 'on')
        store.set('example', 'power_last_confirmed_state', 'on')
        patches = [patch(module+'.now_kyiv', now) for module in
                   ('power_monitor', 'power_sources', 'features.power_presence')]
        patches.append(patch('power_sources.monotonic', lambda: seconds))
        for p in patches:p.start()
        results = {}
        try:
            for seconds, reports in timeline:
                for entity, value in reports.items():
                    ha.states['sensor.'+entity] = dict(state=str(value), last_reported=now().isoformat())
                await feature.on_tick()
                results[seconds] = (store.get('example','power_state'),
                                    [m.split('|')[0] for m in tg.messages])
        finally:
            await feature.on_stop()
            for p in reversed(patches):p.stop()
        return results

    async def test_one_positive_cycle_between_losses_does_not_announce_restore(self):
        # Meters report roughly every 51 and 60 seconds. The first apparent
        # restoration lasts one report only; then the sources disagree.
        timeline = [(0,dict(grid=50,voltage=227)),(50,dict(grid=0)),
                    (64,dict(voltage=0)),(74,{}),(101,dict(grid=50)),
                    (120,dict(voltage=233)),(150,{}),(153,dict(grid=0)),
                    (178,{}),(180,dict(voltage=232)),(205,dict(grid=0)),
                    (240,dict(voltage=231)),(256,dict(grid=0)),
                    (300,dict(voltage=233)),(308,dict(grid=0)),
                    (359,dict(grid=0)),(360,dict(voltage=235)),
                    (411,dict(grid=0)),(420,dict(voltage=232)),
                    (462,dict(grid=50)),(486,dict(voltage=230)),(492,{}),
                    (514,dict(grid=50)),(546,dict(voltage=231))]
        for mode in ('loss_plus_voltage','sources'):
            with self.subTest(mode=mode):
                result = await self.replay(mode,timeline)
                self.assertEqual(result[74][1], ['power_lost'])
                for t in (150,178,180,205,420,492,514):
                    self.assertEqual(result[t][1], ['power_lost'], (mode,t))
                self.assertEqual(result[546], ('on',['power_lost','power_restored']))

    async def test_normal_report_interval_is_not_disappearance(self):
        for mode in ('loss_plus_voltage','sources'):
            with self.subTest(mode=mode):
                result = await self.replay(mode,[(0,dict(grid=50,voltage=230)),
                    (10,dict(grid=0)),(25,{}),(35,{}),(60,dict(voltage=231)),
                    (70,dict(grid=0)),(120,dict(voltage=232))])
                self.assertTrue(all(not messages for _,messages in result.values()))
                self.assertEqual(result[120][0],'unknown')

    async def test_expired_silent_secondary_still_confirms_real_loss(self):
        for mode in ('loss_plus_voltage','sources'):
            with self.subTest(mode=mode):
                result = await self.replay(mode,[(0,dict(grid=50,voltage=230)),
                    (10,dict(grid=0)),(25,{}),(35,{}),(100,dict(grid=0)),
                    (151,dict(grid=0)),(161,{})])
                self.assertEqual(result[35][1],[])
                self.assertEqual(result[161],('off',['power_lost']))

    async def test_fresh_contradiction_cannot_later_age_into_outage(self):
        for mode in ('loss_plus_voltage','sources'):
            with self.subTest(mode=mode):
                result = await self.replay(mode,[(0,dict(grid=50,voltage=230)),
                    (10,dict(grid=0)),(11,dict(voltage=231)),(35,{}),
                    (100,dict(grid=0)),(170,dict(grid=0)),(195,{}),(205,{})])
                self.assertTrue(all(not messages for _,messages in result.values()))

    async def test_restore_needs_current_independent_witness(self):
        for mode in ('loss_plus_voltage','sources'):
            with self.subTest(mode=mode):
                result = await self.replay(mode,[(0,dict(grid=50,voltage=230)),
                    (1,dict(grid=0,voltage=0)),(11,{}),
                    (20,dict(grid='unavailable',voltage=231)),(50,dict(voltage=230)),
                    (100,dict(grid=50)),(130,dict(grid=50,voltage=232))])
                self.assertEqual(result[50][1],['power_lost'])
                self.assertEqual(result[130],('on',['power_lost','power_restored']))

    async def test_repeated_hour_does_not_make_an_old_voltage_sample_fresh(self):
        now = datetime(2026,10,25,3,15,tzinfo=ZoneInfo('Europe/Kyiv'),fold=1)
        ha = DummyHA({'sensor.voltage':dict(state='230',last_reported='2026-10-25T00:14:00+00:00')})
        config = AddressConfig(entity_prefix='example',power=dict(enabled=True,mode='voltage_only'),
            voltage=dict(entities=[dict(entity='sensor.voltage')],max_age_seconds=150))
        monitor = PowerMonitor(config,ha,DummyState())
        with patch('power_monitor.now_kyiv',lambda:now):
            self.assertEqual(await monitor.detect_current_power(),'unknown')
        # The report only looks one minute old on the wall clock; 61 minutes
        # have elapsed. A new report in the repeated hour remains usable.
        ha.states['sensor.voltage']['last_reported']='2026-10-25T01:14:00+00:00'
        with patch('power_monitor.now_kyiv',lambda:now):
            self.assertEqual(await monitor.detect_current_power(),'on')

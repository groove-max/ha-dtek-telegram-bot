"""Replay independent grid sources without real HA, clocks or Telegram writes."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rootfs/app'))
from config import AddressConfig, PowerConfig, PowerSourceConfig
from power_sources import PowerSources, observation
from power_monitor import PowerMonitor
from features.power_presence import PowerPresenceFeature
from test_power_presence_feature import DummyHA, DummyState, DummyTelegram, DummyTemplates
from pydantic import ValidationError


class PowerSourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.base = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
        self.seconds = 0
        self.ha = DummyHA({})
        self.ha.is_connected = True
        self.patches = [patch('power_sources.now_kyiv', self.now),
                        patch('power_sources.monotonic', lambda: self.seconds),
                        patch('power_monitor.now_kyiv', self.now),
                        patch('features.power_presence.now_kyiv', self.now)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])

    def now(self):
        return self.base + timedelta(seconds=self.seconds)

    def source(self, name='a', kind='voltage', **kwargs):
        return PowerSourceConfig(entity=f"{'binary_sensor' if kind == 'binary' else 'sensor'}.{name}", kind=kind, **kwargs)

    def put(self, source, value, age=0, **kwargs):
        self.ha.states[source.entity] = {'state': value, 'last_updated': (self.now() - timedelta(seconds=age)).isoformat(), **kwargs}

    def engine(self, *sources):
        return PowerSources(PowerConfig(enabled=True, mode='sources', sources=list(sources)), self.ha)

    async def test_single_source_of_each_kind_and_polarity(self):
        for kind, present, absent in [('voltage','170','0'), ('frequency','50','0'), ('binary','on','off')]:
            for polarity in (['on','off'] if kind == 'binary' else ['on']):
                with self.subTest(kind=kind, polarity=polarity):
                    s = self.source(kind=kind, present_state=polarity)
                    if polarity == 'off':
                        present, absent = absent, present
                    e = self.engine(s)
                    for value, expected in [(present,'on'),(absent,'off'),('unavailable','unknown'),('unknown','unknown')]:
                        self.put(s, value)
                        self.assertEqual((await e.snapshot())['state'], expected)

    async def test_all_pairs_are_same_line_and_never_partial(self):
        values = {'voltage': ('230','0'), 'frequency': ('50','0'), 'binary': ('on','off')}
        for left in values:
            for right in values:
                a,b = self.source('a',left),self.source('b',right)
                e = self.engine(a,b)
                for av,bv,expected in [(0,0,'on'),(1,1,'off'),(0,1,'unknown'),(1,0,'unknown')]:
                    with self.subTest(left=left,right=right,av=av,bv=bv):
                        self.put(a,values[left][av]); self.put(b,values[right][bv])
                        self.assertEqual((await e.snapshot())['state'], expected)
                self.put(b,'unavailable')
                self.assertEqual((await e.snapshot())['state'], 'unknown')

    async def test_binary_unchanged_for_days_is_valid_but_periodic_reports_expire(self):
        for kind in ('binary','voltage','frequency'):
            s = self.source(kind=kind)
            self.put(s, {'binary':'on','voltage':'230','frequency':'50'}[kind], age=86400)
            self.assertEqual((await self.engine(s).snapshot())['state'], 'on' if kind=='binary' else 'unknown')
        s = self.source(kind='binary',freshness='heartbeat')
        self.put(s,'on',age=181)
        self.assertEqual((await self.engine(s).snapshot())['state'],'unknown')

    async def test_invalid_and_restored_measurements_do_not_prove_outage(self):
        s = self.source()
        e = self.engine(s)
        for value in ('NaN','inf','-1','bad',''):
            self.put(s,value)
            self.assertEqual((await e.snapshot())['state'],'unknown')
        for extra in ({'attributes':{'restored': True}}, {'last_updated':None},
                      {'last_updated':(self.now()+timedelta(seconds=30)).isoformat()},
                      {'attributes':{'unit_of_measurement':'kV'}}):
            self.put(s,'0',**extra)
            self.assertEqual((await e.snapshot())['state'],'unknown')
        self.ha.states.clear()
        self.assertEqual((await e.snapshot())['state'],'unknown')

    async def test_last_reported_not_last_changed_controls_measurements(self):
        s = self.source()
        self.put(s,'230',age=900,last_reported=self.now().isoformat(),last_changed=(self.now()-timedelta(days=10)).isoformat())
        self.assertEqual((await self.engine(s).snapshot())['state'],'on')

    async def test_mains_powered_secondary_can_go_silent_after_loss(self):
        for secondary_kind in ('voltage','frequency','binary'):
            for unavailable in (False, True):
                with self.subTest(kind=secondary_kind, unavailable=unavailable):
                    self.seconds=0
                    a,b=self.source(), self.source('b',secondary_kind,availability='mains_powered', freshness='heartbeat', max_age_seconds=20)
                    e=self.engine(a,b)
                    self.put(a,'230'); self.put(b,{'voltage':'230','frequency':'50','binary':'on'}[secondary_kind])
                    self.assertEqual((await e.snapshot())['state'],'on')
                    self.seconds=10; self.put(a,'0')
                    if unavailable: self.put(b,'unavailable')
                    self.assertEqual((await e.snapshot())['state'],'unknown')
                    self.seconds=24
                    self.assertEqual((await e.snapshot())['state'],'unknown')
                    self.seconds=25
                    result=await e.snapshot()
                    self.assertEqual(result['state'],'off')
                    self.assertEqual(result['sources'][1]['reason'],'silent_after_confirmed_loss')

    async def test_disappearance_before_loss_cannot_corroborate_it(self):
        a,b=self.source(),self.source('b',availability='mains_powered')
        e=self.engine(a,b)
        self.put(a,'230'); self.put(b,'230'); await e.snapshot()
        self.seconds=2; self.put(b,'unavailable'); await e.snapshot()
        self.seconds=10; self.put(a,'0'); await e.snapshot()
        self.seconds=30
        self.assertEqual((await e.snapshot())['state'],'unknown')

    async def test_fresh_contradiction_cannot_be_aged_into_an_outage(self):
        a,b=self.source(),self.source('b',availability='mains_powered')
        e=self.engine(a,b)
        self.put(a,'230'); self.put(b,'230'); await e.snapshot()
        self.seconds=10; self.put(a,'0'); await e.snapshot()
        self.seconds=11; self.put(b,'230'); await e.snapshot()
        self.seconds=40
        self.assertEqual((await e.snapshot())['state'],'unknown')
        self.put(b,'0')
        self.assertEqual((await e.snapshot())['state'],'off')

    async def test_always_on_secondary_disappearing_never_means_zero(self):
        a,b=self.source(),self.source('b')
        e=self.engine(a,b)
        self.put(a,'230');self.put(b,'230');await e.snapshot()
        self.seconds=10; self.put(a,'0');self.put(b,'unavailable');await e.snapshot()
        self.seconds=40
        self.assertEqual((await e.snapshot())['state'],'unknown')

    async def test_restart_disconnect_and_reconnect_clear_inference(self):
        a,b=self.source(),self.source('b',availability='mains_powered')
        e=self.engine(a,b)
        self.put(a,'230');self.put(b,'230');await e.snapshot()
        self.seconds=10; self.put(a,'0');self.put(b,'unavailable');await e.snapshot()
        self.seconds=30
        self.assertEqual((await e.snapshot())['state'],'off')
        self.assertEqual((await self.engine(a,b).snapshot())['state'],'unknown')
        self.ha.is_connected=False
        self.assertEqual((await e.snapshot())['state'],'unknown')
        self.ha.is_connected=True; self.ha.connected_at=self.now()
        self.assertEqual((await e.snapshot())['state'],'unknown')
        self.seconds=31; self.put(a,'230');self.put(b,'230')
        self.assertEqual((await e.snapshot())['state'],'on')

    async def test_mqtt_requires_live_agreement(self):
        s=self.source(mqtt_topic='meter/grid')
        self.put(s,'0')
        self.ha.mqtt_observation=lambda _:None
        e=self.engine(s)
        self.assertEqual((await e.snapshot())['state'],'unknown')
        self.ha.mqtt_observation=lambda _:{'state':'230','last_updated':self.now().isoformat()}
        self.assertEqual((await e.snapshot())['state'],'unknown')
        self.ha.mqtt_observation=lambda _:self.ha.states[s.entity]
        self.assertEqual((await e.snapshot())['state'],'off')

    async def test_primary_binary_is_independent_of_quality_voltage(self):
        a=self.source(kind='binary')
        config=AddressConfig(entity_prefix='demo',power={'enabled':True,'mode':'sources','sources':[a]},voltage={'enabled':True,'entities':[{'entity':'sensor.quality'}]})
        self.put(a,'on');self.ha.states['sensor.quality']={'state':'0','last_updated':self.now().isoformat()}
        pm=PowerMonitor(config,self.ha,DummyState())
        self.assertEqual(await pm.detect_current_power(),'on')
        self.put(a,'off');self.ha.states['sensor.quality']['state']='230'
        self.assertEqual(await pm.detect_current_power(),'off')

    async def test_notification_delays_do_not_restart_and_false_dips_cancel(self):
        a=self.source(kind='binary')
        config=AddressConfig(entity_prefix='demo',power={'enabled':True,'mode':'sources','sources':[a],'loss_delay':10,'restore_delay':20})
        store,tg=DummyState(),DummyTelegram()
        pm=PowerMonitor(config,self.ha,store)
        f=PowerPresenceFeature(address_config=config,ha_client=self.ha,telegram=tg,state_store=store,templates=DummyTemplates())
        f.set_power_monitor(pm);f._monotonic=lambda:self.seconds;f._arm=lambda _:None
        store.set('demo','power_state','on');store.set('demo','power_last_confirmed_state','on')
        self.put(a,'on');await f.on_tick()
        self.seconds=1;self.put(a,'off');await f.on_tick()
        self.seconds=5;self.put(a,'on');await f.on_tick()
        self.seconds=12;await f.on_tick()
        self.assertEqual(tg.messages,[])
        for t in range(20,31):
            self.seconds=t;self.put(a,'off');await f.on_tick()
        self.assertEqual(len(tg.messages),1);self.assertIn('power_lost',tg.messages[0])
        self.seconds=31;self.put(a,'on');await f.on_tick()
        self.seconds=50;await f.on_tick();self.assertEqual(len(tg.messages),1)
        self.seconds=51;await f.on_tick();self.assertIn('power_restored',tg.messages[1])
        await f.on_stop()

    def test_configuration_guards_and_legacy_roundtrip(self):
        invalid = [[], [self.source(availability='mains_powered')], [self.source(),self.source()]]
        for sources in invalid:
            with self.assertRaises(ValidationError):self.engine(*sources)
        for params in ({'kind':'frequency','freshness':'state'}, {'kind':'voltage','present_above':float('nan')}, {'kind':'voltage','mqtt_topic':'x/#'}):
            with self.assertRaises(ValidationError):self.source(**params)
        config=AddressConfig(entity_prefix='demo', power={'enabled':True,'mode':'loss_plus_voltage','loss_entity':'sensor.grid','loss_state':'0','restore_delay':30},voltage={'entities':[{'entity':'sensor.v'}]})
        copy=AddressConfig.model_validate(config.model_dump())
        self.assertEqual(config,copy)
        self.assertEqual(copy.power.mode,'loss_plus_voltage')
        self.assertEqual(copy.power.restore_delay,30)

    async def test_primary_change_between_rest_reads_cannot_confirm_old_state(self):
        a,b=self.source(),self.source('b')
        self.put(a,'0');self.put(b,'0')
        count=0
        async def get_state(entity):
            nonlocal count
            if entity==a.entity:
                count+=1
                if count==2:self.put(a,'230')
            return self.ha.states[entity]
        self.ha.get_state=get_state
        result=await self.engine(a,b).snapshot()
        self.assertEqual(result['state'],'unknown')
        self.assertEqual(result['reason'],'sources_changed')

    async def test_binary_mqtt_heartbeat_expires_and_numeric_format_matches(self):
        s=self.source(kind='binary',mqtt_topic='grid/state')
        self.put(s,'on',age=181)
        self.ha.mqtt_observation=lambda _:self.ha.states[s.entity]
        self.assertEqual((await self.engine(s).snapshot())['state'],'unknown')
        s=self.source(mqtt_topic='grid/voltage')
        self.put(s,'230')
        self.ha.mqtt_observation=lambda _:{'state':'230.0','last_updated':self.now().isoformat()}
        self.assertEqual((await self.engine(s).snapshot())['state'],'on')

    async def test_missing_rest_result_cannot_corroborate_loss(self):
        a,b=self.source(),self.source('b',availability='mains_powered')
        e=self.engine(a,b)
        self.put(a,'230');self.put(b,'230');await e.snapshot()
        self.seconds=10;self.put(a,'0');await e.snapshot()
        self.seconds=40;del self.ha.states[b.entity]
        self.assertEqual((await e.snapshot())['state'],'unknown')

    def test_legacy_binary_loss_condition_is_supported(self):
        from utils import parse_condition
        self.assertTrue(parse_condition('off','off'))
        self.assertTrue(parse_condition('on','on'))
        self.assertFalse(parse_condition('off','unavailable'))
        self.assertFalse(parse_condition('0','unknown'))

    async def test_mains_meter_zero_then_silence_does_not_erase_confirmed_outage(self):
        a,b=self.source(),self.source('b',availability='mains_powered',max_age_seconds=30)
        e=self.engine(a,b)
        self.put(a,'230');self.put(b,'230');await e.snapshot()
        self.seconds=10;self.put(a,'0');self.put(b,'0')
        self.assertEqual((await e.snapshot())['state'],'off')
        self.seconds=50;self.put(a,'0')
        self.assertEqual((await e.snapshot())['state'],'off')
        # Returning city input alone does not establish whole-pair recovery.
        self.seconds=51;self.put(a,'230')
        self.assertEqual((await e.snapshot())['state'],'unknown')
        self.put(b,'230')
        self.assertEqual((await e.snapshot())['state'],'on')

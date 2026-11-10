"""Profile inheritance and optional live telemetry must preserve outage delivery."""
import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rootfs/app'))
from config import AddressConfig
from template_engine import TemplateEngine
from template_sensors import dependencies, sensor_context
from features.power_presence import PowerPresenceFeature
from state_store import StateStore


class TemplateSensorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = TemplateEngine(Path(self.tmp.name) / 'templates')
        self.ha = Mock(get_state=AsyncMock(return_value={
            'state': '85', 'attributes': {'unit_of_measurement': '%', 'friendly_name': 'Battery'}
        }))

    async def render(self, source):
        return self.engine.render_source(source, **await sensor_context(source, self.ha))

    async def test_repeated_entity_is_fetched_once_and_supports_filters_attributes_conditions(self):
        source = "{% if has_value('sensor.battery') %}🔋 {{ states('sensor.battery') | float | round(1) }}{{ state_attr('sensor.battery', 'unit_of_measurement') }}{% endif %}"
        self.assertEqual(await self.render(source), '🔋 85.0%')
        self.ha.get_state.assert_awaited_once_with('sensor.battery')
        self.ha.get_state.return_value = {'state': 'on', 'attributes': {}}
        self.assertEqual(await self.render("{{ is_state('binary_sensor.grid', 'on') }}"), 'True')

    async def test_absent_unknown_and_failed_sensor_never_become_zero(self):
        for value in (None, {'state': 'unknown'}, {'state': 'unavailable'}):
            self.ha.get_state.return_value = value
            self.assertEqual(await self.render("{{ states('sensor.battery') }}"), 'немає даних')
            self.assertEqual(await self.render("{{ has_value('sensor.battery') }}"), 'False')
            self.assertEqual(await self.render("{{ state_attr('sensor.battery', 'unit_of_measurement', '—') }}"), '—')
        self.ha.get_state.side_effect = ConnectionError()
        self.assertEqual(await self.render("Outage. {{ states('sensor.battery', '—') }}"), 'Outage. —')

    async def test_sensor_html_and_template_text_are_not_executed(self):
        self.ha.get_state.return_value = {'state': '<b>{{ 7*7 }}</b>', 'attributes': {'label': '<a href="x">test</a>'}}
        result = await self.render("{{ states('sensor.text') }} / {{ state_attr('sensor.text', 'label') }}")
        self.assertIn('<b>{{ 7*7 }}</b>', result)
        self.assertIn('<a href="x">test</a>', result)
        self.assertNotIn('49', result)

    async def test_slow_sensor_is_cancelled_without_suppressing_message(self):
        async def slow(_):
            await asyncio.sleep(30)
        self.ha.get_state.side_effect = slow
        timeout = asyncio.timeout
        with patch('template_sensors.asyncio.timeout', side_effect=lambda _: timeout(.01)):
            self.assertEqual(await self.render("Outage. {{ states('sensor.battery') }}"), 'Outage. немає даних')

    async def test_preview_and_runtime_use_same_profile_template_and_sensor_values(self):
        from main import Orchestrator
        address = AddressConfig(entity_prefix='a', display_name='Apartment')
        self.engine.save_override('power_lost', "{{ display_name }} 🔋 {{ states('sensor.battery') }}%", profile_id='a')
        app = Mock(spec=Orchestrator)
        app.templates = self.engine
        app.ha = self.ha
        app._get_address_by_index.return_value = address
        app._profile_for.return_value = Mock(test_mode=False, chat_id='123')
        app._build_template_context = AsyncMock(return_value={'display_name': 'Apartment'})
        preview = await Orchestrator.preview_template(app, Mock(), template_name='power_lost', address_index=0)
        feature = PowerPresenceFeature(address, self.ha, Mock(), StateStore(Path(self.tmp.name)/'state.json'), self.engine)
        self.assertEqual(preview['rendered'], await feature.async_render('power_lost'))
        self.assertEqual(preview['template']['origin'], 'profile')

    async def test_plain_templates_do_not_fetch_ha(self):
        self.assertEqual(await self.render('Hello {{ 2 + 3 }}'), 'Hello 5')
        self.ha.get_state.assert_not_awaited()

    def test_validation_blocks_dynamic_ids_and_public_greeting_sensor_access(self):
        for source in ("{{ states(entity) }}", "{{ states('switch.test') }}", "{{ states('../secret') }}", '{{ broken'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.engine.save_override('status_on', source)
        with self.assertRaises(ValueError):
            self.engine.save_override('welcome', "{{ states('sensor.battery') }}")
        with self.assertRaises(ValueError):
            self.engine.save_override('welcome', 'Hello', profile_id='a')
        with self.assertRaises(ValueError):
            dependencies(' '.join("{{ states('sensor.s%d') }}" % n for n in range(33)))

    def test_profile_overrides_inherit_and_reset_without_touching_other_profiles(self):
        builtin = self.engine.get_template_details('status_on')['source']
        self.engine.save_override('status_on', 'Shared')
        self.engine.save_override('status_on', 'Apartment A', profile_id='a')
        self.assertEqual(self.engine.render('status_on', profile_id='a'), 'Apartment A')
        self.assertEqual(self.engine.render('status_on', profile_id='b'), 'Shared')
        self.engine.save_override('status_on', 'New shared')
        self.assertEqual(self.engine.render('status_on', profile_id='a'), 'Apartment A')
        self.assertEqual(self.engine.render('status_on', profile_id='b'), 'New shared')
        reloaded = TemplateEngine(self.engine.templates_dir)
        self.assertEqual(reloaded.render('status_on', profile_id='a'), 'Apartment A')
        self.engine.delete_override('status_on', profile_id='a')
        self.assertTrue(self.engine.get_template_details('status_on', profile_id='a')['inherited'])
        self.assertEqual(self.engine.render('status_on', profile_id='a'), 'New shared')
        self.engine.delete_override('status_on')
        self.assertEqual(self.engine.get_template_details('status_on', profile_id='a')['source'], builtin)

    def test_all_personal_recipients_keep_one_template_scope(self):
        profile = AddressConfig(profile_id='apartment', mode='personal', account_entity='sensor.account', telegram_user_ids=[11,22], telegram_user_id=11)
        routes = [profile.for_user(user) for user in [11,22]]
        self.assertNotEqual(routes[0].key, routes[1].key)
        self.assertEqual([r.template_scope for r in routes], ['apartment','apartment'])
        self.assertNotIn('template_profile_id', routes[0].model_dump())

    async def test_auxiliary_values_do_not_change_notification_identity(self):
        self.engine.save_override('power_lost', "Outage {{ states('sensor.battery') }}", profile_id='a')
        f = PowerPresenceFeature(AddressConfig(entity_prefix='a'), self.ha, Mock(), StateStore(Path(self.tmp.name)/'state.json'), self.engine)
        self.assertEqual(await f.async_render('power_lost'), 'Outage 85')
        identity = f._delivery_key
        self.ha.get_state.return_value = {'state': '84', 'attributes': {}}
        self.assertEqual(await f.async_render('power_lost'), 'Outage 84')
        self.assertEqual(f._delivery_key, identity)

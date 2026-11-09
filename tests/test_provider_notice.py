"""Only unambiguous fresh regional warnings suspend forecasts, never measured power."""
import aiogram
import jinja2
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rootfs/app'))
from provider_notice import applicability, warning_text, warning_html, append_warning, warning, caption_notice, configured_grid, forecast_suspended
from config import AddressConfig, ProviderNoticeConfig
from types import SimpleNamespace
from schedule_image_state import image_content_hash
from schedule_pages import select_page, render_page
from schedule_restoration import changed_restoration_end
from features.upcoming_outage import UpcomingOutageFeature
from features.status_message import StatusMessageFeature
import test_schedule_upgrade as fixtures
from test_grid_members import large_grid


def notice(now, value=True):
    return dict(schedule_applies=value, schedule_applicability_date=now.date().isoformat(),
                provider_notice_state='emergency' if value is False else 'stabilization',
                provider_notice_revision=str(value),
                provider_notice_text='Діють екстрені відключення.' if value is False else 'Оголошення щодо відключень.',
                provider_notice_valid_until=(now+timedelta(minutes=10)).isoformat(),
                provider_notice_checked_at=now.isoformat())


class ContractTests(unittest.TestCase):
    def test_legacy_expiry_and_other_day_do_not_imply_clearance(self):
        now=fixtures.NOW
        self.assertIsNone(applicability({},now=now))
        attrs=notice(now)
        self.assertTrue(applicability(attrs,now=now))
        self.assertIsNone(applicability(attrs,now=now+timedelta(minutes=10)))
        self.assertIsNone(applicability(attrs,now=now,at=now+timedelta(days=1)))
        for value in (None,False):
            self.assertIs(applicability(notice(now,value),now=now),value)
        for expiry in ('', 'invalid', '2026-11-08T12:00:00',None):
            self.assertIsNone(applicability({**attrs,'provider_notice_valid_until':expiry},now=now))

    def test_warning_escapes_bounded_provider_text(self):
        attrs={**notice(fixtures.NOW,False),'provider_notice_text':'<b>ДТЕК & X</b>'+ 'a'*1000}
        value=warning_html(attrs,now=fixtures.NOW)
        self.assertIn('&lt;b&gt;',value)
        self.assertNotIn('<b>',value)
        self.assertIn('Графік може не відповідати',value)
        self.assertLess(len(value),700)

    def test_long_plain_template_preserves_text_and_bounded_notice(self):
        source='https://www.dtek-oem.com.ua/ua/shutdowns'
        value=append_warning('Дім <A & B> '+('😀 '*3000), {**notice(fixtures.NOW,False), 'provider_notice_source':source},now=fixtures.NOW)
        self.assertIn('Графік може не відповідати',value)
        self.assertTrue(value.startswith('Дім <A & B> '))
        self.assertTrue(value.endswith(source))
        self.assertNotIn('<a ',value)
        self.assertNotIn('&amp;',value)
        self.assertLessEqual(len(value.encode('utf-16-le'))//2,4096)

    def test_plain_and_html_notice_use_their_own_link_format(self):
        source='https://www.dtek-oem.com.ua/ua/shutdowns?one=1&two=2'
        attrs={**notice(fixtures.NOW,None),'provider_notice_source':source}
        text=warning_text(attrs,now=fixtures.NOW)
        self.assertTrue(text.endswith('\n'+source))
        self.assertNotIn('<a ',text)
        self.assertIn('one=1&amp;two=2',warning_html(attrs,now=fixtures.NOW))
        # Templates may choose the location of the supplied notice variable.
        custom='Дім <A & B>'+text+'\nДякуємо!'
        self.assertEqual(append_warning(custom,attrs,now=fixtures.NOW),custom)
        attrs['provider_notice_source']=source+'x'*4096
        bounded=append_warning('😀'*4000,attrs,now=fixtures.NOW)
        self.assertLessEqual(len(bounded.encode('utf-16-le'))//2,4096)
        self.assertNotIn(source,bounded)

    def test_neutral_empty_failed_and_safe_source_presentation(self):
        now=fixtures.NOW
        attrs={**notice(now,None),'provider_notice_source':'https://www.dtek-dnem.com.ua/ua/shutdowns'}
        rendered=warning_html(attrs,now=now)
        self.assertIn('Є оголошення ДТЕК',rendered)
        self.assertIn('<a href="https://www.dtek-dnem.com.ua/ua/shutdowns">',rendered)
        self.assertNotIn('призупинено',rendered)
        self.assertNotIn('гік не діє',rendered)
        self.assertIn('https://www.dtek-dnem.com.ua/ua/shutdowns',caption_notice(attrs,now=now))
        self.assertEqual(warning({**attrs,'provider_notice_state':'none','provider_notice_text':''},now=now),'')
        for value in ({'provider_notice_error':'failed'}, {'provider_notice_valid_until':now.isoformat()}):
            failed=warning_html({**attrs,**value},now=now)
            self.assertIn('Не вдалося перевірити',failed)
            self.assertNotIn('Оголошення щодо відключень.',failed)
        for url in ('javascript:alert(1)','https://www.dtek-dnem.com.ua.evil.test/', 'https://user@www.dtek-dnem.com.ua/'):
            self.assertNotIn('<a ',warning_html({**attrs,'provider_notice_source':url},now=now))

    def test_profile_config_defaults_shortcut_roundtrip_and_image_isolation(self):
        old=AddressConfig(entity_prefix='old')
        self.assertTrue(old.provider_notice.enabled)
        disabled=AddressConfig(entity_prefix='disabled',provider_notice=False)
        self.assertFalse(disabled.provider_notice.enabled)
        custom=AddressConfig(entity_prefix='custom',provider_notice={'show_in_status':False,'pause_forecasts':False})
        self.assertEqual(AddressConfig.model_validate(custom.model_dump()).provider_notice,custom.provider_notice)
        self.assertFalse(forecast_suspended(notice(fixtures.NOW,False),custom,now=fixtures.NOW))
        self.assertFalse(forecast_suspended(notice(fixtures.NOW,False),disabled,now=fixtures.NOW))
        self.assertTrue(forecast_suspended(notice(fixtures.NOW,False),old,now=fixtures.NOW))
        grid=large_grid(3);grid['days'][0].update(notice(fixtures.NOW,False))
        original=deepcopy(grid)
        custom.provider_notice.show_on_images=False
        filtered=configured_grid(grid,custom)
        self.assertEqual(grid,original)
        self.assertNotIn('provider_notice_text',filtered['days'][0])
        self.assertIn('provider_notice_text',configured_grid(grid,old)['days'][0])
        self.assertEqual(filtered['days'][0]['rows'],grid['days'][0]['rows'])

    def test_schedule_restoration_is_not_predicted_from_suspended_timetable(self):
        now=fixtures.NOW
        group=fixtures.group_state()
        group['attributes'].update(notice(now,False))
        self.assertIsNone(changed_restoration_end([],[],['GPV5.1'],group,now))

    def test_photo_caption_uses_selected_date_without_extending_todays_emergency(self):
        now=fixtures.NOW
        attrs=notice(now,False)
        attrs['date']=(now+timedelta(days=1)).date().isoformat()
        attrs['schedule_applicability_days']={now.date().isoformat():False, attrs['date']:None}
        self.assertNotIn('Графік може не відповідати',caption_notice(attrs,now=now))
        attrs['schedule_applicability_days']={now.date().isoformat():None,attrs['date']:False}
        self.assertIn('Графік може не відповідати',caption_notice(attrs,now=now))

    def test_image_changes_on_notice_but_not_successful_check_timestamp(self):
        data=large_grid(3);day=data['days'][0]
        day.update(notice(datetime.fromisoformat(day['date']).replace(tzinfo=fixtures.TZ_KYIV)))
        before=image_content_hash(data,0,True)
        day['provider_notice_checked_at']='changed';day['provider_notice_valid_until']='changed'
        self.assertEqual(before,image_content_hash(data,0,True))
        day['schedule_applies']=False
        self.assertNotEqual(before,image_content_hash(data,0,True))

    def test_warning_adds_space_in_png_preserving_table_rows(self):
        from io import BytesIO
        from PIL import Image
        data=large_grid(3);day=data['days'][0]
        now=datetime.fromisoformat(day['date']).replace(hour=12,tzinfo=fixtures.TZ_KYIV)
        plain=Image.open(BytesIO(render_page(select_page(data),now=now)))
        day.update(notice(now,False))
        warned=Image.open(BytesIO(render_page(select_page(data),now=now)))
        self.assertEqual(warned.width,plain.width)
        self.assertEqual(warned.height,plain.height+48)


class ForecastTests(unittest.IsolatedAsyncioTestCase):
    feature=fixtures.ScheduleUpgradeRuntimeTests.feature

    async def asyncSetUp(self):
        await fixtures.ScheduleUpgradeRuntimeTests.asyncSetUp(self)
        patcher=patch('provider_notice.now_kyiv',side_effect=lambda:self.now)
        patcher.start();self.addCleanup(patcher.stop)

    async def test_personal_and_building_suppress_only_explicit_warning(self):
        for mode in ('personal','building'):
            self.base.config.mode=mode
            f=self.feature(UpcomingOutageFeature)
            self.snapshot['attributes'].update(notice(self.now,False))
            await f.on_tick()
            self.assertEqual(self.telegram.messages,[])
            self.assertFalse(f.state_get('warned_outages'))
        self.snapshot['attributes'].update(notice(self.now,None))
        await f.on_tick()
        self.assertEqual(len(self.telegram.messages),1)
        # A successful recheck of the same notice must not send a second alert.
        self.snapshot['attributes']['provider_notice_checked_at']=(self.now+timedelta(seconds=5)).isoformat()
        await f.on_tick()
        self.assertEqual(len(self.telegram.messages),1)

    async def test_change_after_calendar_read_aborts_before_telegram(self):
        f=self.feature(UpcomingOutageFeature)
        self.snapshot['attributes'].update(notice(self.now))
        async def suspend(): self.snapshot['attributes'].update(notice(self.now,False))
        self.state.save.side_effect=suspend
        with self.assertRaises(ValueError): await f.on_tick()
        self.assertEqual(self.telegram.messages,[])
        self.assertFalse(f.state_get('warned_outages'))

    async def test_expiry_during_persistence_does_not_suspend_schedule(self):
        f=self.feature(UpcomingOutageFeature)
        attrs=notice(self.now);attrs['provider_notice_valid_until']=(self.now+timedelta(seconds=1)).isoformat()
        self.snapshot['attributes'].update(attrs)
        async def advance(): self.now+=timedelta(seconds=2)
        self.state.save.side_effect=advance
        await f.on_tick()
        self.assertEqual(len(self.telegram.messages),1)

    async def test_status_warning_keeps_actual_power_and_removes_prediction(self):
        self.snapshot['attributes'].update(notice(self.now,False))
        f=self.feature(StatusMessageFeature)
        # No sensor inference is changed by a provider notice.
        value=await f._build_status_text('on')
        self.assertIn('Графік може не відповідати',value)
        self.assertIn('прогноз призупинено',value)
        self.assertNotIn('23:00',value)

    async def test_unverified_notice_does_not_remove_status_prediction(self):
        self.snapshot['attributes'].update(notice(self.now,None))
        f=self.feature(StatusMessageFeature)
        value=await f._build_status_text('on')
        self.assertNotIn('прогноз призупинено',value)
        self.assertIn('23:00',value)
        self.assertIn('Є оголошення ДТЕК',value)

    async def test_status_creation_and_edit_send_plain_notice_without_tags(self):
        source='https://www.dtek-oem.com.ua/ua/shutdowns'
        self.snapshot['attributes'].update(notice(self.now,None),provider_notice_source=source)
        self.base.config.status_message=AddressConfig(entity_prefix='example').status_message
        self.base.config.status_message.pin=False
        self.telegram.send_message=AsyncMock(return_value=44)
        self.telegram.edit_message_result=AsyncMock(return_value='ok')
        feature=self.feature(StatusMessageFeature)
        await feature._update_status_message()
        await feature._update_status_message()
        self.telegram.send_message.assert_awaited_once()
        self.telegram.edit_message_result.assert_awaited_once()
        for call,index in ((self.telegram.send_message.call_args,0), (self.telegram.edit_message_result.call_args,1)):
            text=call.args[index]
            self.assertTrue(text.endswith(source))
            self.assertNotIn('<a ',text)
            self.assertNotIn('&lt;a',text)
            self.assertIsNone(call.kwargs.get('parse_mode'))

    async def test_schedule_and_group_custom_templates_keep_plain_text_and_one_notice(self):
        from features.schedule_change import ScheduleChangeFeature
        from features.group_change import GroupChangeFeature
        source='https://www.dtek-oem.com.ua/ua/shutdowns'
        self.snapshot['attributes'].update(notice(self.now,None),provider_notice_source=source)
        for cls,name in ((ScheduleChangeFeature,'schedule_change'),(GroupChangeFeature,'group_change')):
            feature=self.feature(cls)
            await feature.read_groups()
            self.base.templates.save_override(name,'Дім <A & B>{{ provider_notice }}\nКінець')
            text=feature.render(name)
            self.assertTrue(text.startswith('Дім <A & B>'))
            self.assertEqual(text.count(source),1)
            self.assertNotIn('<a ',text)
            self.assertTrue(text.endswith('Кінець'))

    async def test_admin_switches_control_runtime_status_and_final_forecast_delivery(self):
        self.snapshot['attributes'].update(notice(self.now,False))
        self.base.config.provider_notice=ProviderNoticeConfig(show_in_status=False,pause_forecasts=False)
        status=self.feature(StatusMessageFeature)
        value=await status._build_status_text('on')
        self.assertNotIn('ДТЕК повідомляє',value)
        self.assertIn('23:00',value)
        upcoming=self.feature(UpcomingOutageFeature)
        await upcoming.on_tick()
        self.assertEqual(len(self.telegram.messages),1)

    async def test_schedule_text_setting_is_independent_from_status(self):
        from features.schedule_change import ScheduleChangeFeature
        self.snapshot['attributes'].update(notice(self.now,None))
        self.base.config.provider_notice=ProviderNoticeConfig(show_in_schedule_messages=False)
        feature=self.feature(ScheduleChangeFeature)
        await feature.read_groups()
        self.assertNotIn('Є оголошення ДТЕК',feature.render('schedule_change',schedule_lines=['Дані графіка']))
        status=self.feature(StatusMessageFeature)
        self.assertIn('Є оголошення ДТЕК',await status._build_status_text('on'))

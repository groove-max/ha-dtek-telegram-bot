"""Live membership gates reuse navigation without granting cabinet or delivery access."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rootfs/app'))
import test_personal_profiles as fixtures
from config import AddressConfig, FullConfig, delivery_profiles
from bot_profiles import BotProfileStore, validate_destinations
from ha_client import HACommandError
from telegram_commands import commands_for
from test_grid_members import large_grid


class BuildingAccessTests(unittest.IsolatedAsyncioTestCase):
    message = fixtures.ConversationTests.message
    callback = fixtures.ConversationTests.callback

    async def asyncSetUp(self):
        await fixtures.ConversationTests.asyncSetUp(self)
        self.house = AddressConfig(entity_prefix='house_a', display_name='Будинок A',
                                   telegram_chat_id='-100123', member_access=True)
        self.app.config.addresses = [self.house]
        self.app.render_status = AsyncMock(return_value='⚡ Будинок A · Живлення є')
        self.status = 'member'
        self.own_status = 'administrator'
        self.bot.get_chat_member = AsyncMock(side_effect=self.check_member)
        self.bot.send_photo = AsyncMock(return_value=SimpleNamespace(message_id=102))

    async def check_member(self, chat_id, user_id):
        return SimpleNamespace(status=self.own_status if user_id == self.bot.id else self.status, is_member=True)

    async def incoming(self, update):
        # Each scenario models a new interaction after the anti-spam interval.
        with self.journal.db:
            self.journal.db.execute('DELETE FROM interactions')
        await self.router.handle(update)

    async def test_member_gets_private_menu_and_same_status_without_new_delivery_route(self):
        original = deepcopy(self.app.config.model_dump())
        routes = list(delivery_profiles(self.app.config))
        await self.incoming(self.message(text='/start'))
        buttons = self.bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard
        labels = [b.text for row in buttons for b in row]
        self.assertIn('⚡️ Стан', labels)  # Even when the group's status is pinned.
        self.assertFalse(any('показ' in b.lower() or 'документ' in b.lower() for b in labels))
        button = next(b for row in buttons for b in row if b.text == '⚡️ Стан')
        await self.incoming(self.callback(button.callback_data, user=777))
        self.app.render_status.assert_awaited_once_with(self.house)
        self.assertEqual(self.bot.send_message.call_args.kwargs['chat_id'], 777)
        self.assertEqual(self.app.config.model_dump(), original)
        self.assertEqual(list(delivery_profiles(self.app.config)), routes)
        self.assertEqual(self.bot.get_chat_member.await_count, 4)
        self.app.ha.command.assert_not_awaited()

    async def test_statuses_and_bot_administrator_requirement_fail_closed(self):
        for status, included in [('creator',True),('administrator',True),('member',True),('restricted',True),('left',False),('kicked',False)]:
            self.status = status
            await self.router.members.refresh(777)
            self.assertEqual(bool(self.router.profiles(777)),included,status)
        self.bot.get_chat_member.side_effect = lambda **kw: SimpleNamespace(status='administrator' if kw['user_id']==self.bot.id else 'restricted',is_member=False)
        await self.router.members.refresh(777)
        self.assertEqual(self.router.profiles(777), [])
        self.bot.get_chat_member.side_effect = self.check_member
        self.status, self.own_status = 'member', 'member'
        await self.router.members.refresh(777)
        self.assertEqual(self.router.profiles(777), [])

    async def test_old_button_cannot_be_used_after_leaving_or_telegram_failure(self):
        for failure in ('left', 'network'):
            self.status = 'member'
            self.bot.get_chat_member.side_effect = self.check_member
            await self.incoming(self.message())
            button = self.router.keyboard(777,self.house,[('⚡ Стан','status',None)]).inline_keyboard[0][0]
            if failure == 'left': self.status = 'left'
            else: self.bot.get_chat_member.side_effect = TimeoutError()
            await self.incoming(self.callback(button.callback_data,user=777))
            self.assertEqual(self.router.profiles(777), [])
            self.app.render_status.assert_not_awaited()
            self.app.ha.command.assert_not_awaited()

    async def test_disabling_remapping_and_other_bot_do_not_reuse_grants(self):
        await self.router.members.refresh(777)
        self.assertEqual(self.router.profiles(777), [self.house])
        self.house.telegram_chat_id = '-100456'
        self.assertEqual(self.router.profiles(777), [])
        await self.router.members.refresh(777)
        self.house.entity_prefix = 'other_source'
        self.assertEqual(self.router.profiles(777), [])
        self.house.member_access = False
        self.bot.get_chat_member.reset_mock()
        await self.router.members.refresh(777)
        self.bot.get_chat_member.assert_not_awaited()
        self.house.member_access, self.house.telegram_bot_id = True, 'another_bot'
        await self.router.members.refresh(777)
        self.bot.get_chat_member.assert_not_awaited()

    async def test_private_test_route_and_disabled_receiver_never_grant_membership(self):
        for test_chat, receiving in [('777',True),('',False)]:
            self.tg._test_chat_id=test_chat
            self.app.config.receive_commands=receiving
            await self.router.members.refresh(777)
            self.assertEqual(self.router.profiles(777),[])
        self.bot.get_chat_member.assert_not_awaited()

    async def test_cabinet_actions_and_forged_buttons_are_rejected_for_members(self):
        await self.router.members.refresh(777)
        for action in ('readings','history','documents','document','readback','confirm','calibration_save','photos'):
            with self.assertRaises(HACommandError):
                await self.router.action(777,self.house,action,None)
        with self.assertRaises(HACommandError):
            await self.router.cabinet(self.house,777,'history')
        with self.assertRaises(HACommandError):
            await self.router.action(777,self.house,'command',('history',None))
        self.app.ha.command.assert_not_awaited()
        token=self.router.keyboard(777,self.house,[('⚡ Стан','status',None)]).inline_keyboard[0][0].callback_data
        await self.incoming(self.callback(token,user=888))
        self.app.render_status.assert_not_awaited()

    async def test_two_buildings_and_account_share_selector_and_capability_filter(self):
        second=self.house.model_copy(update={'entity_prefix':'house_b','display_name':'Будинок B','telegram_chat_id':'-100456'})
        self.app.config.addresses += [second,self.profile]
        await self.incoming(self.message(user=123,text='/status'))
        choices=self.bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual(len(choices),3)
        self.assertEqual(sum('Кабінет' in row[0].text for row in choices),1)
        chosen=next(row[0] for row in choices if 'Будинок B' in row[0].text)
        await self.incoming(self.callback(chosen.callback_data,user=123))
        self.app.render_status.assert_awaited_once_with(second)
        self.router.document_action=AsyncMock()
        await self.incoming(self.message(user=123,text='/documents'))
        self.router.document_action.assert_awaited_once_with(123,self.profile,'documents',None)

    async def test_one_chat_multiple_houses_is_checked_once_and_grants_both(self):
        self.app.config.addresses.append(self.house.model_copy(update={'entity_prefix':'house_b'}))
        await self.router.members.refresh(777)
        self.assertEqual(len(self.router.profiles(777)),2)
        self.assertEqual(self.bot.get_chat_member.await_count,2)

    async def test_partial_membership_filters_selector_without_leaking_other_addresses(self):
        self.app.config.addresses.append(self.house.model_copy(update={'entity_prefix':'secret','display_name':'Not allowed','telegram_chat_id':'-100999'}))
        async def lookup(chat_id,user_id):
            return SimpleNamespace(status='administrator' if user_id==self.bot.id else 'member' if chat_id=='-100123' else 'left')
        self.bot.get_chat_member.side_effect=lookup
        await self.incoming(self.message())
        self.assertEqual(self.router.profiles(777),[self.house])
        self.assertNotIn('Not allowed',str(self.bot.send_message.call_args))

    async def test_member_uses_public_grid_and_preserves_personal_reading_draft(self):
        self.app.config.addresses.append(self.profile)
        session={'profile':self.profile.key,'expires':__import__('time').time()+120,'values':{'GENERAL':'12345'}}
        self.router.sessions[123]=session
        self.app.ha.command.side_effect=[[{'entity':'binary_sensor.house_a_power','status_entity':'sensor.house_schedule'}],{},large_grid()]
        await self.router.members.refresh(123)
        await self.router.action(123,self.house,'grid',0)
        self.assertEqual([c.args[0] for c in self.app.ha.command.call_args_list], ['dtek_monitor/buildings','dtek_monitor/schedule_refresh','dtek_monitor/schedule_grid'])
        self.assertIs(self.router.sessions[123],session)
        self.assertEqual(self.bot.send_photo.call_args.kwargs['chat_id'],123)
        self.assertNotIn('👤 Моя група',str(self.bot.send_photo.call_args.kwargs['reply_markup']))

    async def test_public_native_menu_contains_only_read_only_actions(self):
        await self.router.sync_commands()
        self.assertIn('status',commands_for([],building_access=True))
        for call in self.bot.set_my_commands.call_args_list:
            commands=[c.command for c in call.kwargs['commands']]
            self.assertTrue({'today','tomorrow','status','accounts'} <= set(commands))
            self.assertFalse({'readings','documents','history'} & set(commands))
        self.assertEqual(self.router.profiles(777),[])

    async def test_configuration_rejects_private_destination_send_only_and_test_mode(self):
        cfg=FullConfig(telegram_bot_token='123456:TEST_TOKEN',addresses=[self.house])
        store=BotProfileStore(Path(self.tmp.name)/'bots.json')
        validate_destinations(cfg,store)
        for chat,receiving,test in [('123',True,''),('-100123',False,''),('-100123',True,'-100999')]:
            cfg.addresses[0].telegram_chat_id=chat;cfg.receive_commands=receiving;cfg.test_chat_id=test
            with self.assertRaises(ValueError):validate_destinations(cfg,store)
        with self.assertRaises(ValueError):fixtures.personal(member_access=True)

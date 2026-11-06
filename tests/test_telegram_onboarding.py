"""Profile invitations and native chooser security, lifecycle and permission checks."""
import json
from pathlib import Path
import sqlite3
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_personal_profiles as fixtures
from aiogram.types import Update
from connection_requests import ConnectionRequests
from main import Orchestrator
from telegram_onboarding import OnboardingStore, profile_binding, CANCEL_PICKER, USE_ME


class OnboardingStoreTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = OnboardingStore(self.db)

    def test_hash_only_storage_rotation_revocation_expiry_and_restart(self):
        token, row = self.store.create('invite', 'profile_a', 'binding')
        self.assertEqual(len('dtek_' + token), 37)
        self.assertNotIn(token, str(self.db.execute('SELECT * FROM onboarding_links').fetchall()))
        self.assertEqual(OnboardingStore(self.db).by_token(token)['id'], row['id'])
        token2, row2 = self.store.create('invite', 'profile_a', 'binding')
        self.assertIsNone(self.store.by_token(token))
        self.assertIsNotNone(self.store.by_token(token2))
        self.store.revoke(row2['id'])
        self.assertIsNone(self.store.by_token(token2))
        token3, _ = self.store.create('users', 'profile_a')
        with patch('telegram_onboarding.time.time', return_value=time.time() + 901):
            self.assertIsNone(self.store.by_token(token3))
        self.assertIsNone(self.store.by_token('invalid'))

    def test_picker_single_owner_kind_request_and_one_time_response(self):
        _, row = self.store.create('users', 'p')
        self.assertEqual(self.store.claim(row['id'], 123)['owner'], 123)
        self.assertIsNone(self.store.claim(row['id'], 456))
        self.assertIsNone(self.store.active(456, row['request_id']))
        self.assertFalse(self.store.finish(row['id'], 456, []))
        self.assertTrue(self.store.finish(row['id'], 123, [{'id': '123', 'type': 'private'}]))
        self.assertFalse(self.store.finish(row['id'], 123, []))
        self.assertIsNone(self.store.claim(row['id'], 123))

    def test_new_picker_cancels_previous_and_late_result_cannot_replace_it(self):
        _, first = self.store.create('users', 'a')
        self.store.claim(first['id'], 123)
        _, second = self.store.create('group', 'b')
        self.store.claim(second['id'], 123)
        self.assertIsNone(self.store.active(123, first['request_id']))
        self.assertIsNotNone(self.store.active(123, second['request_id']))
        self.store.revoke(second['id'])
        self.assertFalse(self.store.finish(second['id'], 123, []))

    def test_profile_requests_coexist_with_legacy_and_resolve_independently(self):
        requests = ConnectionRequests(self.db)
        sender = {'id': 123, 'first_name': 'Example'}
        for target in ('', 'one', 'two'):
            self.assertEqual(requests.request(sender, target, 'binding'), 'pending')
        rows = requests.pending()
        self.assertEqual(len(rows), 3)
        one = next(r for r in rows if r['profile_key'] == 'one')
        self.assertFalse(requests.resolve(123, 'accepted', one['revision'], 'two'))
        self.assertTrue(requests.resolve(123, 'accepted', one['revision'], 'one'))
        self.assertEqual(len(requests.pending()), 2)
        self.assertEqual(len(ConnectionRequests(self.db).pending()), 2)

    def test_new_invite_for_changed_source_replaces_stale_target_request(self):
        requests = ConnectionRequests(self.db)
        requests.request({'id':123},'profile','old_binding')
        old = requests.pending()[0]
        requests.request({'id':123},'profile','new_binding')
        new = requests.pending()[0]
        self.assertEqual(new['binding'],'new_binding')
        self.assertNotEqual(new['revision'],old['revision'])
        self.assertFalse(requests.resolve(123,'accepted',old['revision'],'profile'))

    def test_new_schema_preserves_existing_generic_request(self):
        self.db.execute('CREATE TABLE connection_requests (user_id INTEGER PRIMARY KEY,name TEXT,username TEXT,status TEXT,created REAL,changed REAL,revision TEXT)')
        self.db.execute('INSERT INTO connection_requests VALUES(?,?,?,?,?,?,?)', (123,'Legacy','','pending',time.time(),time.time(),'legacy-rev'))
        requests = ConnectionRequests(self.db)
        self.assertEqual(requests.pending()[0]['revision'], 'legacy-rev')
        self.assertTrue(requests.resolve(123,'declined','legacy-rev'))

    def test_store_limits_and_profile_identity_change(self):
        for i in range(200):
            self.store.create('users', f'p{i}')
        with self.assertRaises(ValueError):
            self.store.create('users', 'extra')
        with patch('telegram_onboarding.time.time',return_value=time.time()+2*86400):
            self.store.create('users', 'extra')
        a = fixtures.personal()
        b = a.model_copy(update={'display_name':'Changed name'})
        self.assertEqual(profile_binding(a), profile_binding(b))
        self.assertNotEqual(profile_binding(a), profile_binding(a.model_copy(update={'account_entity':'sensor.other'})))


class OnboardingConversationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ConversationTests.asyncSetUp
    message = fixtures.ConversationTests.message
    callback = fixtures.ConversationTests.callback
    telegram_update = fixtures.ConversationTests.telegram_update

    def flow(self, kind='users', key='p', **kwargs):
        return self.journal.onboarding.create(kind,key,**kwargs)

    async def open(self, token, user=123):
        await self.router.handle(self.message(user=user,text='/start dtek_'+token))

    def shared(self, row, user=123, people=None, chat=None):
        if chat is not None:
            payload={'chat_shared':{'request_id':row['request_id'],'chat_id':chat}}
        else:
            payload={'users_shared':{'request_id':row['request_id'],'users': people or [{'user_id':789,'first_name':'Example'}]}}
        return self.message(user=user,text='',**payload)

    async def test_invitation_requests_only_its_profile_without_disclosing_account(self):
        token, _ = self.flow('invite',self.profile.key,binding=profile_binding(self.profile))
        await self.open(token,user=777)
        request=self.journal.requests.pending()[0]
        self.assertEqual(request['profile_key'],self.profile.key)
        self.assertEqual(self.router.profiles(777),[])
        text=self.bot.send_message.call_args.kwargs['text']
        self.assertNotIn(self.profile.display_name,text)
        self.assertNotIn(self.profile.account_entity,text)
        self.app.ha.command.assert_not_awaited()

    async def test_revoked_expired_and_changed_account_invitations_create_no_request(self):
        for change in ('revoked','changed','expired'):
            token,row=self.flow('invite',self.profile.key,binding=profile_binding(self.profile))
            if change=='revoked':self.journal.onboarding.revoke(row['id'])
            elif change=='changed':self.profile.account_entity='sensor.changed'
            else:self.journal.db.execute('UPDATE onboarding_links SET expires=0 WHERE id=?',(row['id'],))
            self.journal.db.execute("DELETE FROM interactions")
            await self.open(token,user=777)
            self.assertEqual(self.journal.requests.pending(),[])
        self.app.ha.command.assert_not_awaited()

    async def test_existing_member_does_not_request_again(self):
        token,_=self.flow('invite',self.profile.key,binding=profile_binding(self.profile))
        await self.open(token)
        self.assertEqual(self.journal.requests.pending(),[])
        self.assertIn('вже налаштовано',self.bot.send_message.call_args.kwargs['text'])

    async def test_actual_aiogram_shared_update_and_no_automatic_grant(self):
        token,row=self.flow()
        await self.open(token)
        keyboard=self.bot.send_message.call_args.kwargs['reply_markup']
        request=keyboard.keyboard[0][0].request_users
        self.assertEqual(request.max_quantity,10)
        self.assertFalse(request.user_is_bot)
        await self.router.handle(self.telegram_update(self.shared(row)))
        snapshot=self.journal.onboarding.snapshot(self.journal.recipients)[0]
        self.assertEqual(snapshot['choices'][0]['id'],'789')
        self.assertFalse(snapshot['choices'][0]['ready'])
        self.assertTrue(self.bot.send_message.call_args.kwargs['reply_markup'].remove_keyboard)
        self.assertEqual(self.router.profiles(789),[])
        self.assertEqual(self.journal.requests.pending(),[])
        self.assertEqual(self.journal.recipients.list(),[])
        # Only an actual incoming private message makes this person reachable.
        self.journal.recipients.observe({'message':{'date':time.time(),'from':{'id':789},'chat':{'id':789,'type':'private','first_name':'Example'}}},self.bot.id)
        self.assertTrue(self.journal.onboarding.snapshot(self.journal.recipients)[0]['choices'][0]['ready'])

    async def test_chooser_owner_can_have_no_account_access_and_cannot_select_for_others(self):
        token,row=self.flow()
        await self.open(token,user=777)
        await self.router.handle(self.shared(row,user=123))
        self.assertEqual(self.journal.onboarding.get(row['id'])['status'],'selecting')
        await self.router.handle(self.shared(row,user=777))
        self.assertEqual(self.journal.onboarding.get(row['id'])['status'],'complete')
        self.assertEqual(self.router.profiles(777),[])
        self.app.ha.command.assert_not_awaited()

    async def test_expired_and_replayed_shared_results_do_not_overwrite(self):
        token,row=self.flow()
        await self.open(token)
        await self.router.handle(self.shared(row))
        before=self.journal.onboarding.get(row['id'])['result']
        await self.router.handle(self.shared(row,people=[{'user_id':444}]))
        self.assertEqual(self.journal.onboarding.get(row['id'])['result'],before)
        self.journal.db.execute('DELETE FROM interactions')
        token,row=self.flow()
        await self.open(token)
        self.journal.db.execute('UPDATE onboarding_links SET expires=0 WHERE id=?',(row['id'],))
        await self.router.handle(self.shared(row))
        self.assertIsNone(self.journal.onboarding.get(row['id'])['result'])

    async def test_wrong_kind_negative_id_and_oversized_user_selection_rejected(self):
        cases=[{'chat':-1001},{'people':[{'user_id':-123}]},{'people':[{'user_id':i+1} for i in range(11)]}]
        for case in cases:
            self.journal.db.execute('DELETE FROM interactions')
            token,row=self.flow()
            await self.open(token)
            await self.router.handle(self.shared(row,**case))
            self.assertIsNone(self.journal.onboarding.get(row['id'])['result'])
        self.app.ha.command.assert_not_awaited()

    async def test_open_cancel_and_native_navigation_keep_reading_draft_and_remove_keyboard(self):
        draft={'profile':self.profile.key,'expires':time.time()+300,'values':{'GENERAL':'12'},'photos':{'GENERAL':{'data':'fixture'}}}
        self.router.sessions[123]=draft
        token,row=self.flow()
        await self.open(token)
        await self.router.handle(self.message(user=123,text=CANCEL_PICKER))
        self.assertIs(self.router.sessions[123],draft)
        self.assertEqual(self.journal.onboarding.get(row['id'])['status'],'cancelled')
        self.assertTrue(self.bot.send_message.call_args.kwargs['reply_markup'].remove_keyboard)
        self.journal.db.execute('DELETE FROM interactions')
        token,row=self.flow()
        await self.open(token)
        self.journal.db.execute('UPDATE onboarding_links SET expires=0 WHERE id=?',(row['id'],))
        await self.router.handle(self.message(user=123,text='/start'))
        self.assertEqual(self.journal.onboarding.get(row['id'])['status'],'cancelled')
        self.assertIs(self.router.sessions[123],draft)

    async def test_choose_own_private_chat(self):
        token,row=self.flow('private')
        await self.open(token)
        await self.router.handle(self.message(user=123,text=USE_ME))
        self.assertEqual(json.loads(self.journal.onboarding.get(row['id'])['result'])[0]['id'],'123')

    async def test_channel_rights_and_group_member_without_pin(self):
        for kind,pin,member,ready in [('channel',True,SimpleNamespace(status='administrator',can_post_messages=True,can_edit_messages=True),True),
                                      ('channel',True,SimpleNamespace(status='administrator',can_post_messages=True,can_edit_messages=False),False),
                                      ('group',False,SimpleNamespace(status='member'),True)]:
            self.journal.db.execute('DELETE FROM interactions')
            token,row=self.flow(kind,pin=pin)
            self.bot.get_chat=AsyncMock(return_value=SimpleNamespace(type='channel' if kind=='channel' else 'supergroup',title='Demo',username=None,permissions=SimpleNamespace(can_send_messages=True)))
            self.bot.get_chat_member=AsyncMock(side_effect=[member,SimpleNamespace(status='creator')])
            await self.open(token)
            request=self.bot.send_message.call_args.kwargs['reply_markup'].keyboard[0][0].request_chat
            if kind=='channel':
                self.assertTrue(request.bot_administrator_rights.can_post_messages)
                self.assertFalse(request.bot_administrator_rights.can_delete_messages)
            else:
                self.assertIsNone(request.bot_administrator_rights)
                self.assertTrue(request.bot_is_member)
                self.assertFalse(request.user_administrator_rights.can_promote_members)
            await self.router.handle(self.shared(row,chat=-1001))
            result=json.loads(self.journal.onboarding.get(row['id'])['result'])
            self.assertEqual(result[0]['ready'],ready)
        self.app.ha.command.assert_not_awaited()

    async def test_failed_chat_check_can_retry_without_sending_any_message_to_target(self):
        token,row=self.flow('channel')
        self.bot.get_chat=AsyncMock(side_effect=TimeoutError())
        await self.open(token)
        await self.router.handle(self.shared(row,chat=-1001))
        self.assertEqual(self.journal.onboarding.get(row['id'])['status'],'selecting')
        self.assertTrue(all(c.kwargs['chat_id']==123 for c in self.bot.send_message.call_args_list))

    async def test_request_acceptance_requires_specific_account_not_any_access(self):
        self.journal.requests.request({'id':123},'other','not-the-current-binding')
        await self.router.sync_commands()
        self.assertEqual(len(self.journal.requests.pending()),1)
        self.bot.send_message.assert_not_awaited()

    async def test_feedback_journal_redacts_link_but_keeps_live_update_intact(self):
        token,_=self.flow()
        payload=self.message(user=123,text='/start dtek_'+token)
        self.journal.ingest(999,payload,'123')
        raw=str(self.journal.db.execute('SELECT payload FROM events').fetchall())
        self.assertNotIn(token,raw)
        self.assertIn(token,payload['message']['text'])


class OnboardingApiTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ConversationTests.asyncSetUp

    def setup_api(self):
        self.app.bot_profiles = SimpleNamespace(snapshot=lambda _: {'profiles': [{'id':'default','bot_id':123456}]}, read=lambda: {})
        self.app.bots = SimpleNamespace(profiles={}, public=lambda: [], clients={'default':self.tg}, conversations={'default':self.router})
        self.app.get_bot_profiles=lambda: Orchestrator.get_bot_profiles(self.app)
        self.app._onboarding_client=lambda payload: Orchestrator._onboarding_client(self.app,payload)
        self.app._get_editor_runtime_config=lambda: self.app.config
        self.bot.get_me=AsyncMock(return_value=SimpleNamespace(username='ExampleBot'))
        return {'bot_id':'default','kind':'invite','profile_key':self.profile.key,'account_entity':self.profile.account_entity,'expected_revision':'rev'}

    async def test_owner_creates_and_revokes_profile_link_without_granting_access(self):
        body=self.setup_api()
        with patch('config.runtime_config_revision',return_value='rev'):
            result=await Orchestrator.create_onboarding_link(self.app,body)
        self.assertTrue(result['link']['url'].startswith('https://t.me/ExampleBot?start=dtek_'))
        self.assertNotIn('url',result['bots']['onboarding']['default'][0])
        self.assertNotIn('token_hash',result['bots']['onboarding']['default'][0])
        self.assertEqual(self.journal.requests.pending(),[])
        Orchestrator.revoke_onboarding_link(self.app,{'bot_id':'default','id':result['link']['id']})
        self.assertIsNone(self.journal.onboarding.by_token(result['link']['url'].split('dtek_')[1]))
        self.bot.send_message.assert_not_awaited()
        self.app.ha.command.assert_not_awaited()

    async def test_unsaved_changed_source_wrong_bot_and_send_only_are_rejected(self):
        body=self.setup_api()
        with patch('config.runtime_config_revision',return_value='rev'):
            for change in ({'expected_revision':'stale'},{'account_entity':'sensor.other'},{'bot_id':'other'},{'profile_key':'missing'}):
                with self.assertRaises(ValueError):
                    await Orchestrator.create_onboarding_link(self.app,body|change)
            self.app.config.receive_commands=False
            with self.assertRaises(ValueError):
                await Orchestrator.create_onboarding_link(self.app,body)
        self.bot.get_me.assert_not_awaited()

    async def test_picker_supports_unsaved_profile_and_never_returns_credentials(self):
        body=self.setup_api()|{'kind':'users','profile_key':'new_unsaved_profile'}
        result=await Orchestrator.create_onboarding_link(self.app,body)
        self.assertEqual(result['link']['kind'],'users')
        self.assertNotIn('token_hash',json.dumps(result))
        self.assertEqual(self.app.config.addresses,[self.profile])

    async def test_network_failure_does_not_create_link_or_expose_error(self):
        body=self.setup_api()|{'kind':'users'}
        self.bot.get_me.side_effect=RuntimeError('private-token-or-response')
        with self.assertRaises(ValueError) as error:
            await Orchestrator.create_onboarding_link(self.app,body)
        self.assertNotIn('private-token',str(error.exception))
        self.assertEqual(self.journal.onboarding.snapshot(self.journal.recipients),[])

    async def test_real_http_routes_and_errors(self):
        from aiohttp.test_utils import TestClient,TestServer
        from ui_server import UIServer
        body=self.setup_api()|{'kind':'users'}
        self.app.create_onboarding_link=lambda value: Orchestrator.create_onboarding_link(self.app,value)
        self.app.revoke_onboarding_link=lambda value: Orchestrator.revoke_onboarding_link(self.app,value)
        async with TestClient(TestServer(UIServer(self.app)._app)) as client:
            response=await client.post('/api/bots/onboarding/create',json=body)
            self.assertEqual(response.status,200)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            result=await response.json()
            response=await client.post('/api/bots/onboarding/revoke',json={'bot_id':'default','id':result['link']['id']})
            self.assertEqual(response.status,200)
            self.app.config.receive_commands=False
            response=await client.post('/api/bots/onboarding/create',json=body)
            self.assertEqual(response.status,400)

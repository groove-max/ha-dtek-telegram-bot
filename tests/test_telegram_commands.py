"""Native commands, explicit requests and draft preservation without Telegram calls."""
import sqlite3
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_personal_profiles as fixtures
from config import AddressConfig
from connection_requests import ConnectionRequests
from telegram_commands import NativeCommands, commands_for


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.requests = ConnectionRequests(self.db)
        self.sender = {'id': 123, 'first_name': 'Example', 'username': 'example'}

    def test_explicit_request_deduplicates_and_decision_uses_revision(self):
        self.assertEqual(self.requests.request(self.sender), 'pending')
        original = self.requests.pending()[0]
        self.requests.request({**self.sender, 'first_name': 'Updated'})
        self.assertEqual(self.requests.pending(), [original])
        self.assertFalse(self.requests.resolve(123, 'declined', 'old'))
        self.assertTrue(self.requests.resolve(123, 'declined', original['revision']))
        self.assertFalse(self.requests.resolve(123, 'accepted', original['revision']))
        self.assertEqual(self.requests.request(self.sender), 'declined')
        with patch('connection_requests.time.time', return_value=time.time() + 86401):
            self.assertEqual(self.requests.request(self.sender), 'pending')
        self.assertNotEqual(self.requests.pending()[0]['revision'], original['revision'])

    def test_bounded_requests_do_not_evict_pending_applicants(self):
        for user in range(1, 501):
            self.requests.request({'id': user})
        self.assertEqual(self.requests.request({'id': 501}), 'full')
        self.assertEqual(len(self.requests.pending()), 500)
        self.assertEqual(self.requests.request({'id': 1}), 'pending')
        with patch('connection_requests.time.time', return_value=time.time() + 31 * 86400):
            self.assertEqual(self.requests.pending(), [])
            self.assertEqual(self.requests.request({'id': 501}), 'pending')

    def test_invalid_applicants(self):
        for sender in ({'id': -1}, {'id': True}, {'id': 123, 'is_bot': True}):
            with self.assertRaises(ValueError):
                self.requests.request(sender)


class NativeMenuTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.bot = SimpleNamespace(set_my_commands=AsyncMock(), delete_my_commands=AsyncMock(), set_chat_menu_button=AsyncMock())
        self.menu = NativeCommands(self.bot, self.db)

    async def test_scope_capabilities_languages_and_idempotence(self):
        profile = fixtures.personal(allow_readings=True)
        await self.menu.sync({123: [profile]})
        calls = self.bot.set_my_commands.call_args_list
        self.assertEqual(len(calls), 8)
        anonymous = calls[0].kwargs
        self.assertEqual(anonymous['scope'].type, 'all_private_chats')
        self.assertEqual([c.command for c in anonymous['commands']], ['start', 'connect', 'help'])
        ru = calls[-1].kwargs
        self.assertEqual(ru['scope'].chat_id, 123)
        self.assertEqual(ru['language_code'], 'ru')
        self.assertIn('Головне меню', ru['commands'][0].description)
        self.assertIn('readings', [c.command for c in ru['commands']])
        self.assertNotIn('status', [c.command for c in ru['commands']])
        await self.menu.sync({123: [profile]})
        self.assertEqual(self.bot.set_my_commands.await_count, 8)
        profile.status_message.enabled = False
        profile.allow_readings = False
        await self.menu.sync({123: [profile]})
        commands = [c.command for c in self.bot.set_my_commands.call_args.kwargs['commands']]
        self.assertIn('status', commands)
        self.assertNotIn('readings', commands)
        self.assertIn('documents', commands)
        self.assertNotIn('history', commands)

    async def test_removed_user_scopes_are_deleted_even_after_restart(self):
        await self.menu.sync({123: [fixtures.personal()]})
        restarted = NativeCommands(self.bot, self.db)
        await restarted.sync({})
        self.assertEqual(self.bot.delete_my_commands.await_count, 4)
        self.assertEqual([c.kwargs['language_code'] for c in self.bot.delete_my_commands.call_args_list], ['', 'en', 'uk', 'ru'])
        self.assertEqual(self.db.execute('SELECT * FROM command_scopes').fetchall(), [])

    async def test_partial_setup_failure_remains_recoverable(self):
        self.bot.set_my_commands.side_effect = [True] * 4 + [TimeoutError()]
        with self.assertRaises(TimeoutError):
            await self.menu.sync({123: [fixtures.personal()]})
        self.assertEqual(self.db.execute('SELECT chat_id FROM command_scopes').fetchall(), [(123,)])
        self.bot.set_my_commands.side_effect = None
        await NativeCommands(self.bot, self.db).sync({})
        self.assertEqual(self.bot.delete_my_commands.await_count, 4)

    async def test_one_unavailable_chat_does_not_block_other_users(self):
        async def setup(**kwargs):
            if getattr(kwargs['scope'], 'chat_id', None) == 123:
                raise RuntimeError('unavailable')
        self.bot.set_my_commands.side_effect = setup
        with self.assertRaises(RuntimeError):
            await self.menu.sync({123: [fixtures.personal()], 456: [fixtures.personal(telegram_user_id=456)]})
        self.assertIn(456, self.menu.synced)
        self.assertNotIn(123, self.menu.synced)

    def test_building_private_profile_has_only_supported_actions(self):
        names = commands_for([AddressConfig(entity_prefix='house')])
        self.assertNotIn('documents', names)
        self.assertNotIn('readings', names)
        self.assertIn('today', names)
        self.assertIn('accounts', commands_for([fixtures.personal(), fixtures.personal(account_entity='sensor.other')]))


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ConversationTests.asyncSetUp
    message = fixtures.ConversationTests.message
    callback = fixtures.ConversationTests.callback

    def draft(self):
        session = {'profile': self.profile.key, 'expires': time.time() + 300,
                   'context': {'revision': 'meter', 'zones': [{'zone': 'DAY', 'previous': '10'}, {'zone': 'NIGHT', 'previous': '5'}]},
                   'values': {'DAY': '12'}, 'input': True, 'photos': {'DAY': {'data': 'fixture'}}}
        self.router.sessions[123] = session
        return session

    async def test_start_is_not_a_connection_request(self):
        await self.router.handle(self.message())
        self.assertEqual(self.journal.requests.pending(), [])
        self.assertIn('GitHub', self.bot.send_message.call_args.kwargs['text'])
        self.app.ha.command.assert_not_awaited()

    async def test_connect_works_without_welcome_and_grants_no_access(self):
        self.app.config.welcome_enabled = False
        await self.router.handle(self.message(text='/connect'))
        self.assertEqual(self.journal.requests.pending()[0]['user_id'], 777)
        self.assertEqual(self.router.profiles(777), [])
        self.assertIn('Очікуйте', self.bot.send_message.call_args.kwargs['text'])
        await self.router.handle(self.message(text='/readings'))
        self.app.ha.command.assert_not_awaited()

    async def test_group_connect_and_sender_mismatch_are_ignored(self):
        await self.router.handle(self.message(text='/connect', kind='group'))
        await self.router.handle(self.message(text='/connect', chat={'id': 123, 'type': 'private'}))
        self.assertEqual(self.journal.requests.pending(), [])

    async def test_commands_dispatch_to_existing_workflows(self):
        self.router.action = AsyncMock()
        for name, action, payload in [('today', 'grid', 0), ('tomorrow', 'grid', 1), ('history', 'history', None), ('documents', 'documents', None), ('status', 'status', None), ('readings', 'readings', None)]:
            await self.router.command(123, [self.profile], '/' + name)
            self.router.action.assert_awaited_with(123, self.profile, action, payload)

    async def test_named_bot_suffix_and_unknown_command(self):
        self.bot.get_me = AsyncMock(return_value=SimpleNamespace(username='DemoBot'))
        self.router.action = AsyncMock()
        await self.router.command(123, [self.profile], '/today@OtherBot')
        self.router.action.assert_not_awaited()
        await self.router.command(123, [self.profile], '/today@DemoBot')
        self.router.action.assert_awaited_with(123, self.profile, 'grid', 0)
        draft = self.draft()
        await self.router.command(123, [self.profile], '/typo')
        self.assertIs(self.router.sessions[123], draft)
        self.assertIn('Невідома команда', self.bot.send_message.call_args.kwargs['text'])

    async def test_start_keeps_values_and_photos_but_old_confirmation_cannot_submit(self):
        draft = self.draft()
        token = self.router.keyboard(123, self.profile, [('Confirm', 'confirm', 'old')]).inline_keyboard[0][0].callback_data
        await self.router.handle(self.message(user=123, text='/start'))
        self.assertIs(self.router.sessions[123], draft)
        self.assertIn('Продовжити', str(self.bot.send_message.call_args.kwargs['reply_markup']))
        await self.router.handle(self.callback(token))
        self.app.ha.command.assert_not_awaited()
        await self.router.resume_reading(123)
        self.assertIn('Ніч', self.bot.send_message.call_args.kwargs['text'])
        self.assertEqual(draft['values'], {'DAY': '12'})
        self.assertIn('DAY', draft['photos'])

    async def test_documents_and_grid_keep_draft_cancel_clears_it(self):
        draft = self.draft()
        self.router.document_action = AsyncMock()
        self.router.grid = AsyncMock()
        await self.router.action(123, self.profile, 'documents', None)
        await self.router.action(123, self.profile, 'grid', 0)
        self.assertIs(self.router.sessions[123], draft)
        await self.router.command(123, [self.profile], '/cancel')
        self.assertNotIn(123, self.router.sessions)
        self.app.ha.command.assert_not_awaited()

    async def test_profile_switch_requires_discard_and_stale_discard_is_safe(self):
        draft = self.draft()
        second = fixtures.personal(account_entity='sensor.other_account', allow_readings=True)
        self.app.config.addresses.append(second)
        await self.router.action(123, second, 'profile', None)
        self.assertIs(self.router.sessions[123], draft)
        discard = next(b for b in self.router.buttons.values() if b['action'] == 'discard')
        self.router.sessions[123] = dict(draft)
        await self.router.action(123, second, 'discard', discard['payload'])
        self.assertIn(123, self.router.sessions)
        await self.router.action(123, second, 'profile', None)
        discard = [b for b in self.router.buttons.values() if b['action'] == 'discard'][-1]
        await self.router.action(123, second, 'discard', discard['payload'])
        self.assertNotIn(123, self.router.sessions)
        self.app.ha.command.assert_not_awaited()

    async def test_command_profile_selection_preserves_requested_day_and_authorization(self):
        second = fixtures.personal(account_entity='sensor.other_account')
        self.app.config.addresses.append(second)
        await self.router.command(123, self.router.profiles(123), '/tomorrow')
        button = self.bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard[1][0]
        self.router.grid = AsyncMock()
        await self.router.handle(self.callback(button.callback_data, user=777))
        self.router.grid.assert_not_awaited()
        await self.router.handle(self.callback(button.callback_data))
        self.router.grid.assert_awaited_with(123, second, 1)
        self.app.config.addresses.remove(second)
        await self.router.handle(self.callback(button.callback_data))
        self.assertEqual(self.router.grid.await_count, 1)

    async def test_active_config_accepts_request_once_after_restart_not_draft(self):
        self.journal.requests.request({'id': 777})
        await self.router.sync_commands()
        self.assertEqual(len(self.journal.requests.pending()), 1)
        self.app.config.addresses.append(fixtures.personal(telegram_user_id=777))
        self.router._commands_retry = 0
        await self.router.sync_commands()
        self.assertEqual(self.journal.requests.pending(), [])
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.router._commands_retry = 0
        await self.router.sync_commands()
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_send_only_server_cannot_overwrite_menu_or_resolve_requests(self):
        self.app.config.receive_commands = False
        self.journal.requests.request({'id': 123})
        await self.router.sync_commands()
        self.bot.set_my_commands.assert_not_awaited()
        self.assertEqual(len(self.journal.requests.pending()), 1)

    async def test_request_api_scopes_by_bot_and_stale_decisions_cannot_overwrite(self):
        from main import Orchestrator
        self.journal.requests.request({'id': 777})
        self.app.bot_profiles = SimpleNamespace(snapshot=lambda _: {'profiles': [{'id': 'default', 'bot_id': 123456}]}, read=lambda: {})
        self.app.bots = SimpleNamespace(profiles={}, public=lambda: [], clients={'default': self.tg})
        self.app.get_bot_profiles = lambda: Orchestrator.get_bot_profiles(self.app)
        pending = self.app.get_bot_profiles()['connection_requests']['default'][0]
        for wrong in ({'bot_id': 'other', 'user_id': 777, 'revision': pending['revision']},
                      {'bot_id': 'default', 'user_id': 777, 'revision': 'old'}):
            with self.assertRaises(ValueError):
                Orchestrator.decline_connection_request(self.app, wrong)
        self.assertEqual(len(self.journal.requests.pending()), 1)
        result = Orchestrator.decline_connection_request(self.app, {'bot_id': 'default', 'user_id': 777, 'revision': pending['revision']})
        self.assertEqual(result['connection_requests']['default'], [])
        self.assertEqual(self.journal.requests.request({'id': 777}), 'declined')

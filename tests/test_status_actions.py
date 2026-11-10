"""HA actions preserve routing, delivery evidence and the existing pinned status."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rootfs/app'))
from config import AddressConfig
from features.status_message import StatusMessageFeature
from ha_client import HAClient
from state_store import StateStore
from template_engine import TemplateEngine
from status_actions import StatusActions, ActionJob
import status_actions


class StatusActionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.address = AddressConfig(mode='personal', profile_id='apartment', account_entity='sensor.account',
                                     telegram_user_ids=[11, 22], status_message={'enabled': False})
        self.routes = [self.address.for_user(u) for u in [11, 22]]
        self.telegram = SimpleNamespace(send_message=AsyncMock(return_value=123))
        self.generation = datetime.now(timezone.utc)
        self.app = SimpleNamespace(
            config=SimpleNamespace(addresses=[self.address]), deliveries=self.routes,
            ha=SimpleNamespace(is_connected=True, connected_at=self.generation, command=AsyncMock()),
            bots=SimpleNamespace(service=lambda a: self.telegram, chat=lambda a: a.telegram_chat_id,
                                 profiles={}, enqueue=AsyncMock()),
            render_status=AsyncMock(return_value='Status with battery 30%'), _features_by_address={},
        )
        self.manager = StatusActions(self.app, Path(self.temp.name))
        self.addCleanup(self.manager.db.close)

    def command(self, **kwargs):
        return dict(request_id='command1', profile_id='apartment', operation='send', silent=True,
                    expires=status_actions.time.time() + 55, **kwargs)

    async def execute(self, command=None):
        return await self.manager.execute(command or self.command(), self.generation)

    async def test_one_profile_fans_out_to_current_members_with_auto_status_disabled(self):
        self.assertFalse(self.manager.catalog()[0]['can_refresh'])
        result = await self.execute()
        self.assertEqual(result, {'status': 'sent', 'recipients': 2})
        self.assertEqual([c.kwargs['chat_id'] for c in self.telegram.send_message.call_args_list], ['11', '22'])
        self.assertTrue(all(c.kwargs['disable_notification'] for c in self.telegram.send_message.call_args_list))
        self.assertEqual([c.args[0].template_scope for c in self.app.render_status.call_args_list], ['apartment', 'apartment'])

    async def test_duplicate_request_is_not_resent_even_after_restart(self):
        first = await self.execute()
        self.assertEqual(await self.execute(), first)
        other = StatusActions(self.app, Path(self.temp.name))
        self.addCleanup(other.db.close)
        self.assertEqual(other.instance, self.manager.instance)
        self.assertEqual(await other.execute(self.command(), self.generation), first)
        self.assertEqual(self.telegram.send_message.await_count, 2)
        mutated = self.command(); mutated['silent'] = False
        self.assertEqual((await self.execute(mutated))['status'], 'failed')

    async def test_lost_result_and_partial_delivery_are_not_replayed(self):
        self.telegram.send_message.side_effect = [123, None]
        self.assertEqual(await self.execute(), {'status': 'partial', 'recipients': 1})
        self.assertEqual((await self.execute())['status'], 'partial')
        self.assertEqual(self.telegram.send_message.await_count, 2)

    async def test_crash_after_first_send_leaves_unknown_durable_result(self):
        self.telegram.send_message.side_effect = [123, RuntimeError('connection lost')]
        with self.assertRaises(RuntimeError):
            await self.execute()
        self.assertEqual((await self.execute())['status'], 'unknown')
        self.assertEqual(self.telegram.send_message.await_count, 2)

    async def test_expired_wrong_profile_and_disconnected_commands_cannot_send(self):
        command = self.command(); command['expires'] = 0
        self.assertEqual((await self.execute(command))['status'], 'expired')
        command = self.command(); command['profile_id'] = 'building'
        self.assertEqual((await self.execute(command))['status'], 'failed')
        self.app.ha.is_connected = False
        self.assertEqual((await self.execute())['status'], 'expired')
        self.telegram.send_message.assert_not_awaited()

    async def test_render_disconnect_does_not_send_stale_data(self):
        async def render(_):
            self.app.ha.is_connected = False
            return 'Status'
        self.app.render_status.side_effect = render
        self.assertEqual((await self.execute())['status'], 'expired')
        self.telegram.send_message.assert_not_awaited()

    async def test_new_requests_rate_limited_and_template_errors_not_delivered(self):
        await self.execute()
        command = self.command(); command['request_id'] = 'command2'
        self.assertEqual((await self.execute(command))['status'], 'rate_limited')
        with self.manager.db:
            self.manager.db.execute('DELETE FROM cooldown')
        self.app.render_status.return_value = '[Template error: status_on]'
        self.assertEqual((await self.execute(command))['status'], 'failed')
        self.assertEqual(self.telegram.send_message.await_count, 2)

    async def test_refresh_requires_enabled_pinned_status_and_uses_existing_features(self):
        command = self.command(); command['operation'] = 'refresh'
        self.assertEqual((await self.execute(command))['status'], 'disabled')
        feature = Mock(spec=StatusMessageFeature)
        feature.refresh_from_automation = AsyncMock(return_value='updated')
        self.app._features_by_address = {a.key: [feature] for a in self.routes}
        command['request_id'] = 'command2'
        self.assertEqual(await self.execute(command), {'status': 'updated', 'recipients': 2})
        self.telegram.send_message.assert_not_awaited()

    async def test_handle_uses_bot_worker_and_acknowledges_actual_result(self):
        async def enqueue(bot, method, jobs):
            self.assertEqual(bot, 'default')
            self.assertEqual(method, 'run')
            await jobs[0].run()
        self.app.bots.enqueue.side_effect = enqueue
        await self.manager.handle(self.command())
        result = self.app.ha.command.call_args.kwargs
        self.assertEqual(result['status'], 'sent')
        self.assertEqual(result['recipients'], 2)
        self.assertEqual(result['request_id'], 'command1')

    async def test_cancelled_job_cannot_send_after_caller_timeout(self):
        future = asyncio.get_running_loop().create_future()
        callback = AsyncMock()
        job = ActionJob(callback, future)
        future.cancel()
        await job.run()
        callback.assert_not_awaited()

    async def test_revoked_recipient_is_excluded(self):
        self.app.deliveries = self.routes[:1]
        self.assertEqual((await self.execute())['recipients'], 1)
        self.assertEqual(self.telegram.send_message.call_args.kwargs['chat_id'], '11')

    async def test_unknown_request_remains_unknown_after_acknowledgement_is_lost(self):
        async def enqueue(bot, method, jobs):
            await jobs[0].run()
        self.app.bots.enqueue.side_effect = enqueue
        self.app.ha.command.side_effect = ConnectionError('lost acknowledgement')
        await self.manager.handle(self.command())
        self.app.ha.command.side_effect = None
        await self.manager.handle(self.command())
        self.assertEqual(self.telegram.send_message.await_count, 2)
        self.assertEqual(self.app.ha.command.call_args.kwargs['status'], 'sent')

    async def test_old_connection_generation_cannot_send_after_reconnect(self):
        self.app.ha.connected_at = 'new-session'
        self.assertEqual((await self.execute())['status'], 'expired')
        self.telegram.send_message.assert_not_awaited()


class ActionSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_subscription_does_not_block_reader_and_can_receive_command_response(self):
        ha = HAClient(ha_token='fictional')
        ha._connected.set()
        ha._ws = SimpleNamespace(send_json=AsyncMock(), closed=True)
        callback = AsyncMock()
        pending = asyncio.create_task(ha._send_command({'type': 'subscribe'}, subscription_callback=callback))
        await asyncio.sleep(0)
        await ha._handle_message({'id': 1, 'type': 'result', 'success': True})
        await pending
        await ha._handle_message({'id': 1, 'type': 'event', 'event': {'request_id': 'a'}})
        await asyncio.sleep(0)
        callback.assert_awaited_once_with({'request_id': 'a'})
        await ha.stop()

    async def test_rejected_subscription_is_removed(self):
        ha = HAClient(ha_token='fictional'); ha._connected.set()
        ha._ws = SimpleNamespace(send_json=AsyncMock())
        pending = asyncio.create_task(ha._send_command({'type': 'subscribe'}, subscription_callback=AsyncMock()))
        await asyncio.sleep(0)
        await ha._handle_message({'id': 1, 'type': 'result', 'success': False})
        await pending
        self.assertFalse(ha._subscriptions)


class ExplicitPinnedRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.address = AddressConfig(entity_prefix='home', status_message={'enabled': True, 'pin': False, 'min_update_interval': 0})
        self.store = StateStore(Path(self.temp.name) / 'state.json')
        self.addAsyncCleanup(self.store.flush)
        self.tg = SimpleNamespace(resolve_chat_id=lambda chat: '100',
                                  edit_message_result=AsyncMock(return_value='ok'), send_message=AsyncMock(return_value=7))
        self.feature = StatusMessageFeature(address_config=self.address, ha_client=Mock(), telegram=self.tg,
                                            state_store=self.store, templates=TemplateEngine(Path(self.temp.name)))
        self.feature._build_status_text = AsyncMock(return_value='Current battery 20%')
        self.feature.state_set('status_message_id', 6)
        self.feature.state_set('status_message_chat_id', '100')
        self.addAsyncCleanup(self.feature.on_stop)

    async def test_edits_same_message_and_does_not_send_new(self):
        self.assertEqual(await self.feature.refresh_from_automation(status_actions.time.time()+55), 'updated')
        self.tg.edit_message_result.assert_awaited_once()
        self.assertEqual(self.tg.edit_message_result.call_args.args[:2], (6, 'Current battery 20%'))
        self.tg.send_message.assert_not_awaited()

    async def test_send_new_mode_never_used_by_refresh(self):
        self.address.status_message.delivery_mode = 'send_new'
        self.assertEqual(await self.feature.refresh_from_automation(status_actions.time.time()+55), 'disabled')
        self.tg.send_message.assert_not_awaited()

    async def test_cooldown_checked_after_waiting_for_another_update(self):
        self.address.status_message.min_update_interval = 300
        await self.feature._update_lock.acquire()
        task = asyncio.create_task(self.feature.refresh_from_automation(status_actions.time.time()+55))
        await asyncio.sleep(0)
        self.feature._last_attempt_ts = status_actions.time.monotonic()
        self.feature._update_lock.release()
        self.assertEqual(await task, 'rate_limited')
        self.tg.edit_message_result.assert_not_awaited()

    async def test_error_is_reported_without_scheduling_hidden_retry(self):
        self.tg.edit_message_result.return_value = 'unknown'
        self.assertEqual(await self.feature.refresh_from_automation(status_actions.time.time()+55), 'unknown')
        self.assertIsNone(self.feature._delayed_task)

    async def test_request_expiring_during_render_is_not_delivered(self):
        async def render(_):
            await asyncio.sleep(.02)
            return 'Late status'
        self.feature._build_status_text.side_effect = render
        self.assertEqual(await self.feature.refresh_from_automation(status_actions.time.time()+.01), 'expired')
        self.tg.edit_message_result.assert_not_awaited()

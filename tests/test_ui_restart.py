"""Exercise Supervisor's self-only restart contract and visible failure state."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rootfs/app"))
from ui_server import UIServer


class RestartTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.status = 200
        self.body = {"result": "ok", "data": {}}
        async def restart(request):
            self.calls.append(request.path)
            # Default-role apps can restart self; a concrete slug is forbidden.
            if request.match_info["target"] != "self":
                return web.json_response({"result": "error"}, status=403)
            return web.json_response(self.body, status=self.status)
        app = web.Application()
        app.router.add_post('/addons/{target}/restart', restart)
        self.supervisor = TestServer(app)
        await self.supervisor.start_server()
        self.addAsyncCleanup(self.supervisor.close)
        real_session = aiohttp.ClientSession
        url = self.supervisor.make_url('/')
        owner = self
        class LocalSupervisor:
            def __init__(self, **kwargs):
                owner.assertEqual(kwargs['timeout'].total, 45)
                self.session = real_session(**kwargs)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): await self.session.close()
            def post(self, target, **kwargs):
                owner.assertTrue(target.startswith('http://supervisor/'))
                return self.session.post(str(url) + target.removeprefix('http://supervisor/'), **kwargs)
        self.factory = LocalSupervisor
        self.ui = UIServer(Mock())
        self.addAsyncCleanup(self.ui.stop)

    async def run_restart(self):
        with patch.object(self.ui, '_supervisor_token', return_value='synthetic-test-token'), patch('ui_server.aiohttp.ClientSession', self.factory):
            self.ui._schedule_restart()
            task = self.ui._restart_task
            self.ui._schedule_restart()
            self.assertIs(self.ui._restart_task, task)
            await task

    async def test_default_role_endpoint_and_acceptance_is_not_application(self):
        identity = self.ui._activation_snapshot()['process_id']
        await self.run_restart()
        self.assertEqual(self.calls, ['/addons/self/restart'])
        self.assertEqual(self.ui._activation_snapshot()['process_id'], identity)
        self.assertEqual(self.ui._restart_status, {'state':'accepted'})
        self.assertNotEqual(UIServer(Mock())._activation_snapshot()['process_id'], identity)

    async def test_permission_and_json_rejections_are_visible_and_not_retried(self):
        for status, body, reason in [(403, {'result':'error'}, 'permission_denied'), (500, {'result':'error'}, 'supervisor_rejected'), (200, {'result':'error','message':'private details'}, 'supervisor_rejected')]:
            self.status, self.body = status, body
            before = len(self.calls)
            await self.run_restart()
            self.assertEqual(len(self.calls), before+1)
            self.assertEqual(self.ui._restart_status, {'state':'failed','reason':reason})
            self.assertNotIn('private', str(self.ui._activation_snapshot()))

    async def test_missing_token_never_calls_supervisor(self):
        with patch.object(self.ui, '_supervisor_token', return_value=''), patch('ui_server.aiohttp.ClientSession') as session:
            self.ui._schedule_restart()
            await self.ui._restart_task
            session.assert_not_called()
        self.assertEqual(self.ui._restart_status['reason'], 'missing_token')

    async def test_timeout_is_unknown_failure_without_automatic_retry(self):
        with patch.object(self.ui, '_supervisor_token', return_value='synthetic'), patch('ui_server.aiohttp.ClientSession', side_effect=TimeoutError) as session:
            self.ui._schedule_restart()
            await self.ui._restart_task
            session.assert_called_once()
        self.assertEqual(self.ui._restart_status, {'state':'failed','reason':'timeout'})

    async def test_shutdown_cancels_pending_restart(self):
        self.ui._schedule_restart()
        await self.ui.stop()
        self.assertTrue(self.ui._restart_task.cancelled())

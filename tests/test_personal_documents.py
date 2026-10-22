"""New acts stay private and are not resent after ambiguous Telegram delivery."""
import base64
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
import test_personal_profiles as fixtures
from ha_client import HACommandError

PDF=base64.b64encode(b'%PDF-1.4\nSYNTHETIC\n%%EOF').decode()

class DocumentConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.ConversationTests.asyncSetUp(self)
        self.profile.document_notifications=True
        self.rows=[]
        async def command(kind,**args):
            if args['action']=='documents':return {'documents':list(self.rows)}
            if args['action']=='document':return {'name':'Synthetic.pdf','data':PDF,'mime_type':'application/pdf'}
            raise AssertionError('Unexpected action')
        self.app.ha.command.side_effect=command
        self.bot.send_document=AsyncMock(return_value=SimpleNamespace(message_id=12345))
        async def recorded(operation,**kwargs):return await operation()
        self.tg._recorded=recorded

    async def check(self):
        self.router._document_checks={}
        await self.router.check_documents(self.profile,123)

    async def test_baseline_no_flood_then_new_pdf_once_across_router_restart(self):
        await self.check();self.bot.send_document.assert_not_awaited()
        self.rows=[{'id':'a'*32,'name':'Synthetic.pdf'}]
        await self.check();self.bot.send_document.assert_awaited_once()
        self.assertEqual(self.bot.send_document.call_args.kwargs['chat_id'],123)
        self.assertEqual(self.bot.send_document.call_args.kwargs['document'].data,base64.b64decode(PDF))
        await self.check();self.bot.send_document.assert_awaited_once()
        self.assertNotIn(PDF,str(self.app.state.get(self.profile.key,'documents_seen')))

    async def test_ambiguous_send_not_replayed_and_missing_file_retries_later(self):
        await self.check();self.rows=[{'id':'b'*32,'name':'Synthetic.pdf'}]
        self.bot.send_document.side_effect=TimeoutError
        await self.check();await self.check();self.bot.send_document.assert_awaited_once()
        self.assertEqual(self.journal.db.execute('SELECT status FROM deliveries').fetchone()[0],'unknown')

    async def test_unsupported_file_still_notifies_and_new_account_starts_baseline(self):
        await self.check();self.rows=[{'id':'c'*32,'name':'Synthetic.pdf'}]
        old=self.app.ha.command.side_effect
        async def command(kind,**args):
            if args['action']=='document':raise HACommandError('document_too_large')
            return await old(kind,**args)
        self.app.ha.command.side_effect=command
        await self.check();self.bot.send_document.assert_not_awaited()
        self.assertIn('Новий документ',self.bot.send_message.call_args.kwargs['text'])
        self.profile.account_entity='sensor.another_account';self.rows.append({'id':'d'*32,'name':'Other.pdf'})
        count=self.bot.send_message.await_count
        await self.check();self.assertEqual(self.bot.send_message.await_count,count)

    async def test_unassigned_user_cannot_read_documents(self):
        with self.assertRaises(HACommandError):await self.router.document_action(777,self.profile,'documents',None)
        self.app.ha.command.assert_not_awaited()
        self.bot.send_document.assert_not_awaited()

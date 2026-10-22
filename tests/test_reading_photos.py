"""Photo input never bypasses account identity, review or single-use confirmation."""
import asyncio
import base64
from io import BytesIO
import json
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock
from PIL import Image
import test_personal_profiles as fixtures
from reading_photos import prepare_photo, BoundedBuffer, MAX_INPUT
from ha_client import HACommandError


def png():
    data=BytesIO();Image.new('RGB',(80,40),'white').save(data,format='PNG');return data.getvalue()


class ImageTests(unittest.TestCase):
    def test_reencode_strips_metadata_and_keeps_bounded_readable_jpeg(self):
        source=BytesIO();image=Image.new('RGB',(3000,2000),'white');exif=Image.Exif();exif[270]='PRIVATE';image.save(source,format='JPEG',exif=exif)
        result=prepare_photo(source.getvalue())
        data=base64.b64decode(result['data']);converted=Image.open(BytesIO(data))
        self.assertEqual(converted.format,'JPEG');self.assertEqual(converted.width,2560)
        self.assertFalse(converted.getexif());self.assertLessEqual(len(data),1024*1024)

    def test_invalid_animated_and_oversize_images_are_rejected(self):
        for raw in (b'<svg/>',b'%PDF-1.4',b'x'*(MAX_INPUT+1)):
            with self.assertRaises(HACommandError):prepare_photo(raw)
        stream=BoundedBuffer();stream.seek(MAX_INPUT)
        with self.assertRaises(HACommandError):stream.write(b'x')


class PhotoConversationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.ConversationTests.asyncSetUp
    message=fixtures.ConversationTests.message
    callback=fixtures.ConversationTests.callback

    async def setup_session(self,zones=('GENERAL',)):
        session={'profile':self.profile.key,'expires':time.time()+300,'context':{'revision':'revision','account':'••••0001','photo_submission':True,'zones':[{'zone':z,'previous':'100'} for z in zones]},'values':{z:'123' for z in zones}}
        self.router.sessions[123]=session
        self.bot.get_file=AsyncMock(return_value=SimpleNamespace(file_path='photos/synthetic.jpg',file_size=len(png())))
        async def download(path,**kwargs):kwargs['destination'].write(png())
        self.bot.download_file=AsyncMock(side_effect=download)
        self.bot.send_photo=AsyncMock(return_value=SimpleNamespace(message_id=999))
        self.app.ha.command.return_value={'confirmation':'draft','readings':[{'zone':z,'value':123,'previous':'100','increase':'23'} for z in zones]}
        return session

    async def attach(self,zone='GENERAL',**changes):
        await self.router.action(123,self.profile,'photo_zone',zone)
        message=self.message(user=123,text='',photo=[{'file_id':'synthetic','file_size':len(png())}],message_id=len(self.bot.get_file.call_args_list)+10,**changes)
        await self.router.handle(message)

    async def test_photo_is_reviewed_and_only_confirm_can_submit(self):
        session=await self.setup_session()
        await self.router.review(123,self.profile,session)
        obsolete=next(k for k,v in self.router.buttons.items() if v['action']=='confirm')
        await self.attach()
        self.assertNotIn(obsolete,self.router.buttons)
        self.bot.send_photo.assert_awaited_once()
        self.assertNotIn('confirm',[c.kwargs['action'] for c in self.app.ha.command.call_args_list])
        await self.router.action(123,self.profile,'photo_review',None)
        self.assertIn('GENERAL',self.app.ha.command.call_args.kwargs['photos'])
        photo=session['photos']['GENERAL']['data']
        self.assertNotIn(photo,json.dumps(self.journal.db.execute('SELECT payload FROM events').fetchall()))
        token=next(k for k,v in self.router.buttons.items() if v['action']=='confirm')
        self.app.ha.command.return_value={'status':'accepted','photos':{'GENERAL':{}}}
        await self.router.handle(self.callback('dtek:'+token))
        await self.router.handle(self.callback('dtek:'+token))
        self.assertEqual(sum(c.kwargs['action']=='confirm' for c in self.app.ha.command.call_args_list),1)
        self.assertNotIn(123,self.router.sessions)

    async def test_partial_tariffs_have_no_confirmation_and_remove_preserves_values(self):
        session=await self.setup_session(('DAY_2_ZONE','NIGHT_2_ZONE'))
        await self.attach('DAY_2_ZONE')
        await self.router.action(123,self.profile,'photos',None)
        self.assertFalse(any(b['action'] in ('confirm','photo_review') for b in self.router.buttons.values()))
        await self.attach('NIGHT_2_ZONE')
        await self.router.action(123,self.profile,'photo_review',None)
        self.assertEqual(set(self.app.ha.command.call_args.kwargs['photos']),{'DAY_2_ZONE','NIGHT_2_ZONE'})
        await self.router.action(123,self.profile,'photo_remove',None)
        self.assertEqual(session['photos'],{})
        self.assertEqual(session['values'],{'DAY_2_ZONE':'123','NIGHT_2_ZONE':'123'})
        self.assertFalse(any(b['action']=='confirm' for b in self.router.buttons.values()))

    async def test_unassigned_group_expired_and_revoked_users_never_download(self):
        session=await self.setup_session()
        await self.router.action(123,self.profile,'photo_zone','GENERAL')
        for msg in (self.message(user=777,text='',photo=[{'file_id':'x'}]),self.message(user=123,text='',kind='group',photo=[{'file_id':'x'}])):
            await self.router.handle(msg)
        session['expires']=time.time()-1
        await self.router.handle(self.message(user=123,text='',photo=[{'file_id':'x'}]))
        self.bot.get_file.assert_not_awaited()
        await self.setup_session();self.profile.allow_readings=False
        await self.router.handle(self.message(user=123,text='',photo=[{'file_id':'x'}],message_id=20))
        self.bot.get_file.assert_not_awaited()

    async def test_album_and_bad_document_keep_existing_photo(self):
        session=await self.setup_session();await self.attach()
        saved=session['photos']['GENERAL'].copy()
        await self.attach(media_group_id='album')
        self.assertEqual(session['photos']['GENERAL'],saved)
        self.assertIn('без альбому',self.bot.send_message.call_args.kwargs['text'])
        await self.router.handle(self.message(user=123,text='',document={'file_id':'x','mime_type':'application/pdf'},message_id=22))
        self.assertEqual(session['photos']['GENERAL'],saved)
        self.assertEqual(self.bot.download_file.await_count,1)

    async def test_lost_confirm_response_does_not_replay_images(self):
        await self.setup_session();await self.attach();await self.router.action(123,self.profile,'photo_review',None)
        token=next(k for k,v in self.router.buttons.items() if v['action']=='confirm')
        self.app.ha.command.side_effect=TimeoutError
        await self.router.handle(self.callback('dtek:'+token));await self.router.handle(self.callback('dtek:'+token))
        self.assertEqual(sum(c.kwargs['action']=='confirm' for c in self.app.ha.command.call_args_list),1)
        self.assertNotIn(123,self.router.sessions)

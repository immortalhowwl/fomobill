import tempfile
import unittest
from pathlib import Path
import notifications as n

class WelcomeTests(unittest.TestCase):
    def test_photo_checkpoint_preserves_full_reply_and_pairing(self):
        with tempfile.TemporaryDirectory() as d:
            now=[1000.]
            path=Path(d)/'n.db'
            s=n.Notifications(path,clock=lambda:now[0])
            token=s.create_pairing('owner')
            update={'update_id':1,'message':{'text':'/start '+token,'chat':{'id':12,'type':'private'},'from':{'id':12}}}
            self.assertEqual(s.handle_update(update),'paired')
            calls=[]
            self.assertEqual(s.drain_replies(lambda *a:calls.append(a) or 8, lambda chat:7),'photo_sent')
            self.assertEqual(calls,[])
            s=n.Notifications(path,clock=lambda:now[0])
            self.assertEqual(s.handle_update(update),'duplicate')
            now[0]+=2
            self.assertEqual(s.drain_replies(lambda *a:calls.append(a) or 8,lambda c:self.fail('duplicate photo')),'sent')
            self.assertIn('WEBSITE LINKED',calls[0][1])
            self.assertFalse(s.status('owner')['active'])
            with s.connect() as db:
                row=db.execute('select * from command_replies').fetchone()
                self.assertEqual(row['photo_message_id'],7)
                self.assertEqual(row['message_id'],8)

    def test_media_retry_fallback_and_existing_preferences(self):
        for permanent in (False,True):
            with self.subTest(permanent=permanent), tempfile.TemporaryDirectory() as d:
                now=[1000.];s=n.Notifications(Path(d)/'n.db',clock=lambda:now[0])
                def update(uid,text):return {'update_id':uid,'message':{'text':text,'chat':{'id':12,'type':'private'},'from':{'id':12}}}
                s.handle_update(update(1,'/subscribe'))
                with s.connect() as db:before=tuple(db.execute('select * from recipients').fetchone())
                s.handle_update(update(2,'/start'))
                def fail(chat):raise n.DeliveryError('rejected' if permanent else 'temporary',permanent=permanent)
                expected=['photo_fallback'] if permanent else ['photo_pending','photo_pending','photo_fallback']
                for status in expected:
                    self.assertEqual(s.drain_replies(lambda *a:self.fail('early text'),fail),status)
                    now[0]+=61
                self.assertEqual(s.drain_replies(lambda *a:99,lambda c:self.fail('fallback photo')),'sent')
                with s.connect() as db:self.assertEqual(tuple(db.execute('select * from recipients').fetchone()),before)
                s.handle_update(update(3,'/help'));now[0]+=2
                self.assertEqual(s.drain_replies(lambda *a:100,lambda c:self.fail('help photo')),'sent')
                s.handle_update(update(4,'/stop'));s.handle_update(update(5,'/start'))
                with s.connect() as db:self.assertEqual(db.execute('select active from recipients').fetchone()[0],0)

    def test_rate_limit_shared_and_invalid_receipt(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            now=[1000.];s=n.Notifications(Path(d)/'n.db',clock=lambda:now[0])
            s.handle_update({'update_id':1,'message':{'text':'/start','chat':{'id':12,'type':'private'},'from':{'id':12}}})
            def fail(chat):raise n.DeliveryError('rate_limited',retry_after=300)
            self.assertEqual(s.drain_replies(lambda *a:8,fail),'photo_pending')
            now[0]+=299
            self.assertEqual(s.drain_replies(lambda *a:8,lambda c:7),'cooldown')
            now[0]+=1
            self.assertEqual(s.drain_replies(lambda *a:8,lambda c:7),'photo_sent')
        bot=n.Telegram('12345:'+'x'*35)
        for result in [{'message_id':1,'chat':{'id':13,'type':'private'}},{'message_id':False,'chat':{'id':12,'type':'private'}}]:
            with patch.object(bot,'call',return_value=result):
                with self.assertRaises(n.DeliveryError):bot.send_welcome(12)
        with patch.object(Path,'open',side_effect=FileNotFoundError):
            with self.assertRaises(n.DeliveryError) as e:bot.send_welcome(12)
            self.assertTrue(e.exception.permanent)

    def test_real_multipart_upload(self):
        import io,json
        from email.parser import BytesParser
        from email.policy import default
        class Response(io.BytesIO):
            pass
        class Opener:
            def open(self,request,timeout):
                self.request=request
                return Response(json.dumps({'ok':True,'result':{'message_id':9,'chat':{'id':12,'type':'private'}}}).encode())
        o=Opener(); b=n.Telegram('12345:'+'x'*35,opener=o)
        self.assertEqual(b.send_welcome(12),9)
        self.assertTrue(o.request.full_url.endswith('/sendPhoto'))
        msg=BytesParser(policy=default).parsebytes(('Content-Type: '+o.request.get_header('Content-type')+'\r\n\r\n').encode()+o.request.data)
        parts={p.get_param('name',header='content-disposition'):p for p in msg.iter_parts()}
        self.assertEqual(parts['chat_id'].get_content().strip(),'12')
        self.assertEqual(parts['photo'].get_content_type(),'image/png')
        self.assertTrue(parts['photo'].get_payload(decode=True).startswith(b'\x89PNG'))
        self.assertEqual(parts['protect_content'].get_content().strip(),'true')
        self.assertNotIn('parse_mode',parts)

if __name__=='__main__':unittest.main()

"""Offline integration fixtures only; never starts a provider loop."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
import app
from monitoring import Monitor, PROGRAM

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.server=app.make_server(port=0,data_dir=self.tmp.name,sources=app.Sources(lambda *a: []))
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def req(self,path,method='GET',body=None,cookie=None,origin=True,extra=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        h={'Content-Type':'application/json','X-Fomobill':'1'}
        if origin:h['Origin']=self.origin
        if cookie:h['Cookie']=cookie
        h.update(extra or {})
        c.request(method,path,json.dumps(body) if body is not None else None,h)
        r=c.getresponse();data=r.read();headers=dict(r.getheaders());status=r.status;c.close()
        return status,json.loads(data),headers
    def owner(self):
        status,data,h=self.req('/api/session','POST',{})
        self.assertEqual(status,200)
        self.assertIn('HttpOnly',h['Set-Cookie']);self.assertIn('SameSite=Strict',h['Set-Cookie'])
        return h['Set-Cookie'].split(';')[0]
    def test_owner_privacy_csrf_paths_and_stop(self):
        a,b=self.owner(),self.owner();self.assertNotEqual(a,b)
        self.assertEqual(self.req('/api/monitor')[0],401)
        self.assertEqual(self.req('/api/monitor/watch','POST',{'address':PROGRAM},a,False)[0],403)
        self.assertEqual(self.req('/api/monitor/watch','POST',{'address':PROGRAM},a,extra={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.req('/api/monitor/watch','POST',{'address':PROGRAM,'owner':'evil'},a)[0],400)
        self.assertEqual(self.req('/api/monitor/watch','POST',{'address':PROGRAM},a)[0],200)
        self.assertEqual(len(self.req('/api/monitor',cookie=a)[1]['watches']),1)
        self.assertEqual(self.req('/api/monitor',cookie=b)[1]['watches'],[])
        self.assertEqual(self.req('/api/monitor?owner=evil',cookie=a)[0],400)
        self.assertEqual(self.req('/api/monitor/watch/','POST',{},a)[0],405)
        self.assertEqual(self.req('/api/monitor/unwatch','POST',{'address':PROGRAM},b)[0],200)
        self.assertEqual(len(self.req('/api/monitor',cookie=a)[1]['watches']),1)
        self.assertEqual(self.req('/api/monitor/unwatch','POST',{'address':PROGRAM},a)[0],200)
        self.assertEqual(self.req('/api/monitor',cookie=a)[1]['watches'],[])
    def test_no_implicit_worker_body_limit_and_forgery(self):
        self.assertIsNone(self.server.runtime.worker)
        a=self.owner()
        self.assertEqual(self.req('/api/monitor',cookie=a+'x')[0],401)
        self.assertEqual(self.req('/api/session','POST',{'x':'x'*5000})[0],413)
        self.assertEqual(self.req('/api/telegram/pair','POST',{},a)[0],503)
        self.assertEqual(self.req('/health')[1]['build'],'fomobill-live-0.3')
    def test_metadata_is_projected_only_from_matching_token(self):
        address='So11111111111111111111111111111111111111112'
        pair={'chainId':'solana','pairAddress':PROGRAM,'baseToken':{'address':address},'quoteToken':{},'info':{'socials':[{'type':'twitter','url':'https://x.com/example'},{'type':'twitter','url':'javascript:alert(1)'}]}}
        result=app.Sources(lambda *args:[pair]).market(address)
        context=result['pairs'][0]['context'];self.assertFalse(context['contentRead']);self.assertFalse(context['aiConnected'])
        self.assertEqual(len(context['links']),1);self.assertFalse(context['links'][0]['officialVerified'])
        self.assertEqual(app.Sources(lambda *args:[pair]).market(PROGRAM)['pairs'],[])
    def test_evidence_route_and_read_routes_never_poll(self):
        from test_monitoring_engine import receipt, envelope
        from monitoring import decode_transaction
        r,tx=receipt();sig=r['request']['params'][0];trade=decode_transaction(tx,sig)['trades'][0]
        m=self.server.runtime.monitor;calls=[]
        def fetch(url,body):
            calls.append(body)
            return envelope([{'signature':sig,'slot':tx['slot'],'err':None}]) if body['method']=='getSignaturesForAddress' else (r['body'].encode(),200,{})
        m.fetch=fetch;a,b=self.owner(),self.owner()
        self.req('/api/monitor/watch','POST',{'address':trade['wallet']},a)
        self.assertEqual(m.tick()['processed'],1);before=len(calls)
        self.assertEqual(len(self.req('/api/monitor/wallet?address='+trade['wallet'],cookie=a)[1]['trades']),1)
        result=self.req('/api/monitor/evidence?signature='+sig,cookie=a)
        self.assertEqual(result[0],200);self.assertEqual(result[1]['rawResponse'],r['body'])
        self.assertEqual(self.req('/api/monitor/evidence?signature='+sig,cookie=b)[0],404)
        self.assertEqual(len(calls),before)
    def test_secure_cookie_and_limits(self):
        self.server.allowed_hosts.add('example.com')
        status,data,h=self.req('/api/session','POST',{},extra={'Host':'example.com','Origin':'https://example.com'})
        self.assertEqual(status,200);self.assertIn('; Secure',h['Set-Cookie'])
        self.assertEqual(self.req('/api/session','POST',{},extra={'Host':'evil.example'})[0],403)
        self.server.limiter=app.Limiter(1)
        self.assertEqual(self.req('/api/session','POST',{})[0],200)
        self.assertEqual(self.req('/api/session','POST',{})[0],429)
    def test_telegram_worker_identity_stop_before_drain_and_backoff(self):
        from notifications import DeliveryError
        runtime=self.server.runtime;events=[]
        class Bot:
            def call(self,method,body):return {'is_bot':True,'username':'test_bot'} if method=='getMe' else {'url':''}
            def updates(self,offset):return [{'update_id':3},{'update_id':2}]
            def send(self,*args):raise AssertionError('No unsolicited messages')
            def send_welcome(self,*args):raise AssertionError('No unsolicited photos')
        runtime.bot=Bot();runtime.expected_bot='test_bot'
        runtime.notifications.handle_update=lambda u:events.append(u['update_id'])
        runtime.notifications.drain=lambda send:events.append('drain')
        runtime.telegram_tick();self.assertEqual(events,[2,3,'drain']);self.assertEqual(runtime.telegram_status,'ready')
        runtime.bot.updates=lambda offset:(_ for _ in ()).throw(DeliveryError('rate_limited',900))
        runtime.telegram_tick();self.assertEqual(events,[2,3,'drain']);self.assertGreater(runtime.telegram_due,app.time.time()+899)
        runtime.telegram_due=0;runtime.bot_username=None;runtime.expected_bot='different_bot';runtime.telegram_tick();self.assertIsNone(runtime.bot)
    def test_native_onboarding_watch_status_stop_and_web_isolation(self):
        runtime=self.server.runtime;sent=[];updates=[];photos=[]
        clock=[app.time.time()]
        runtime.notifications.clock=lambda:clock[0]
        class Bot:
            def call(self,method,body):return {'is_bot':True,'username':'test_bot'} if method=='getMe' else {'url':''}
            def updates(self,offset):return list(updates)
            def send(self,chat,text):sent.append((chat,text));return len(sent)
            def send_welcome(self,chat):photos.append(chat);return len(photos)
        runtime.bot=Bot();runtime.expected_bot='test_bot'
        def command(uid,text,chat=12):
            updates[:]=[{'update_id':uid,'message':{'text':text,'chat':{'type':'private','id':chat},'from':{'id':chat}}}]
            with runtime.notifications.connect() as db:db.execute("UPDATE meta SET value=0 WHERE key='send_after'")
            runtime.telegram_tick()
            if text == '/start':
                self.assertEqual(photos[-1],chat)
                clock[0]+=2
                with runtime.notifications.connect() as db:db.execute("UPDATE meta SET value=0 WHERE key='send_after'")
                updates.clear()
                runtime.telegram_tick()
        command(1,'/start')
        self.assertEqual(len(sent),1,'/start must receive a real transport reply')
        self.assertEqual(sent[0][0],12);self.assertIn('/watch',sent[0][1])
        self.assertFalse(runtime.notifications.status('telegram_00000000000000000000000000000012')['active'])
        command(2,'/subscribe')
        self.assertIn('0 selected wallets',sent[-1][1])
        command(3,'/watch '+PROGRAM)
        self.assertEqual(runtime.monitor.snapshot('telegram_00000000000000000000000000000012')['watches'][0]['address'],PROGRAM)
        self.assertFalse(runtime.notifications.status('telegram_00000000000000000000000000000012')['active'])
        self.assertEqual(runtime.monitor.snapshot('telegram_00000000000000000000000000000013')['watches'],[])
        self.assertEqual(self.req('/api/monitor',cookie=self.owner())[1]['watches'],[])
        command(4,'/subscribe');since=runtime.notifications.status('telegram_00000000000000000000000000000012')['since']
        command(5,'/status');self.assertIn(PROGRAM,sent[-1][1]);self.assertIn('ON',sent[-1][1])
        command(6,'/start');self.assertEqual(runtime.notifications.status('telegram_00000000000000000000000000000012')['since'],since)
        command(7,'/stop');self.assertIn('OFF',sent[-1][1]);self.assertFalse(runtime.notifications.status('telegram_00000000000000000000000000000012')['active'])
        command(8,'/unwatch '+PROGRAM)
        self.assertEqual(runtime.monitor.snapshot('telegram_00000000000000000000000000000012')['watches'],[])
        command(9,'/watch not-an-address');self.assertIn('Invalid',sent[-1][1])
        self.assertEqual(runtime.monitor.snapshot('telegram_00000000000000000000000000000012')['watches'],[])

    def test_telegram_poll_cadence_does_not_increase_rpc_cadence(self):
        from unittest.mock import patch
        runtime=self.server.runtime;events=[];waits=[]
        class Stop:
            def set(self):pass
            def is_set(self):return len(waits)>=3
            def wait(self,seconds):waits.append(seconds)
        runtime.stop_event=Stop()
        runtime.telegram_tick=lambda:events.append('telegram')
        runtime.monitor.tick=lambda:events.append('monitor')
        with patch.object(app.time,'monotonic',return_value=1000):runtime.run()
        self.assertEqual(events,['telegram','monitor','telegram','telegram'])
        self.assertEqual(waits,[2,2,2])
        self.assertEqual(self.req('/health')[1]['telegramBuild'],'receipts-0.3.4')

    def test_group_notification_evidence_gates(self):
        import copy
        from monitoring import b58
        runtime=self.server.runtime;owner='a'*64;now=app.time.time();a=b58(bytes([1])*32);b=b58(bytes([2])*32);sig=b58(bytes([3])*64)
        trades=[{'id':str(i),'side':'buy','mint':PROGRAM,'wallet':w,'blockTime':now-20+i,'observedAt':now-10,'signature':sig,'commitment':'finalized','qualifiesForGroup':True,'signers':[w]} for i,w in enumerate((a,b))]
        snapshot={'watches':[{'address':a},{'address':b}],'trades':trades,'groups':[{'id':'test-only','mint':PROGRAM,'triggerTime':now-19,'participants':[a,b],'evidenceIds':['0','1']}],'coverage':{'paused':False,'lastRun':{'status':'sampled','observedAt':now-5},'cursors':[{'polled':now-5,'coverage':{'status':'sampled'}}]}}
        runtime.monitor.evidence=lambda *args:{'status':'ok'};queued=[]
        runtime.notifications.enqueue=lambda *args,**kwargs:queued.append((args,kwargs))
        runtime.monitor.snapshot=lambda *args:copy.deepcopy(snapshot)
        runtime.enqueue_groups(owner);self.assertEqual(len(queued),1);self.assertEqual(queued[0][1]['captured_at'],now-10)
        for modify in [lambda s:s['coverage'].update(paused=True),lambda s:s['coverage']['lastRun'].update(status='partial'),lambda s:s['trades'][0].update(qualifiesForGroup=False),lambda s:s['trades'][0].update(signers=[]),lambda s:s['trades'][0].update(side='sell'),lambda s:s['trades'].pop(),lambda s:s['coverage']['lastRun'].update(observedAt=now-500)]:
            bad=copy.deepcopy(snapshot);modify(bad);runtime.monitor.snapshot=lambda *args:bad
            runtime.enqueue_groups(owner);self.assertEqual(len(queued),1)
    def test_main_sigterm_clean_stop_without_provider_work(self):
        import os
        import subprocess
        import signal
        import shutil
        import urllib.request
        with tempfile.TemporaryDirectory() as directory:
            package=Path(directory)/'package';package.mkdir()
            for name in ('app.py','monitoring.py','notifications.py','holders.py','holder_history_sources.py'):shutil.copy2(Path(app.__file__).parent/name,package/name)
            shutil.copytree(Path(app.__file__).parent/'static',package/'static')
            data=Path(directory)/'data'
            env={**os.environ,'HOST':'127.0.0.1','PORT':'0','DATA_DIR':str(data),'FOMOBILL_MONITOR_ENABLED':'1','FOMOBILL_TELEGRAM_BOT_TOKEN':'','FOMOBILL_TELEGRAM_USERNAME':''}
            process=subprocess.Popen(['python3','-u',str(package/'app.py')],cwd=package,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                line=process.stdout.readline();self.assertIn('FOMOBILL read-only server',line)
                port=app.re.search(r"'127.0.0.1', (\d+)",line)[1]
                with urllib.request.urlopen('http://127.0.0.1:'+port+'/health',timeout=5) as response:self.assertEqual(json.load(response)['build'],'fomobill-live-0.3')
                process.send_signal(signal.SIGTERM);stdout,stderr=process.communicate(timeout=10)
                self.assertEqual(process.returncode,0,stderr)
                self.assertTrue((data/'monitor.sqlite3').is_file())
            finally:
                if process.poll() is None:process.kill();process.communicate()
    def test_cookie_survives_server_restart_key_and_single_worker(self):
        a=self.owner();owner=self.server.runtime.authenticate(a)
        other=app.Runtime(Path(self.tmp.name),monitor=self.server.runtime.monitor)
        self.assertEqual(other.authenticate(a),owner)
        self.server.runtime.monitor.tick=lambda: {'status':'sampled'}
        self.server.runtime.start();thread=self.server.runtime.worker
        self.server.runtime.start();self.assertIs(thread,self.server.runtime.worker)
        with self.assertRaises(RuntimeError):other.start()
        self.server.runtime.close();self.assertFalse(thread.is_alive())

if __name__=='__main__':unittest.main()

"""Deterministic offline tests. Altered receipts/test doubles are synthetic only."""
import base64
import copy
import hashlib
import json
import pathlib
import tempfile
import unittest
from monitoring import Monitor, decode_transaction, group_trades, PROGRAM, RPCS, b58, FIELDS

FIX=pathlib.Path(__file__).parent/'tests/fixtures'
NS='a'*32

def receipt(name='live-tx-1.json'):
    r=json.loads((FIX/name).read_text());return r,json.loads(r['body'])['result']

def envelope(result):return json.dumps({'jsonrpc':'2.0','id':1,'result':result}).encode(),200,{}

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=pathlib.Path(self.temp.name)/'m.db';self.now=[1000.]
        self.r,self.tx=receipt();self.sig=self.r['request']['params'][0];self.calls=[]
    def tearDown(self):self.temp.cleanup()
    def engine(self,fetch=None):return Monitor(self.path,fetch or self.fetch,lambda:self.now[0])
    def fetch(self,url,body):
        self.calls.append((url,body))
        return envelope([{'signature':self.sig,'slot':self.tx['slot'],'err':None}]) if body['method']=='getSignaturesForAddress' else (self.r['body'].encode(),200,{})
    def test_restart_dedup_and_receipt_replay(self):
        m=self.engine();m.watch(NS,PROGRAM);self.assertEqual(m.tick()['processed'],1)
        with m.connect(True) as db:
            first=db.execute('select observed from transactions').fetchone()[0]
            e=json.loads(db.execute("select payload from observations where kind='trades'").fetchone()[0])
        m.watch(NS,e['wallet']);before=len(self.calls);self.assertEqual(len(m.wallet(NS,e['wallet'])['trades']),1);self.assertEqual(len(self.calls),before)
        raw=m.evidence(NS,self.sig);self.assertEqual(hashlib.sha256(raw['rawResponse'].encode()).hexdigest(),raw['sha256'])
        self.assertEqual(m.evidence('b'*32,self.sig)['status'],'not_found')
        self.now[0]+=61;m=self.engine();m.tick()
        with m.connect(True) as db:
            self.assertEqual(db.execute('select count(*) from transactions').fetchone()[0],1)
            self.assertEqual(db.execute('select observed from transactions').fetchone()[0],first)
        self.assertEqual(sum(b['method']=='getTransaction' for _,b in self.calls),1)
    def test_null_error_cursor_does_not_advance(self):
        def null(url,body):
            if body['method']=='getSignaturesForAddress':return self.fetch(url,body)
            return envelope(None)
        m=self.engine(null);m.watch(NS,PROGRAM);r=m.tick();self.assertEqual(r['status'],'partial')
        with m.connect(True) as db:
            c=db.execute('select * from cursors').fetchone();self.assertIsNone(c['head']);self.assertEqual(json.loads(c['queue']),[self.sig]);self.assertEqual(db.execute('select count(*) from transactions').fetchone()[0],0)
        self.now[0]+=121;m=self.engine();self.assertEqual(m.tick()['processed'],1)
    def test_budget_queue_continues_after_restart(self):
        sigs=[b58(bytes([i])*64) for i in range(1,6)]
        def fetch(url,body):
            self.calls.append(body)
            if body['method']=='getSignaturesForAddress':return envelope([{'signature':s,'slot':1,'err':None} for s in sigs])
            s=body['params'][0]
            if s==sigs[1] and url==RPCS[0]:return b'rate limited',429,{'Retry-After':'1'}
            t=copy.deepcopy(self.tx);t['transaction']['signatures']=[s];return envelope(t)
        m=self.engine(fetch);m.watch(NS,PROGRAM);m.tick()
        with m.connect(True) as db:
            c=db.execute('select * from cursors').fetchone();self.assertEqual(json.loads(c['queue']),[sigs[-1]]);self.assertIsNone(c['head'])
        self.assertEqual(len(self.calls),6)
        self.now[0]+=61;m=self.engine(fetch);m.tick()
        with m.connect(True) as db:self.assertEqual(db.execute('select head from cursors').fetchone()[0],sigs[0])
    def test_rate_after_persists_and_no_tight_loop(self):
        def fetch(url,body):self.calls.append(body);return b'blocked',429,{'Retry-After':'7200'}
        m=self.engine(fetch);m.watch(NS,PROGRAM);m.tick();self.assertEqual(len(self.calls),2)
        self.now[0]+=61;self.engine(fetch).tick();self.assertEqual(len(self.calls),2)
        with m.connect(True) as db:self.assertEqual(m._get(db,'cooldown:'+RPCS[0]),8200)
    def test_readonly_snapshot_does_not_write(self):
        m=self.engine();m.watch(NS,PROGRAM);before=self.path.read_bytes();m.snapshot(NS);m.wallet(NS,PROGRAM);self.assertEqual(before,self.path.read_bytes());self.assertEqual(self.calls,[])
        with m.connect(True) as db:
            with self.assertRaises(Exception):db.execute("insert into state values ('test','0')")
    def test_registry_caps_and_expiry(self):
        m=self.engine()
        for i in range(1,9):m.watch(NS,b58(bytes([i])*32))
        with self.assertRaises(ValueError):m.watch(NS,PROGRAM)
        for i in range(9,33):m.watch(str(i).zfill(32),b58(bytes([i])*32))
        with self.assertRaises(ValueError):m.watch('z'*32,PROGRAM)
        self.now[0]+=604801;m.watch(NS,PROGRAM);self.assertEqual(len(m.snapshot(NS)['watches']),1)
    def test_catchup_before_cursor(self):
        m=self.engine();m.watch(NS,PROGRAM);m.tick();self.now[0]+=61
        sigs=[b58(bytes([i])*64) for i in range(10,15)]
        def fetch(url,body):
            if body['method']=='getSignaturesForAddress':
                options=body['params'][1];page=[self.sig] if 'before' in options else sigs
                self.calls.append(body);return envelope([{'signature':s,'slot':1,'err':None} for s in page])
            t=copy.deepcopy(self.tx);t['transaction']['signatures']=[body['params'][0]];return envelope(t)
        m=self.engine(fetch);m.tick()
        with m.connect(True) as db:
            c=db.execute('select * from cursors').fetchone();self.assertEqual(c['before_sig'],sigs[-1]);self.assertEqual(c['head'],self.sig)
        self.now[0]+=61;m=self.engine(fetch);m.tick()
        with m.connect(True) as db:self.assertEqual(db.execute('select head from cursors').fetchone()[0],sigs[0])
        self.assertEqual(self.calls[-1]['params'][1]['before'],sigs[-1])
    def test_malformed_page_and_transaction_gap(self):
        m=self.engine(lambda u,b:envelope([{'signature':'bad'}]));m.watch(NS,PROGRAM);self.assertEqual(m.tick()['status'],'partial')
        self.now[0]+=61
        def fetch(u,b):return self.fetch(u,b) if b['method']=='getSignaturesForAddress' else envelope({'malformed':True})
        m=self.engine(fetch);self.assertEqual(m.tick()['status'],'partial')
        with m.connect(True) as db:self.assertTrue(db.execute('select error from receipts order by id desc').fetchone()[0].startswith('Decoder:'))
    def test_retention_keeps_every_observation_receipt(self):
        m=self.engine(lambda u,b:envelope([]));m.watch(NS,PROGRAM)
        with m.connect() as db:
            for i in range(405):
                sig=b58(i.to_bytes(64,'big'))
                rid=db.execute('insert into receipts(observed,endpoint,request,body,sha256,error) values (?,?,?,?,?,?)',(i,RPCS[0],'{}',b'{}',hashlib.sha256(b'{}').hexdigest(),None)).lastrowid
                db.execute('insert into transactions values (?,?,?)',(sig,rid,i))
                db.execute('insert into touches values (?,?)',(PROGRAM,sig))
                db.execute('insert into observations values (?,?,?,?)',(sig,sig,'trades',json.dumps({'wallet':PROGRAM,'blockTime':i,'id':sig,'side':'sell','mint':PROGRAM,'qualifiesForGroup':False})))
        m.tick()
        with m.connect(True) as db:
            self.assertEqual(db.execute('select count(*) from transactions').fetchone()[0],400)
            self.assertEqual(db.execute('select count(*) from observations').fetchone()[0],400)
            self.assertEqual(db.execute('select count(*) from observations o join transactions t on o.signature=t.signature join receipts r on r.id=t.receipt').fetchone()[0],400)
            self.assertEqual(db.execute('pragma foreign_key_check').fetchall(),[])
    def test_synthetic_groups_alert_restart_and_common_source(self):
        # SQL-only synthetic analytic seed: never used by live collector/fixtures.
        m=self.engine(lambda u,b:envelope([]));a=b58(bytes([7])*32);b=b58(bytes([8])*32)
        for w in (a,b):m.watch(NS,w)
        with m.connect() as db:
            rid=db.execute('insert into receipts(observed,endpoint,request,body,sha256,error) values (?,?,?,?,?,?)',(1,RPCS[0],'{}',b'{}',hashlib.sha256(b'{}').hexdigest(),None)).lastrowid
            db.execute('insert into transactions values (?,?,?)',(self.sig,rid,1))
            for i,w in enumerate((a,b)):
                e={'id':'unit-trade-'+str(i),'signature':self.sig,'wallet':w,'mint':PROGRAM,'side':'buy','blockTime':100+i,'qualifiesForGroup':True}
                db.execute('insert into observations values (?,?,?,?)',(e['id'],self.sig,'trades',json.dumps(e)))
                t={'id':'unit-transfer-'+str(i),'signature':self.sig,'source':PROGRAM,'destination':w,'blockTime':90+i,'lamportsRaw':'10'}
                db.execute('insert into observations values (?,?,?,?)',(t['id'],self.sig,'transfers',json.dumps(t)))
        m.tick();s=m.snapshot(NS);self.assertEqual(len(s['groups']),1);self.assertEqual(len(s['alerts']),1);self.assertEqual(len(s['commonSources']),1)
        created=s['alerts'][0]['createdAt'];self.now[0]+=61;m=self.engine(lambda u,b:envelope([]));m.tick();self.assertEqual(m.snapshot(NS)['alerts'][0]['createdAt'],created)
        self.assertEqual(m.snapshot('b'*32)['alerts'],[]);m.unwatch(NS,b);self.assertEqual(m.snapshot(NS)['alerts'],[])
    def test_stopped_worker_does_nothing(self):
        import threading
        stop=threading.Event();stop.set();self.engine().run(stop);self.assertEqual(self.calls,[])

class DecoderTests(unittest.TestCase):
    def setUp(self):self.r,self.tx=receipt();self.sig=self.r['request']['params'][0]
    def test_real_sell(self):
        d=decode_transaction(self.tx,self.sig);self.assertEqual(len(d['trades']),1);e=d['trades'][0]
        self.assertEqual(e['side'],'sell');self.assertEqual(e['tokenAmountRaw'],'618862158608');self.assertFalse(e['qualifiesForGroup']);self.assertEqual(e['wallet'],'FzTkNaz6dG514FsGSD7VXMuFiDtEQBmjCqsZkr5hTpcy')
    def test_real_buy_exact_quote(self):
        live=json.loads((FIX/'live-ingestion.json').read_text());found=[]
        for r in live['receipts']:
            p=json.loads(r['request'])
            if p['method']=='getTransaction':found+=decode_transaction(json.loads(r['body']).get('result'),p['params'][0])['trades']
        self.assertTrue(any(t['side']=='buy' for t in found))
    def test_version_one_real_shape(self):
        r,t=receipt('real-pump-transaction.json');self.assertEqual(t['version'],1);self.assertEqual(decode_transaction(t,r['request']['params'][0])['gaps'],[])
    def test_idl_schema_matches_pinned_fields(self):
        d=json.loads((FIX/'pump-idl.json').read_text());fields=next(x for x in d['types'] if x['name']=='TradeEvent')['type']['fields']
        self.assertEqual([x['name'] for x in fields],[x[0] for x in FIELDS]);self.assertEqual(d['address'],PROGRAM)
    def test_failed_caught_failure_and_unbalanced(self):
        for kind in ['overall','caught','unbalanced']:
            t=copy.deepcopy(self.tx)
            if kind=='overall':t['meta']['err']={'failure':1}
            elif kind=='caught':t['meta']['logMessages'].append('Program '+PROGRAM+' failed: synthetic failure')
            else:t['meta']['logMessages'].pop()
            self.assertEqual(decode_transaction(t,self.sig)['trades'],[])
    def test_wrong_program_discriminator_and_corrupt_borsh(self):
        for mode in ['program','disc','truncated','bool','extra']:
            t=copy.deepcopy(self.tx);logs=t['meta']['logMessages']
            if mode=='program':t['meta']['logMessages']=[l.replace(PROGRAM,'11111111111111111111111111111111') for l in logs]
            else:
                i=next(i for i,l in enumerate(logs) if l.startswith('Program data: ') and base64.b64decode(l[14:])[:8]==bytes([189,219,127,211,78,230,97,238]))
                raw=bytearray(base64.b64decode(logs[i][14:]))
                if mode=='disc':raw[0]=0
                elif mode=='truncated':raw=raw[:-1]
                elif mode=='extra':raw+=b'x'
                else:raw[56]=2
                logs[i]='Program data: '+base64.b64encode(raw).decode()
            self.assertEqual(decode_transaction(t,self.sig)['trades'],[],mode)
    def test_delta_only_is_not_trade(self):
        t=copy.deepcopy(self.tx);t['meta']['logMessages']=[];self.assertEqual(decode_transaction(t,self.sig)['trades'],[])
    def test_missing_time_not_replaced_with_now(self):
        t=copy.deepcopy(self.tx);t['blockTime']=None;events=decode_transaction(t,self.sig)['trades'];self.assertIsNone(events[0]['blockTime']);self.assertEqual(group_trades(events),[])
    def test_group_unique_wallets_sell_progression(self):
        def e(i,w,side,t):return {'id':str(i),'wallet':w,'mint':'m','side':side,'blockTime':t,'qualifiesForGroup':True}
        a=[e(1,'a','buy',100),e(2,'a','buy',101)];self.assertEqual(group_trades(a),[])
        a+=[e(3,'b','sell',102)];self.assertEqual(group_trades(a),[])
        a+=[e(4,'b','buy',110),e(5,'a','sell',120),e(6,'b','buy',130)]
        g=group_trades(a);self.assertEqual(len(g),1);self.assertEqual(g[0]['participants'],['a','b']);self.assertEqual([x['side'] for x in g[0]['progression']],['sell','buy'])
        self.assertEqual(group_trades([e(1,'a','buy',1),e(2,'b','buy',500)]),[])

if __name__=='__main__':unittest.main()

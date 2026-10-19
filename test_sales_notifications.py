"""Offline only: historical receipt replay is NOT fresh-live acceptance."""
import tempfile
import unittest
import json
import hashlib
import sqlite3
import copy
from unittest.mock import patch
import app
from monitoring import decode_transaction, group_trades
from pathlib import Path
from notifications import Notifications


def update(i, text, chat=12):
    return {'update_id':i,'message':{'text':text,'chat':{'type':'private','id':chat},'from':{'id':chat}}}


class SalesConsentTests(unittest.TestCase):
    def test_legacy_migration_preserves_all_state(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'n.db'
            with sqlite3.connect(path) as db:
                db.executescript("""CREATE TABLE recipients(owner TEXT PRIMARY KEY,chat INTEGER UNIQUE,active INTEGER NOT NULL DEFAULT 0,since REAL NOT NULL DEFAULT 0);
                INSERT INTO recipients VALUES('a',12,1,123),('b',13,0,456);
                CREATE TABLE meta(key TEXT PRIMARY KEY,value REAL NOT NULL);
                INSERT INTO meta VALUES('offset',99),('send_after',800);
                CREATE TABLE outbox(owner TEXT,event TEXT,text TEXT,created REAL,expires REAL,next_try REAL,attempts INTEGER DEFAULT 0,state TEXT DEFAULT 'pending',message_id INTEGER,PRIMARY KEY(owner,event));
                INSERT INTO outbox VALUES('a','pump:group:old','historical',1,2,3,1,'sent',55);""")
            n=Notifications(path,clock=lambda:1000)
            with n.connect() as db:
                self.assertEqual([tuple(r) for r in db.execute('SELECT owner,chat,active,since FROM recipients ORDER BY owner')],[('a',12,1,123),('b',13,0,456)])
                self.assertEqual(tuple(db.execute('SELECT event,state,message_id FROM outbox').fetchone()),('pump:group:old','sent',55))
                self.assertEqual(db.execute("SELECT value FROM meta WHERE key='send_after'").fetchone()[0],800)
                self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0],'ok')
            self.assertEqual(n.offset(),99);self.assertFalse(n.status('a')['sales'])
            self.assertEqual(Notifications(path).status('a'),n.status('a'))

    def test_separate_opt_in_idempotence_pause_resume(self):
        with tempfile.TemporaryDirectory() as d:
            now=[1000]; n=Notifications(Path(d)/'n.db',clock=lambda:now[0])
            n.handle_update(update(1,'/subscribe'))
            owner='telegram_'+str(12).zfill(32)
            self.assertFalse(n.status(owner).get('sales',False))
            self.assertEqual(n.handle_update(update(2,'/sales_on')),'sales_on')
            self.assertTrue(n.status(owner)['sales'])
            now[0]=1010;n.handle_update(update(3,'/sales_on'))
            self.assertEqual(n.status(owner)['salesSince'],1000)
            n.handle_update(update(4,'/stop'))
            self.assertFalse(n.status(owner)['active'])
            n.handle_update(update(5,'/subscribe'))
            self.assertTrue(n.status(owner)['sales'])
            self.assertEqual(n.status(owner)['since'],1010)
            n.handle_update(update(6,'/sales_off'))
            self.assertFalse(n.status(owner)['sales'])
            self.assertTrue(n.status(owner)['active'])


class HistoricalSalesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=1791968330
        with patch.object(app.time,'time',return_value=1791968000):
            self.runtime=app.Runtime(Path(self.tmp.name))
        self.n=self.runtime.notifications;self.n.clock=lambda:self.now
        receipts=json.loads((Path(app.__file__).parent/'static/historical-case.json').read_text())['receipts']
        trades=[]
        for r in receipts:
            self.assertEqual(hashlib.sha256(r['rawResponse'].encode()).hexdigest(),r['sha256'])
            decoded=decode_transaction(json.loads(r['rawResponse'])['result'],r['signature'])
            for t in decoded['trades']:
                t.update(observedAt=r['observedAt'],commitment='finalized');trades.append(t)
        self.snap={'trades':trades,'groups':group_trades(trades),'watches':[{'address':w} for w in {t['wallet'] for t in trades}], 'coverage':{'paused':False,'lastRun':{'status':'sampled','observedAt':self.now},'cursors':[]}}
        self.runtime.monitor.snapshot=lambda owner:copy.deepcopy(self.snap)
        self.runtime.monitor.evidence=lambda *args:{'status':'ok'}
        self.owner='telegram_'+str(12).zfill(32)
        self.now=1791968001
        self.n.handle_update(update(1,'/subscribe'));self.n.handle_update(update(2,'/sales_on'))
        self.now=1791968330

    def tearDown(self):self.tmp.cleanup()

    def rows(self):
        with self.n.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM outbox')]

    def tick(self):
        with patch.object(app.time,'time',return_value=self.now):self.runtime.enqueue_groups(self.owner)

    def test_saved_receipts_chain_sale_dedupe_retry_and_pause(self):
        self.tick();self.tick()
        rows=self.rows();sales=[r for r in rows if r['event'].startswith('pump:sale:')]
        self.assertEqual(len(sales),1)
        self.assertIn('not a full exit',sales[0]['text'])
        self.assertIn('08:55:05',sales[0]['text'])
        self.assertEqual(len(rows),2)
        self.n.drain(lambda *a:100)
        self.now+=2
        self.assertEqual(self.n.drain(lambda *a:(_ for _ in ()).throw(RuntimeError())),'retry')
        self.now+=31;self.tick();self.assertEqual(self.n.drain(lambda *a:101),'sent')
        self.assertEqual([r['message_id'] for r in self.rows() if r['event'].startswith('pump:sale:')],[101])
        self.n.handle_update(update(3,'/stop'));self.tick()
        self.assertEqual(len(self.rows()),2)

    def test_sale_gates_and_pending_evidence_loss(self):
        original=copy.deepcopy(self.snap)
        sale=next(t for t in original['trades'] if t['side']=='sell')
        for changes in ({'mint':'wrong'},{'wallet':'wrong'},{'blockTime':1791968101},
                        {'blockTime':1791968100},{'blockTime':None},{'qualifiesForGroup':False},
                        {'signers':[]},{'commitment':'confirmed'},{'observedAt':self.now-301},
                        {'observedAt':self.now+1}):
            self.snap=copy.deepcopy(original)
            next(t for t in self.snap['trades'] if t['side']=='sell').update(changes)
            self.tick()
            self.assertFalse(any(r['event'].startswith('pump:sale:') for r in self.rows()),changes)
        self.snap=original;self.tick()
        self.runtime.monitor.evidence=lambda owner,sig: {'status':'not_found' if sig==sale['signature'] else 'ok'}
        self.tick()
        self.assertEqual([r['state'] for r in self.rows() if r['event'].startswith('pump:sale:')],['suppressed'])

    def test_restart_resume_and_default_off_no_backlog(self):
        self.n.handle_update(update(3,'/sales_off'));self.tick()
        self.assertEqual(len(self.rows()),1)
        self.n.handle_update(update(4,'/sales_on'));self.tick()
        self.assertEqual(len(self.rows()),1)
        with self.n.connect() as db:db.execute('UPDATE recipients SET sales_since=1791968001')
        self.runtime.sales_boot=self.now;self.tick()
        self.assertEqual(len(self.rows()),1)
        self.runtime.sales_boot=1791968000
        self.n.handle_update(update(5,'/stop'));self.n.handle_update(update(6,'/subscribe'));self.tick()
        self.assertEqual(len(self.rows()),1)

    def test_never_admitted_stale_group_does_not_spawn_sales(self):
        trigger=self.snap['groups'][0]['triggerTime']
        for t in self.snap['trades']:
            if t['side']=='buy' and t['blockTime']==trigger:t['observedAt']=self.now-301
        self.tick()
        self.assertEqual(self.rows(),[])

    def test_sale_status_is_visible_in_help_and_status(self):
        self.assertIn('/sales_on',self.n.command_reply('help',True))
        self.assertIn('Participant sale alerts: ON',self.runtime.telegram_watch_status(self.owner))

    def test_distinct_sale_events_and_recipient_dedup(self):
        sale=copy.deepcopy(next(t for t in self.snap['trades'] if t['side']=='sell'))
        sale['id']=sale['signature']+':999';sale['logIndex']=999
        self.snap['trades'].append(sale) # synthetic identity edge case, not chain evidence
        self.tick()
        self.now=1791968001
        self.n.handle_update(update(3,'/subscribe',13));self.n.handle_update(update(4,'/sales_on',13))
        self.now=1791968330
        with patch.object(app.time,'time',return_value=self.now):self.runtime.enqueue_groups('telegram_'+str(13).zfill(32))
        self.tick()
        sales=[r for r in self.rows() if r['event'].startswith('pump:sale:')]
        self.assertEqual(len(sales),4)
        self.assertEqual(len({r['event'] for r in sales}),2)

    def test_stale_source_cancels_pending_and_no_resume_resurrection(self):
        self.tick();self.snap['coverage']['lastRun']['status']='partial';self.tick()
        self.assertEqual([r['state'] for r in self.rows() if r['event'].startswith('pump:sale:')],['suppressed'])
        self.snap['coverage']['lastRun']['status']='sampled';self.tick()
        self.assertEqual([r['state'] for r in self.rows() if r['event'].startswith('pump:sale:')],['suppressed'])

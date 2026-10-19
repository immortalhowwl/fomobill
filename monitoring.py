"""Bounded finalized Solana monitoring. No import-time I/O or background thread."""
import base64
import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from email.utils import parsedate_to_datetime

PROGRAM = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
RPCS = ('https://api.mainnet-beta.solana.com', 'https://solana-rpc.publicnode.com')
IDL_SOURCE = 'https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/idl/pump.json'
ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
MAX_BODY = 262144
# Exact official TradeEvent schema fetched 2026-10-12; trailing bytes rejected.
FIELDS = [('mint','pubkey'),('sol_amount','u64'),('token_amount','u64'),('is_buy','bool'),('user','pubkey'),('timestamp','i64'),('virtual_sol_reserves','u64'),('virtual_token_reserves','u64'),('real_sol_reserves','u64'),('real_token_reserves','u64'),('fee_recipient','pubkey'),('fee_basis_points','u64'),('fee','u64'),('creator','pubkey'),('creator_fee_basis_points','u64'),('creator_fee','u64'),('track_volume','bool'),('total_unclaimed_tokens','u64'),('total_claimed_tokens','u64'),('current_sol_volume','u64'),('last_update_timestamp','i64'),('ix_name','string'),('mayhem_mode','bool'),('cashback_fee_basis_points','u64'),('cashback','u64'),('buyback_fee_basis_points','u64'),('buyback_fee','u64'),('shareholders','shareholders'),('quote_mint','pubkey'),('quote_amount','u64'),('virtual_quote_reserves','u64'),('real_quote_reserves','u64'),('holder_rewards_bps','u64'),('holder_rewards','u64'),('creator_fee_unclaimed','u64')]

def b58(raw):
    n=int.from_bytes(raw,'big'); out=''
    while n: n,r=divmod(n,58);out=ALPHABET[r]+out
    return '1'*(len(raw)-len(raw.lstrip(b'\0')))+out

def address(v):
    if not isinstance(v,str) or not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,44}',v): raise ValueError('Invalid Solana address')
    n=0
    for c in v:n=n*58+ALPHABET.index(c)
    if (n.bit_length()+7)//8+len(v)-len(v.lstrip('1')) !=32:raise ValueError('Address must decode to 32 bytes')
    return v

def namespace(v):
    if not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',v):raise ValueError('Namespace capability required')
    return hashlib.sha256(v.encode()).hexdigest()

def signature(v):
    if not isinstance(v,str) or not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{64,88}',v):raise ValueError('Invalid signature')
    return v

class Reader:
    def __init__(self,data):self.data=data;self.pos=0
    def take(self,n):
        if self.pos+n>len(self.data):raise ValueError('Truncated event')
        v=self.data[self.pos:self.pos+n];self.pos+=n;return v
    def read(self,k):
        if k=='pubkey':return b58(self.take(32))
        if k in ('u64','i64','u32','u16'):return int.from_bytes(self.take({'u64':8,'i64':8,'u32':4,'u16':2}[k]),'little',signed=k=='i64')
        if k=='bool':
            v=self.take(1)[0]
            if v>1:raise ValueError('Invalid bool')
            return bool(v)
        n=self.read('u32')
        if n>128:raise ValueError('Event collection bound')
        if k=='string':return self.take(n).decode()
        return [{'address':self.read('pubkey'),'share_bps':self.read('u16')} for _ in range(n)]

def decode_transaction(tx,sig):
    """Fail closed; protocol events, never token-balance-only inference."""
    empty={'trades':[], 'transfers':[], 'gaps':[]}
    try:
        if not isinstance(tx,dict) or tx.get('version') not in ('legacy',0,1):raise ValueError('Unsupported transaction shape/version')
        meta=tx['meta'];message=tx['transaction']['message']
        if sig not in tx['transaction']['signatures']:raise ValueError('Signature mismatch')
        if 'err' not in meta:raise ValueError('Missing execution status')
        if meta['err'] is not None:return empty
        if type(tx['slot']) is not int:raise ValueError('Missing slot')
        stamp=tx.get('blockTime')
        if stamp is not None and (type(stamp) is not int or stamp<0):raise ValueError('Invalid chain time')
        keys=message['accountKeys']
        if not isinstance(keys,list) or any(not isinstance(k,dict) for k in keys):raise ValueError('Expected jsonParsed keys')
        signers=[address(k['pubkey']) for k in keys if k.get('signer') is True]
        logs=meta.get('logMessages')
        if not isinstance(logs,list) or len(logs)>2000:raise ValueError('Missing/bounded logs')
        stack=[];trades=[]
        for i,line in enumerate(logs):
            if not isinstance(line,str):raise ValueError('Malformed logs')
            if re.match(r'Program \w+ failed:',line):raise ValueError('Caught invocation failure; omitted')
            inv=re.fullmatch(r'Program (\w+) invoke \[(\d+)\]',line)
            done=re.fullmatch(r'Program (\w+) success',line)
            if inv:
                if int(inv[2])!=len(stack)+1:raise ValueError('Invocation depth mismatch')
                stack.append(inv[1])
            elif done:
                if not stack or stack.pop()!=done[1]:raise ValueError('Invocation mismatch')
            elif line.startswith('Program data: ') and stack and stack[-1]==PROGRAM:
                raw=base64.b64decode(line[14:],validate=True)
                if raw[:8]!=bytes([189,219,127,211,78,230,97,238]):continue
                rd=Reader(raw[8:]);e={name:rd.read(kind) for name,kind in FIELDS}
                if rd.pos!=len(raw)-8:raise ValueError('Unknown TradeEvent layout')
                if e['token_amount']<=0 or e['ix_name'] not in ('buy','sell','buy_exact_sol_in','buy_exact_quote_in'):raise ValueError('Invalid trade event')
                if e['is_buy'] != (e['ix_name']!='sell'):raise ValueError('Inconsistent event side')
                if stamp is not None and abs(e['timestamp']-stamp)>300:raise ValueError('Event timestamp mismatch')
                trades.append({'id':sig+':'+str(i),'signature':sig,'logIndex':i,'wallet':e['user'],'mint':e['mint'],'side':'buy' if e['is_buy'] else 'sell','tokenAmountRaw':str(e['token_amount']),'quoteMint':e['quote_mint'],'quoteAmountRaw':str(e['quote_amount']),'solAmountRaw':str(e['sol_amount']) if e['quote_mint'] in ('11111111111111111111111111111111','So11111111111111111111111111111111111111112') else None,'blockTime':stamp,'eventTime':e['timestamp'],'slot':tx['slot'],'signers':signers,'invocationStack':list(stack),'qualifiesForGroup':e['user'] in signers and all(p==PROGRAM for p in stack),'attribution':'Protocol event account; not verified human or owner','source':'Pump TradeEvent','schema':'pump-trade-2026-10-12','url':'https://solscan.io/tx/'+sig})
        if stack:raise ValueError('Truncated invocation logs')
        transfers=[]
        instructions=list(message.get('instructions',[]))
        for group in meta.get('innerInstructions') or []:instructions.extend(group['instructions'])
        for i,ins in enumerate(instructions[:2000]):
            if ins.get('programId')!='11111111111111111111111111111111':continue
            parsed=ins.get('parsed',{})
            if parsed.get('type')!='transfer':continue
            info=parsed['info'];amt=info['lamports']
            if type(amt) is not int or amt<=0:continue
            transfers.append({'id':sig+':transfer:'+str(i),'signature':sig,'source':address(info['source']),'destination':address(info['destination']),'lamportsRaw':str(amt),'blockTime':stamp,'slot':tx['slot'],'url':'https://solscan.io/tx/'+sig,'claim':'Observed native SOL transfer; no ownership inference'})
        if len(trades)>16 or len(transfers)>64:raise ValueError('Observation bound exceeded')
        return {'trades':trades,'transfers':transfers,'gaps':[]}
    except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as e:
        empty['gaps']=[str(e)[:180]];return empty

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('RPC redirect rejected')

def transport(url,payload):
    """Return exact response bytes, HTTP status, headers. Injectable only in tests."""
    req=Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','User-Agent':'FOMOBILL-monitor/1'})
    try:
        with build_opener(NoRedirect()).open(req,timeout=7) as r:return r.read(MAX_BODY+1),r.status,dict(r.headers)
    except HTTPError as e:return e.read(MAX_BODY+1),e.code,dict(e.headers)

SCHEMA='''
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watches(ns TEXT,address TEXT,expires REAL,PRIMARY KEY(ns,address));
CREATE TABLE IF NOT EXISTS cursors(address TEXT PRIMARY KEY,head TEXT,before_sig TEXT,target TEXT,queue TEXT NOT NULL DEFAULT '[]',polled REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY,observed REAL,endpoint TEXT,request TEXT,body BLOB,sha256 TEXT,error TEXT);
CREATE TABLE IF NOT EXISTS transactions(signature TEXT PRIMARY KEY,receipt INTEGER REFERENCES receipts(id),observed REAL);
CREATE TABLE IF NOT EXISTS touches(address TEXT,signature TEXT REFERENCES transactions(signature) ON DELETE CASCADE,PRIMARY KEY(address,signature));
CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY,signature TEXT REFERENCES transactions(signature) ON DELETE CASCADE,kind TEXT,payload TEXT);
CREATE TABLE IF NOT EXISTS alerts(ns TEXT,id TEXT,payload TEXT,created REAL,PRIMARY KEY(ns,id));
CREATE INDEX IF NOT EXISTS obs_signature ON observations(signature);
CREATE INDEX IF NOT EXISTS touches_signature ON touches(signature);
'''

class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

class Monitor:
    def __init__(self,path,fetch=None,clock=None):
        self.path=Path(path).resolve();self.path.parent.mkdir(parents=True,exist_ok=True)
        self.fetch=fetch or transport;self.clock=clock or time.time;self.lock=threading.RLock()
        with self.connect() as db:db.executescript(SCHEMA)
    def connect(self,readonly=False):
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5,factory=ClosingConnection) if readonly else sqlite3.connect(self.path,timeout=5,factory=ClosingConnection)
        db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
        if not readonly:db.execute('PRAGMA max_page_count=65536')
        return db
    def _get(self,db,key,default=None):
        r=db.execute('SELECT value FROM state WHERE key=?',(key,)).fetchone();return json.loads(r[0]) if r else default
    def _set(self,db,key,v):db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',(key,json.dumps(v)))
    def watch(self,ns,addr):
        ns=namespace(ns);addr=address(addr);now=self.clock()
        with self.lock,self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self._expire(db,now)
            exists=db.execute('SELECT 1 FROM watches WHERE ns=? AND address=?',(ns,addr)).fetchone()
            if not exists:
                if db.execute('SELECT count(*) FROM watches').fetchone()[0]>=128 or db.execute('SELECT count(*) FROM watches WHERE ns=?',(ns,)).fetchone()[0]>=8:raise ValueError('Watch capacity reached')
                if not db.execute('SELECT 1 FROM cursors WHERE address=?',(addr,)).fetchone() and db.execute('SELECT count(*) FROM cursors').fetchone()[0]>=32:raise ValueError('Global address capacity reached')
            db.execute('INSERT OR REPLACE INTO watches VALUES (?,?,?)',(ns,addr,now+604800))
            db.execute('INSERT OR IGNORE INTO cursors(address) VALUES (?)',(addr,))
        return {'schemaVersion':1,'address':addr,'expiresAt':now+604800,'status':'watching'}
    def _expire(self,db,now):
        db.execute('DELETE FROM watches WHERE expires<=?',(now,))
        db.execute('DELETE FROM cursors WHERE address NOT IN (SELECT address FROM watches)')
        db.execute('DELETE FROM alerts WHERE ns NOT IN (SELECT ns FROM watches)')
        active={r[0] for r in db.execute('SELECT address FROM cursors')}
        for r in db.execute("SELECT key FROM state WHERE key LIKE 'addressStatus:%'").fetchall():
            if r[0].split(':',1)[1] not in active:db.execute('DELETE FROM state WHERE key=?',(r[0],))
    def unwatch(self,ns,addr):
        with self.lock,self.connect() as db:
            db.execute('DELETE FROM watches WHERE ns=? AND address=?',(namespace(ns),address(addr)))
            db.execute('DELETE FROM alerts WHERE ns=?',(namespace(ns),));self._expire(db,self.clock())
        return {'schemaVersion':1,'status':'removed'}
    def pause(self,value=True):
        with self.lock,self.connect() as db:self._set(db,'paused',bool(value))
    def _rpc(self,db,method,params):
        now=self.clock();payload={'jsonrpc':'2.0','id':1,'method':method,'params':params}
        failures=[]
        for url in RPCS:
            if self.calls>=6:raise ValueError('Pass RPC budget reached')
            if self._get(db,'cooldown:'+url,0)>now:continue
            self.calls+=1;raw=b'';error=None;status=0;headers={}
            try:
                raw,status,headers=self.fetch(url,payload)
                if not isinstance(raw,bytes):raise ValueError('Transport must preserve bytes')
                if len(raw)>MAX_BODY:raw=raw[:MAX_BODY];raise ValueError('Response exceeded 256 KiB; truncated receipt')
                if status!=200:raise ValueError('HTTP '+str(status))
                data=json.loads(raw)
                if not isinstance(data,dict):raise ValueError('Malformed RPC response')
                if data.get('error'):raise ValueError('RPC '+json.dumps(data['error']))
                if data.get('result') is None:raise ValueError('Null/missing RPC result')
            except Exception as exc:
                error=str(exc)[:500];failures.append(url+': '+error)
                delay=3600 if status in (401,403) else 120
                retry=next((v for k,v in headers.items() if k.lower()=='retry-after'),None)
                if retry:
                    try:delay=max(delay,float(retry))
                    except (ValueError,TypeError):
                        try:delay=max(delay,parsedate_to_datetime(retry).timestamp()-now)
                        except (ValueError,TypeError,OverflowError):pass
                self._set(db,'cooldown:'+url,now+delay)
            rid=db.execute('INSERT INTO receipts(observed,endpoint,request,body,sha256,error) VALUES (?,?,?,?,?,?)',(now,url,json.dumps(payload),raw,hashlib.sha256(raw).hexdigest(),error)).lastrowid
            if not error:return data['result'],rid
        raise ValueError('; '.join(failures) or 'Public providers backing off; prior errors retained')
    def _store(self,db,sig,tx,rid,addr):
        decoded=decode_transaction(tx,sig)
        if decoded['gaps']:
            error='Decoder: '+'; '.join(decoded['gaps'])
            db.execute('UPDATE receipts SET error=? WHERE id=?',(error,rid))
            raise ValueError(error)
        receipt=db.execute('SELECT * FROM receipts WHERE id=?',(rid,)).fetchone()
        db.execute('INSERT OR IGNORE INTO transactions VALUES (?,?,?)',(sig,rid,self.clock()))
        db.execute('INSERT OR IGNORE INTO touches VALUES (?,?)',(addr,sig))
        for kind in ('trades','transfers'):
            for event in decoded[kind]:
                event.update(observedAt=receipt['observed'],endpoint=receipt['endpoint'],receiptSha256=receipt['sha256'],commitment='finalized')
                db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?)',(event['id'],sig,kind,json.dumps(event)))
                for touched in ([event['wallet']] if kind=='trades' else [event['source'],event['destination']]):
                    db.execute('INSERT OR IGNORE INTO touches VALUES (?,?)',(touched,sig))
    def tick(self):
        with self.lock,self.connect() as db:
            db.execute('BEGIN IMMEDIATE');now=self.clock()
            if self._get(db,'paused',False):return {'schemaVersion':1,'status':'paused'}
            due=self._get(db,'nextTick',0)
            if due>now:return {'schemaVersion':1,'status':'backoff','nextTick':due}
            self._set(db,'nextTick',now+60);self._expire(db,now);self.calls=0;errors=[];processed=0
            rows=db.execute('SELECT * FROM cursors ORDER BY polled,address LIMIT 2').fetchall()
            for row in rows:
                c=dict(row);addr=c['address'];q=json.loads(c['queue'])
                try:
                    if not q:
                        options={'limit':5,'commitment':'finalized'}
                        if c['before_sig']:options['before']=c['before_sig']
                        page,_=self._rpc(db,'getSignaturesForAddress',[addr,options])
                        if not isinstance(page,list) or len(page)>5:raise ValueError('Invalid signature page')
                        for r in page:
                            signature(r['signature'])
                            if 'err' not in r or type(r.get('slot')) is not int:raise ValueError('Malformed signature row')
                        if not c['target'] and page:c['target']=page[0]['signature']
                        reached=any(r['signature']==c['head'] for r in page)
                        for r in page:
                            if r['signature']==c['head']:break
                            q.append(r['signature'])
                        # Initial page is intentionally only a newest-page sample.
                        more=bool(c['head'] and len(page)==5 and not reached)
                        c['before_sig']=page[-1]['signature'] if more else None
                        c['queue']=json.dumps(q)
                    while q and self.calls<6:
                        sig=q[0]
                        if db.execute('SELECT 1 FROM transactions WHERE signature=?',(sig,)).fetchone():db.execute('INSERT OR IGNORE INTO touches VALUES (?,?)',(addr,sig))
                        else:
                            tx,rid=self._rpc(db,'getTransaction',[sig,{'encoding':'jsonParsed','commitment':'finalized','maxSupportedTransactionVersion':1}])
                            self._store(db,sig,tx,rid,addr)
                        q.pop(0);processed+=1
                    if not q and not c['before_sig']:
                        c['head']=c['target'] or c['head'];c['target']=None
                except Exception as exc:errors.append({'address':addr,'message':str(exc)[:500]})
                local=[e for e in errors if e['address']==addr]
                self._set(db,'addressStatus:'+addr,{'checkedAt':now,'status':'gap' if local else ('pending' if q or c['before_sig'] else 'sampled'),'errors':local})
                db.execute('UPDATE cursors SET head=?,before_sig=?,target=?,queue=?,polled=? WHERE address=?',(c['head'],c['before_sig'],c['target'],json.dumps(q),now,addr))
                if self.calls>=6:break
            status={'schemaVersion':1,'status':'partial' if errors else 'sampled','observedAt':now,'rpcCalls':self.calls,'processed':processed,'errors':errors}
            self._set(db,'lastRun',status)
            # Retain receipt and derived observations atomically; no orphan evidence.
            db.execute('DELETE FROM transactions WHERE signature IN (SELECT signature FROM transactions ORDER BY observed DESC,signature LIMIT -1 OFFSET 400)')
            db.execute('DELETE FROM receipts WHERE id NOT IN (SELECT receipt FROM transactions) AND id NOT IN (SELECT id FROM receipts ORDER BY id DESC LIMIT 100)')
            for n in db.execute('SELECT DISTINCT ns FROM watches').fetchall():
                snap=self._snapshot(db,n[0])
                ids=[g['id'] for g in snap['groups']]
                for old in db.execute('SELECT id FROM alerts WHERE ns=?',(n[0],)).fetchall():
                    if old[0] not in ids:db.execute('DELETE FROM alerts WHERE ns=? AND id=?',(n[0],old[0]))
                for group in snap['groups']:
                    alert={'id':group['id'],'kind':'observed_group_buy','group':group,'claim':'Observed co-buys only; not ownership or coordination'}
                    db.execute('INSERT INTO alerts VALUES (?,?,?,?) ON CONFLICT(ns,id) DO UPDATE SET payload=excluded.payload',(n[0],alert['id'],json.dumps(alert),now))
            return status
    def run(self,stop_event):
        while not stop_event.is_set():
            try:self.tick()
            except (sqlite3.Error,OSError):pass
            stop_event.wait(60)
    def _snapshot(self,db,ns):
        watches=[dict(r) for r in db.execute('SELECT address,expires FROM watches WHERE ns=? AND expires>? ORDER BY address',(ns,self.clock()))]
        addrs={r['address'] for r in watches};trades=[];transfers=[]
        for r in db.execute('SELECT kind,payload FROM observations ORDER BY rowid DESC LIMIT 32000'):
            p=json.loads(r['payload'])
            if r['kind']=='trades' and p['wallet'] in addrs:trades.append(p)
            elif r['kind']=='transfers' and (p['source'] in addrs or p['destination'] in addrs):transfers.append(p)
        trades.sort(key=lambda p:(p['blockTime'] or 0,p['id']))
        trades=trades[-200:]
        transfers.sort(key=lambda p:(p['blockTime'] or 0,p['id']));transfers=transfers[-200:]
        groups=group_trades(trades);common=[]
        for t in transfers:
            if t['blockTime'] is None:continue
            peers=[x for x in transfers if x['source']==t['source'] and x['destination'] in addrs and x['blockTime'] is not None and t['blockTime']<=x['blockTime']<=t['blockTime']+300]
            if len({x['destination'] for x in peers})>=2:
                common.append({'source':t['source'],'windowSeconds':300,'startTime':t['blockTime'],'destinations':sorted({x['destination'] for x in peers}),'evidenceIds':[x['id'] for x in peers],'claim':'Shared observed transfer source; not common ownership'})
        pending=[dict(r) for r in db.execute('SELECT address,head,before_sig,queue,polled FROM cursors') if r['address'] in addrs]
        for r in pending:
            r['pendingCount']=len(json.loads(r.pop('queue')))
            r['coverage']=self._get(db,'addressStatus:'+r['address'],{'status':'not_yet_sampled'})
        last=self._get(db,'lastRun',{})
        # Global run errors can contain another user's address; redact by namespace.
        last=dict(last);last['errors']=[e for e in last.get('errors',[]) if e.get('address') in addrs]
        alerts=[]
        for row in db.execute('SELECT payload,created FROM alerts WHERE ns=? ORDER BY created DESC LIMIT 50',(ns,)):
            item=json.loads(row[0])
            if set(item['group']['participants']).issubset(addrs):
                item['createdAt']=row[1];alerts.append(item)
        return {'schemaVersion':1,'watches':watches,'trades':trades[-200:],'groups':groups[-50:],'transfers':transfers[-200:],'commonSources':common[-50:],'alerts':alerts, 'coverage':{'status':'bounded_sample','commitment':'finalized','windowSeconds':300,'lastRun':last,'paused':self._get(db,'paused',False),'cursors':pending,'limits':{'globalAddresses':32,'namespaceAddresses':8,'registrations':128,'rpcCallsPerMinute':6,'signaturesPerPage':5,'retainedTransactions':400},'limitations':['Exact-address sampling; no ATA expansion or full history','Pump exact-schema bonding curve events only; no AMM adapter','Unknown timestamps excluded from grouping','Sales are not full exits; no holdings, PnL or ownership claims','Retention-scoped groups/alerts; absent evidence is not inactivity']}}
    def snapshot(self,ns):
        with self.connect(True) as db:return self._snapshot(db,namespace(ns))
    def wallet(self,ns,addr,limit=50):
        addr=address(addr);s=self.snapshot(ns)
        return {'schemaVersion':1,'address':addr,'trades':[t for t in s['trades'] if t['wallet']==addr][-max(1,min(int(limit),50)):],'coverage':s['coverage']}
    def evidence(self,ns,sig):
        ns=namespace(ns);signature(sig)
        with self.connect(True) as db:
            allowed=db.execute('SELECT 1 FROM touches t JOIN watches w ON w.address=t.address WHERE w.ns=? AND w.expires>? AND t.signature=?',(ns,self.clock(),sig)).fetchone()
            if not allowed:return {'schemaVersion':1,'status':'not_found'}
            r=db.execute('SELECT r.* FROM receipts r JOIN transactions t ON t.receipt=r.id WHERE t.signature=?',(sig,)).fetchone()
            return {'schemaVersion':1,'signature':sig,'endpoint':r['endpoint'],'observedAt':r['observed'],'sha256':r['sha256'],'request':json.loads(r['request']),'rawResponse':bytes(r['body']).decode('utf-8'),'status':'ok'}

def group_trades(trades,window=300):
    """Anchor groups on second distinct buyer; one group/participant-set/window."""
    groups=[]
    buys=[t for t in trades if t['side']=='buy' and t['qualifiesForGroup'] and t['blockTime'] is not None]
    buys.sort(key=lambda t:(t['blockTime'],t['id']))
    for anchor in buys:
        cluster=[t for t in buys if t['mint']==anchor['mint'] and anchor['blockTime']-window<=t['blockTime']<=anchor['blockTime'] and (t['blockTime'],t['id'])<=(anchor['blockTime'],anchor['id'])]
        participants=sorted({t['wallet'] for t in cluster})
        if len(participants)<2:continue
        if any(g['mint']==anchor['mint'] and g['participants']==participants and anchor['blockTime']-g['triggerTime']<=window for g in groups):continue
        gid=hashlib.sha256((anchor['mint']+anchor['id']+'|'.join(participants)).encode()).hexdigest()[:24]
        progression=[t for t in trades if t['mint']==anchor['mint'] and t['wallet'] in participants and t['blockTime'] is not None and t['blockTime']>anchor['blockTime']]
        groups.append({'id':gid,'mint':anchor['mint'],'windowSeconds':window,'startTime':anchor['blockTime']-window,'triggerTime':anchor['blockTime'],'participants':participants,'evidenceIds':[t['id'] for t in cluster],'progression':progression[-50:],'claim':'Observed distinct event accounts buying; not coordination; later sells are not full exits'})
    return groups

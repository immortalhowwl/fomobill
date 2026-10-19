"""FOMOBILL: bounded, read-only Solana source lookup. No third-party dependencies."""
import re
import json
import math
import secrets
import hmac
import signal
import fcntl
from http.cookies import SimpleCookie
from monitoring import Monitor, signature
from holders import Holders, REPORT, MAX_BODY as HOLDER_MAX_BODY
from notifications import Notifications, Telegram, DeliveryError, x_context
from datetime import datetime, timezone

DEX = 'https://api.dexscreener.com/token-pairs/v1/solana/'
RPCS = ('https://api.mainnet-beta.solana.com', 'https://solana-rpc.publicnode.com')

def now():
    return datetime.now(timezone.utc).isoformat()

class ProviderError(Exception):
    pass

def text(value, limit=100):
    return value[:limit] if isinstance(value, str) else ''

def number(value):
    try:
        n = float(value)
        return n if not isinstance(value, bool) and math.isfinite(n) and n >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None

class Sources:
    def __init__(self, fetch=None):
        self.fetch = fetch or fetch_json

    def market(self, address):
        validate_address(address)
        url = DEX + address
        result = {'kind':'token', 'chain':'solana', 'address':address, 'observedAt':now(),
                  'source':url, 'provider':'DexScreener', 'pairs':[], 'errors':[],
                  'coverage':'Up to 12 Solana pairs where this address is the base token, ordered by reported USD liquidity. Provider snapshot, not all markets; no wallet evidence.'}
        try:
            raw = self.fetch(url)
            if not isinstance(raw, list):
                raise ProviderError('Unexpected DexScreener response shape')
            pairs = {}
            for p in raw[:200]:
                if not isinstance(p, dict) or p.get('chainId') != 'solana':
                    continue
                base, quote = p.get('baseToken'), p.get('quoteToken')
                if not isinstance(base, dict) or base.get('address') != address or not isinstance(quote, dict):
                    continue
                try: pair = validate_address(p.get('pairAddress'))
                except ValueError: continue
                price = p.get('priceUsd')
                liquidity = p.get('liquidity') or {}
                volume = p.get('volume') or {}
                pairs[pair] = {'pairAddress':pair, 'tokenAddress':address, 'name':text(base.get('name')), 'symbol':text(base.get('symbol'),30),
                              'quoteSymbol':text(quote.get('symbol'),30), 'dex':text(p.get('dexId'),40),
                              'priceUsd':str(price)[:60] if number(price) is not None else None,
                              'liquidityUsd':number(liquidity.get('usd')) if isinstance(liquidity,dict) else None,
                              'volume24hUsd':number(volume.get('h24')) if isinstance(volume,dict) else None,
                              'url':'https://dexscreener.com/solana/'+pair,
                              'context':x_context(metadata=p.get('info'))}
            result['pairs'] = sorted(pairs.values(), key=lambda p:p['liquidityUsd'] if p['liquidityUsd'] is not None else -1, reverse=True)[:12]
            result['status'] = 'ok' if result['pairs'] else 'empty'
        except ProviderError as e:
            result['status'] = 'unavailable'
            result['errors'].append({'source':url, 'message':str(e)[:200]})
        return result

    def wallet(self, address):
        validate_address(address)
        result = {'kind':'wallet','chain':'solana','address':address,'observedAt':now(),
                  'provider':'Solana public RPC','source':None,'activity':[],'errors':[],
                  'limit':20,'commitment':'finalized','cursor':None,
                  'coverage':'One newest page, at most 20 finalized transaction signatures referencing the exact account. Not full wallet history: associated token accounts are not expanded; no buy/sell, ownership, holdings or PnL classification.'}
        body = {'jsonrpc':'2.0','id':1,'method':'getSignaturesForAddress',
                'params':[address,{'limit':20,'commitment':'finalized'}]}
        for url in RPCS:
            try:
                raw = self.fetch(url, body)
                if not isinstance(raw,dict): raise ProviderError('Unexpected JSON-RPC response')
                if raw.get('error'):
                    error = raw['error']
                    raise ProviderError('RPC '+str(error.get('code'))+': '+text(error.get('message'),150) if isinstance(error,dict) else 'RPC error')
                rows = raw.get('result')
                if not isinstance(rows,list): raise ProviderError('Missing JSON-RPC signature result')
                activity, seen = [], set()
                for row in rows[:20]:
                    if not isinstance(row,dict): raise ProviderError('Malformed signature row')
                    signature = row.get('signature')
                    if not isinstance(signature,str) or not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{64,88}',signature):
                        raise ProviderError('Malformed transaction signature')
                    if signature in seen: continue
                    seen.add(signature)
                    if not isinstance(row.get('slot'),int) or isinstance(row.get('slot'),bool) or row['slot'] < 0:
                        raise ProviderError('Malformed slot')
                    block = row.get('blockTime')
                    if block is not None and (not isinstance(block,int) or isinstance(block,bool) or block < 0):
                        raise ProviderError('Malformed block timestamp')
                    if 'err' not in row: raise ProviderError('Missing transaction success status')
                    activity.append({'signature':signature,'slot':row['slot'],'blockTime':block,
                                     'status':'success' if row['err'] is None else 'failed',
                                     'confirmation':text(row.get('confirmationStatus'),30) or 'unavailable',
                                     'error':row['err'],'url':'https://solscan.io/tx/'+signature})
                result.update(status='ok' if activity else 'empty', source=url, activity=activity,
                              cursor=activity[-1]['signature'] if activity else None,
                              pageFull=len(rows)>=20)
                return result
            except ProviderError as e:
                result['errors'].append({'source':url,'message':str(e)[:200]})
        result['status'] = 'unavailable'
        return result

BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'

def validate_address(value):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,44}', value):
        raise ValueError('Enter an exact Solana base58 address (32 decoded bytes); EVM is not supported.')
    number = 0
    for char in value:
        number = number * 58 + BASE58.index(char)
    size = (number.bit_length() + 7) // 8 + len(value) - len(value.lstrip('1'))
    if size != 32:
        raise ValueError('Solana address must decode to exactly 32 bytes.')
    return value

# HTTP transport, storage and resource bounds. Only operator-fixed upstreams.
import os
import time
import socket
import sqlite3
import threading
import hashlib
from collections import OrderedDict, deque
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'
MAX_RESPONSE = 1024 * 1024

class Limiter:
    def __init__(self, count, window=60, max_keys=1024):
        self.count, self.window, self.max_keys = count, window, max_keys
        self.rows = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, key):
        with self.lock:
            t = time.monotonic()
            q = self.rows.pop(key, deque())
            while q and q[0] <= t - self.window: q.popleft()
            allowed = len(q) < self.count
            if allowed: q.append(t)
            self.rows[key] = q
            while len(self.rows) > self.max_keys: self.rows.popitem(last=False)
            return allowed

UPSTREAM_LIMIT = Limiter(30, max_keys=1)

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError('Upstream redirect rejected')

def fetch_json(url, body=None):
    if not (url in RPCS or (url.startswith(DEX) and validate_address(url[len(DEX):]))):
        raise ProviderError('Upstream URL is not allowlisted')
    if not UPSTREAM_LIMIT.allow('global'):
        raise ProviderError('Local upstream budget reached (30 requests/minute); retry later')
    req = Request(url, data=json.dumps(body).encode() if body is not None else None,
                  headers={'User-Agent':'FOMOBILL/0.3 read-only', 'Accept':'application/json', 'Content-Type':'application/json'})
    try:
        with build_opener(NoRedirect()).open(req, timeout=7) as response:
            raw = response.read(MAX_RESPONSE+1)
            if len(raw)>MAX_RESPONSE: raise ProviderError('Provider response exceeded 1 MiB')
        data = json.loads(raw)
        receipt_dir = os.environ.get('RECEIPTS_DIR')
        if receipt_dir:
            # Opt-in verification receipts; never in project or public files.
            directory = Path(receipt_dir).resolve()
            if directory == ROOT or ROOT in directory.parents:
                raise ProviderError('Receipt directory must be outside project')
            directory.mkdir(parents=True,exist_ok=True)
            digest = hashlib.sha256(raw).hexdigest()
            receipt = {'observedAt':now(), 'url':url,'request':body,'sha256':digest,'response':data,'rawResponse':raw.decode('utf-8')}
            filename = directory / ('receipt-'+str(time.time_ns())+'.json')
            filename.write_text(json.dumps(receipt,ensure_ascii=False))
            for old in sorted(directory.glob('receipt-*.json'))[:-100]: old.unlink()
        return data
    except HTTPError as e:
        raise ProviderError('HTTP '+str(e.code)+'; retry later'+(' (Retry-After: '+text(e.headers.get('Retry-After'),50)+')' if e.headers.get('Retry-After') else '')) from e
    except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as e:
        raise ProviderError('Provider connection failed or timed out') from e
    except (ValueError, UnicodeError) as e:
        raise ProviderError('Provider returned invalid JSON') from e

class PublicHolders:
    """Per-lookup deadline; public lookup quota, never the monitor's RPC budget."""
    def client(self):
        deadline=time.monotonic()+20
        def transport(url,body):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ValueError('Public lookup deadline reached; partial coverage only')
            if url not in RPCS:
                if not url.startswith(REPORT) or not url.endswith('/report') or body is not None:
                    raise ValueError('Upstream URL is not allowlisted')
                validate_address(url[len(REPORT):-len('/report')])
            if not UPSTREAM_LIMIT.allow('global'):raise ValueError('Local upstream budget reached (30 requests/minute); retry later')
            req=Request(url,data=None if body is None else json.dumps(body).encode(),
                        headers={'User-Agent':'FOMOBILL/0.3 read-only','Accept':'application/json','Content-Type':'application/json'})
            try:
                with build_opener(NoRedirect()).open(req,timeout=min(3,remaining)) as response:
                    chunks=[];size=0
                    while True:
                        if time.monotonic()>=deadline:raise ValueError('Public lookup deadline reached; partial coverage only')
                        chunk=response.read1(min(65536,HOLDER_MAX_BODY+1-size))
                        if not chunk:break
                        chunks.append(chunk);size+=len(chunk)
                        if size>HOLDER_MAX_BODY:raise ValueError('Provider response exceeded 4 MiB')
                    raw=b''.join(chunks)
                    status=response.status;headers=dict(response.headers)
            except HTTPError as exc:
                return b'',exc.code,dict(exc.headers)
            except ProviderError as exc:
                raise ValueError(str(exc)) from exc
            receipt_dir=os.environ.get('RECEIPTS_DIR')
            if receipt_dir:
                directory=Path(receipt_dir).resolve()
                if directory==ROOT or ROOT in directory.parents:raise ValueError('Receipt directory must be outside project')
                directory.mkdir(parents=True,exist_ok=True)
                receipt={'observedAt':now(),'url':url,'request':body,'status':status,'sha256':hashlib.sha256(raw).hexdigest(),'rawResponse':raw.decode('utf-8')}
                (directory/('holders-'+str(time.time_ns())+'.json')).write_text(json.dumps(receipt))
                for old in sorted(directory.glob('holders-*.json'))[:-100]:old.unlink()
            return raw,status,headers
        return Holders(transport)
    def top_holders(self,mint):return self.client().top_holders(mint)
    def wallet_history_page(self,mint,wallet,limit=5,cursor=None):return self.client().wallet_history_page(mint,wallet,limit=limit,cursor=cursor)

class Cache:
    def __init__(self, directory):
        directory = Path(directory).resolve()
        if directory == ROOT or ROOT in directory.parents:
            raise ValueError('DATA_DIR must be outside project/static source tree')
        directory.mkdir(parents=True,exist_ok=True)
        self.path = directory/'cache.sqlite3'
        self.lock = threading.Lock()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, saved REAL NOT NULL, expires REAL NOT NULL, value TEXT NOT NULL)')
    def connect(self): return sqlite3.connect(self.path, timeout=5)
    def get(self,key):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT saved,expires,value FROM cache WHERE key=?',(key,)).fetchone()
            if not row or row[1] <= time.time(): return None
            value=json.loads(row[2]);value['cache']={'hit':True,'ageSeconds':round(time.time()-row[0],1),'ttlSeconds':int(row[1]-row[0])}
            return value
    def put(self,key,value):
        ttl=15 if value['status']=='unavailable' else 60
        saved=time.time()
        with self.lock, self.connect() as db:
            db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?,?)',(key,saved,saved+ttl,json.dumps(value)))
            db.execute('DELETE FROM cache WHERE key IN (SELECT key FROM cache ORDER BY saved DESC LIMIT -1 OFFSET 256)')
        value['cache']={'hit':False,'ageSeconds':0,'ttlSeconds':ttl}
        return value

ROUTES = {'/historical-case.json':('historical-case.json','application/json; charset=utf-8'), '/':('index.html','text/html; charset=utf-8'), '/app.js':('app.js','text/javascript; charset=utf-8'),
          '/style.css':('style.css','text/css; charset=utf-8'), '/bill-renderer.js':('bill-renderer.js','text/javascript; charset=utf-8'),
          '/crawlers.js':('crawlers.js','text/javascript; charset=utf-8'), '/bill-fallback.png':('bill-fallback.png','image/png')}

# Exact allowlist: never expose package instructions or arbitrary source paths.
ROUTES['/legacy'] = ROUTES['/legacy/'] = ROUTES['/']
ROUTES['/workspace/'] = ('workspace/index.html', 'text/html; charset=utf-8')
for asset in ['workspace.css', 'inferno-theme.css', 'runtime-config.js', 'data-adapter.js', 'market-chart.js', 'workspace-core.js', 'workspace-ui.js']:
    ROUTES['/workspace/' + asset] = ('workspace/' + asset, 'text/css; charset=utf-8' if asset.endswith('.css') else 'text/javascript; charset=utf-8')
ROUTES['/'] = ('fomobill-home/index.html', 'text/html; charset=utf-8')
HOME_ASSETS = {
    'fomobill-home.css': 'text/css; charset=utf-8',
    'fomobill-home.js': 'text/javascript; charset=utf-8',
    'assets/favicon.svg': 'image/svg+xml',
    'assets/inferno-poster.webp': 'image/webp',
    'assets/inferno-loop.mp4': 'video/mp4',
    'assets/NimbusSans-Regular.otf': 'font/otf',
    'assets/NimbusSans-Bold.otf': 'font/otf',
    'assets/DejaVuSansMono.ttf': 'font/ttf',
    'assets/FONT-LICENSES.txt': 'text/plain; charset=utf-8',
    'assets/Archivo-Variable.woff2': 'font/woff2',
    'assets/OFL-Archivo.txt': 'text/plain; charset=utf-8',
    'assets/fomo-mark.svg': 'image/svg+xml',
    'assets/FOMO-MARK-SOURCE.txt': 'text/plain; charset=utf-8',
}
for asset, mime in HOME_ASSETS.items():
    ROUTES['/fomobill-home/' + asset] = ('fomobill-home/' + asset, mime)
    if asset.startswith('assets/'):
        ROUTES['/' + asset] = ('fomobill-home/' + asset, mime)

class Server(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=16
    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(16)
        self.network=threading.BoundedSemaphore(2)
        self.limiter=Limiter(60)
        super().__init__(*args,**kwargs)
    def server_close(self):
        if hasattr(self,'runtime'): self.runtime.close()
        super().server_close()
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            try: request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            finally: self.shutdown_request(request)
            return
        try: super().process_request(request,address)
        except Exception:
            self.slots.release();raise
    def process_request_thread(self,request,address):
        try: super().process_request_thread(request,address)
        finally:self.slots.release()

class Runtime:
    """One explicitly started worker. Constructors and HTTP reads never poll."""
    def __init__(self, directory, monitor=None):
        directory=Path(directory);os.chmod(directory,0o700)
        key=directory/'owner.key'
        try:
            fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError:pass
        else:
            with os.fdopen(fd,'wb') as out:out.write(secrets.token_bytes(32))
        self.key=key.read_bytes()
        if len(self.key)!=32:raise ValueError('Invalid owner key')
        self.monitor=monitor or Monitor(directory/'monitor.sqlite3')
        os.chmod(self.monitor.path,0o600)
        self.notifications=Notifications(directory/'notifications.sqlite3')
        self.stop_event=threading.Event();self.worker=None;self.lock=threading.RLock()
        self.bot=None;self.bot_username=None;self.telegram_status='unavailable: dedicated bot not configured'
        self.telegram_due=0;self.worker_error=False
        self.sales_boot=time.time()
    def _mac(self,value):return hmac.new(self.key,('cookie:'+value).encode(),hashlib.sha256).hexdigest()
    def issue(self):
        token=secrets.token_urlsafe(32)
        return token+'.'+self._mac(token)
    def authenticate(self,cookie):
        try:
            jar=SimpleCookie();jar.load(cookie or '');value=jar['fomo_owner'].value
            token,mac=value.split('.')
            if not re.fullmatch(r'[A-Za-z0-9_-]{43}',token) or not hmac.compare_digest(mac,self._mac(token)):return None
            return hmac.new(self.key,('namespace:'+token).encode(),hashlib.sha256).hexdigest()
        except (KeyError,ValueError):return None
    def start(self,configure_telegram=False):
        with self.lock:
            if self.worker is not None:return
            self.worker_lock=open(self.monitor.path.parent/'worker.lock','a')
            try:fcntl.flock(self.worker_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                self.worker_lock.close();raise RuntimeError('One worker per data directory is required') from None
            if configure_telegram:
                # Only this dedicated secret is ever read. Operator identity must also be pinned.
                try:self.bot=Telegram.from_env()
                except ValueError:self.bot=None;self.telegram_status='unavailable: invalid dedicated bot configuration'
                self.expected_bot=os.environ.get('FOMOBILL_TELEGRAM_USERNAME','')
                if self.bot and not re.fullmatch(r'[A-Za-z0-9_]{5,32}',self.expected_bot):
                    self.bot=None;self.telegram_status='unavailable: dedicated bot identity not pinned'
                elif self.bot:self.telegram_status='unavailable: checking dedicated bot identity'
            self.worker=threading.Thread(target=self.run,name='fomobill-monitor',daemon=False)
            self.worker.start()
    def run(self):
        monitor_due=0
        while not self.stop_event.is_set():
            self.telegram_tick()
            if not self.stop_event.is_set() and time.monotonic()>=monitor_due:
                try:self.monitor.tick();self.worker_error=False
                except Exception:self.worker_error=True
                monitor_due=time.monotonic()+60
            self.stop_event.wait(2)
    def close(self):
        self.stop_event.set()
        if self.worker and self.worker is not threading.current_thread():self.worker.join()
        if getattr(self,'worker_lock',None) and not self.worker_lock.closed:self.worker_lock.close()
    def telegram_tick(self):
        if not self.bot or time.time()<self.telegram_due:return
        try:
            if not self.bot_username:
                me=self.bot.call('getMe',{})
                if not isinstance(me,dict) or me.get('is_bot') is not True or me.get('username')!=self.expected_bot:
                    self.bot=None;self.telegram_status='unavailable: bot identity mismatch';return
                hook=self.bot.call('getWebhookInfo',{})
                if not isinstance(hook,dict) or hook.get('url'):
                    self.bot=None;self.telegram_status='unavailable: existing webhook';return
                self.bot_username=me['username']
            updates=self.bot.updates(self.notifications.offset())
            with self.lock:
                for update in sorted((u for u in updates if isinstance(u,dict) and type(u.get('update_id')) is int),key=lambda u:u['update_id']):
                    outcome=self.notifications.handle_update(update)
                    if outcome not in ('ignored','duplicate',None):self.telegram_reply_context(update,outcome)
                self.telegram_status='ready'
                if len(updates)>=100:return  # drain stop commands before sending
                with self.notifications.connect() as db:
                    owners=[r[0] for r in db.execute('SELECT owner FROM recipients WHERE active=1 LIMIT 1000')]
                for owner in owners:self.enqueue_groups(owner)
                self.notifications.drain_replies(self.bot.send, self.bot.send_welcome)
                self.notifications.drain(self.bot.send)
        except DeliveryError as e:
            self.telegram_due=time.time()+max(120,e.retry_after);self.telegram_status='unavailable: provider backoff'
        except Exception:
            self.telegram_due=time.time()+120;self.telegram_status='unavailable: provider failure'
    def telegram_reply_context(self,update,outcome):
        # Consent/offset have already committed. A crash or monitor error leaves alerts OFF.
        with self.notifications.connect() as db:
            row=db.execute("SELECT c.chat,c.text,r.owner FROM command_replies c LEFT JOIN recipients r ON r.chat=c.chat WHERE c.update_id=? AND c.state='pending'",(update['update_id'],)).fetchone()
        if not row or not row['owner']:return
        owner=row['owner'];text=row['text']
        if outcome=='watch_pending':
            command,addr=update['message']['text'].split(' ',1)
            try:
                if command=='/watch':self.monitor.watch(owner,addr)
                else:self.monitor.unwatch(owner,addr)
                text=('△ FOMOBILL · WALLET SELECTED\n\nSelected for 7 days:\n' if command=='/watch' else '△ FOMOBILL · WALLET REMOVED\n\n')+addr+'\n\nAlerts OFF.\n/subscribe — enable future qualifying alerts after selecting your wallets'
            except ValueError:
                text='△ FOMOBILL · WALLET NOT UPDATED\n\nInvalid Solana address or watch capacity reached. Alerts remain OFF.\n\nCheck the exact address and your wallet list below.\n/watch ADDRESS — add or renew\n/unwatch ADDRESS — remove'
            except Exception:
                text='△ FOMOBILL · WALLET REQUEST INTERRUPTED\n\nThe request could not be completed. Alerts remain OFF.\n\nRetry the same wallet command, then /subscribe to resume.'
        try:text+='\n\n'+self.telegram_watch_status(owner)
        except Exception:text+='\n\nWALLET STATUS UNAVAILABLE\nTry /status again shortly. No monitoring result is implied.'
        with self.notifications.connect() as db:
            db.execute("UPDATE command_replies SET text=? WHERE update_id=? AND state='pending'",(text,update['update_id']))

    def telegram_watch_status(self,owner):
        snap=self.monitor.snapshot(owner);watches=snap['watches'];count=len(watches)
        preference=self.notifications.status(owner)
        text='ALERT PREFERENCES\nParticipant sale alerts: '+('ON' if preference['sales'] else 'OFF')
        if not preference['active']:text+=' (paused with subscription)'
        text+='\n\nWALLETS · '+str(count)+' selected wallets'
        if not watches:return text+'\nNo active monitor selection. No trade alerts can be generated.\n\nNEXT STEP\n/watch ADDRESS — select at least two wallets, then /subscribe. No website required.'
        text+='\n'+'\n'.join(str(i)+'. '+w['address'] for i,w in enumerate(watches,1))
        if count==1:text+='\n\nNEXT STEP\nAdd at least one more wallet with /watch ADDRESS, then /subscribe.'
        elif not preference['active']:text+='\n\nNEXT STEP\n/subscribe — resume future qualifying alerts'
        text+='\n\nSELECTION\nExpires after 7 days. /watch ADDRESS renews and pauses alerts until /subscribe.\nCo-buy: at least two selected wallets buy the same mint within 5 minutes.'
        coverage=snap['coverage'];run=coverage.get('lastRun',{})
        state='paused' if coverage.get('paused') else str(run.get('status','waiting for first check'))
        text+='\n\nCOLLECTION\nCollector: '+state+'.\nBounded Pump sample; gaps or empty results are not proof of inactivity.'
        return text

    def enqueue_groups(self,owner):
        eligible_sales=set()
        try:self._enqueue_groups(owner,eligible_sales)
        finally:
            # Recheck retained evidence every tick, including retries. Never replay
            # a pending sale after restart/source loss or receipt eviction.
            with self.notifications.lock,self.notifications.connect() as db:
                for row in db.execute("SELECT event FROM outbox WHERE owner=? AND event LIKE 'pump:sale:%' AND state='pending'",(owner,)).fetchall():
                    if row['event'] not in eligible_sales:
                        db.execute("UPDATE outbox SET state='suppressed' WHERE owner=? AND event=?",(owner,row['event']))

    def _enqueue_groups(self,owner,eligible_sales):
        snap=self.monitor.snapshot(owner);coverage=snap['coverage'];run=coverage['lastRun'];now_time=time.time()
        if coverage['paused'] or run.get('status')!='sampled' or not 0<=now_time-run.get('observedAt',0)<=180:return
        if any(c.get('coverage',{}).get('status')!='sampled' or now_time-c.get('polled',0)>180 for c in coverage['cursors']):return
        watches={w['address'] for w in snap['watches']};trades={t['id']:t for t in snap['trades']}
        for group in snap['groups']:
            evidence=[trades.get(i) for i in group['evidenceIds']]
            if not evidence or any(not t or t['side']!='buy' or t['mint']!=group['mint'] or t.get('commitment')!='finalized' or t.get('qualifiesForGroup') is not True or t['wallet'] not in watches or t['wallet'] not in t.get('signers',[]) or t.get('blockTime') is None for t in evidence):continue
            if {t['wallet'] for t in evidence}!=set(group['participants']):continue
            trigger=[t for t in evidence if t['blockTime']==group['triggerTime']]
            if not trigger:continue
            if any(self.monitor.evidence(owner,t['signature']).get('status')!='ok' for t in evidence):continue
            links=['https://solscan.io/tx/'+signature(t['signature']) for t in evidence]
            message=('△ FOMOBILL · CO-BUY OBSERVED\n\nTOKEN\n'+group['mint']
                     +'\n\nPARTICIPANTS · '+str(len(group['participants']))+' selected wallets\n'+'\n'.join(group['participants'])
                     +'\n\nBUY RECEIPTS\n'+'\n'.join(dict.fromkeys(links))
                     +'\n\nBounded sample, not proof of coordination.\n/stop — pause alerts')
            try:self.notifications.enqueue(owner,'pump:group:'+group['id'],message,occurred_at=group['triggerTime'],captured_at=min(t['observedAt'] for t in trigger))
            except ValueError:continue
            preference=self.notifications.status(owner)
            cutoff=max(self.sales_boot,preference['since'],preference['salesSince'])
            if not preference['active'] or not preference['sales'] or group['triggerTime']<=cutoff:continue
            with self.notifications.connect() as db:
                admitted=db.execute("SELECT 1 FROM outbox WHERE owner=? AND event=? AND state IN ('pending','sent')",(owner,'pump:group:'+group['id'])).fetchone()
            if not admitted:continue
            for sale in trades.values():
                if (sale.get('side')!='sell' or sale.get('mint')!=group['mint'] or sale.get('wallet') not in group['participants']
                    or sale.get('wallet') not in watches or sale.get('commitment')!='finalized'
                    or sale.get('qualifiesForGroup') is not True
                    or sale.get('wallet') not in sale.get('signers',[]) or type(sale.get('blockTime')) not in (int,float)
                    or not group['triggerTime']<sale['blockTime']<=sale.get('observedAt',0)<=now_time
                    or not now_time-300<=sale['observedAt'] or sale['blockTime']<=now_time-3600):continue
                if self.monitor.evidence(owner,sale['signature']).get('status')!='ok':continue
                event='pump:sale:'+sale['id']
                sale_message=('△ FOMOBILL · PARTICIPANT SALE\n\nTOKEN\n'+group['mint']
                              +'\n\nSELLER\n'+sale['wallet']
                              +'\n\nCO-BUY CONTEXT\nGroup: '+group['id']
                              +'\nGroup time (UTC): '+datetime.fromtimestamp(group['triggerTime'],timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                              +'\n\nSALE RECEIPT\nhttps://solscan.io/tx/'+signature(sale['signature'])
                              +'\n\nCO-BUY RECEIPTS\n'+'\n'.join(dict.fromkeys(links))
                              +'\n\nBounded sample, not a full exit.\n/sales_off — disable sale alerts only')
                try:
                    self.notifications.enqueue(owner,event,sale_message,occurred_at=sale['blockTime'],captured_at=sale['observedAt'],sale_group_at=group['triggerTime'])
                    eligible_sales.add(event)
                except ValueError:continue

class Handler(BaseHTTPRequestHandler):
    server_version='FOMOBILL/0.3'
    sys_version=''
    def setup(self):
        super().setup();self.connection.settimeout(10)
    def log_message(self,fmt,*args):
        # Do not log queried wallet addresses or visitor IPs.
        pass
    def reply(self,status,data,mime='application/json; charset=utf-8'):
        raw = json.dumps(data,ensure_ascii=False,allow_nan=False).encode() if isinstance(data,(dict,list)) else data
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; media-src 'self'; font-src 'self'; connect-src 'self'; frame-src https://dexscreener.com; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()')
        self.send_header('Connection','close')
        if status==429:self.send_header('Retry-After','60')
        if getattr(self,'new_cookie',None):self.send_header('Set-Cookie',self.new_cookie)
        self.end_headers()
        if self.command!='HEAD':self.wfile.write(raw)
        self.close_connection=True
    def media(self, file, mime):
        """Bounded binary streaming, including HEAD and a single byte range."""
        target = STATIC / file
        size = target.stat().st_size
        start, end, status = 0, size - 1, 200
        requested = self.headers.get('Range') if self.command != 'HEAD' else None
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            valid = bool(match and any(match.groups()))
            if valid:
                a, b = match.groups()
                if a:
                    start = int(a); end = min(int(b), size - 1) if b else size - 1
                else:
                    length = int(b); start = max(0, size - length)
                    valid = length > 0
                valid = valid and start <= end and start < size
            if not valid:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if status == 206:self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if self.command != 'HEAD':
            with target.open('rb') as source:
                source.seek(start); remaining = end - start + 1
                while remaining:
                    block = source.read(min(65536, remaining))
                    if not block:break
                    self.wfile.write(block); remaining -= len(block)

    def do_HEAD(self):self.do_GET()
    def do_POST(self):
        try:self.mutate()
        except (BrokenPipeError,ConnectionResetError):pass
        except ValueError as e:self.reply(400,{'error':str(e)[:200]})
        except Exception:self.reply(503,{'error':'Service temporarily unavailable'})
    def do_PUT(self):self.reply(405,{'error':'Method not allowed'})
    do_DELETE=do_PUT
    do_PATCH=do_PUT
    def do_GET(self):
        try:self.route()
        except (BrokenPipeError,ConnectionResetError):pass
        except ValueError as e:self.reply(400,{'error':str(e)[:200]})
        except Exception:self.reply(503,{'error':'Service temporarily unavailable; no result generated'})
    def owner(self):
        return self.server.runtime.authenticate(self.headers.get('Cookie'))
    def budget(self,owner=None,write=False):
        if not self.server.limiter.allow('ip:'+self.client_address[0]):return False
        return self.server.limiter.allow(('write:' if write else 'owner:')+(owner or 'anonymous'))
    def origin_ok(self):
        host=self.headers.get('Host','')
        local=host.split(':')[0] in ('localhost','127.0.0.1')
        expected=('http://' if local else 'https://')+host
        return (self.headers.get('Origin')==expected and self.headers.get('X-Fomobill')=='1'
                and self.headers.get('Sec-Fetch-Site','same-origin')=='same-origin')
    def mutate(self):
        if len(self.path)>256:return self.reply(414,{'error':'Request URL too long'})
        host=self.headers.get('Host','').split(':')[0].lower()
        if host not in self.server.allowed_hosts:return self.reply(403,{'error':'Host not allowed'})
        paths=('/api/session','/api/monitor/watch','/api/monitor/unwatch','/api/telegram/pair','/api/telegram/stop')
        if self.path not in paths:return self.reply(405,{'error':'Method or path not allowed'})
        if not self.origin_ok():return self.reply(403,{'error':'Same-origin request required'})
        owner=self.owner()
        if not self.budget(owner,True):return self.reply(429,{'error':'Request limit; wait 60 seconds'})
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))!=1:return self.reply(400,{'error':'One Content-Length required'})
        length=self.headers.get('Content-Length','')
        if not re.fullmatch(r'[0-9]{1,8}',length):return self.reply(400,{'error':'Invalid body length'})
        if int(length)>4096:return self.reply(413,{'error':'Body limit is 4096 bytes'})
        if self.headers.get('Content-Type')!='application/json':return self.reply(415,{'error':'JSON required'})
        body=json.loads(self.rfile.read(int(length)))
        if not isinstance(body,dict):raise ValueError('JSON object required')
        expected={'address'} if self.path in ('/api/monitor/watch','/api/monitor/unwatch') else set()
        if set(body)!=expected:raise ValueError('Unexpected request fields')
        runtime=self.server.runtime
        if self.path=='/api/session':
            if not owner:
                self.new_cookie='fomo_owner='+runtime.issue()+'; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000'+('' if host in ('localhost','127.0.0.1') else '; Secure')
            return self.reply(200,{'status':'ready','identity':'Anonymous browser capability; clearing cookies loses access'})
        if not owner:return self.reply(401,{'error':'Initialize browser identity first'})
        with runtime.lock:
            if expected:
                addr=validate_address(body['address'])
                # Every watch change pauses delivery and cancels queued messages.
                runtime.notifications.stop(owner)
                result=(runtime.monitor.watch if self.path.endswith('/watch') else runtime.monitor.unwatch)(owner,addr)
            elif self.path=='/api/telegram/stop':
                runtime.notifications.stop(owner);result={'status':'stopped'}
            else:
                if runtime.telegram_status!='ready':return self.reply(503,{'error':'Telegram unavailable; dedicated verified bot required'})
                token=runtime.notifications.create_pairing(owner)
                result={'url':'https://t.me/'+runtime.bot_username+'?start='+token,'expiresIn':600}
        return self.reply(200,result)
    def private_read(self,path,parsed):
        owner=self.owner()
        if not owner:return self.reply(401,{'error':'Browser identity required'})
        if self.headers.get('Sec-Fetch-Site','same-origin') not in ('same-origin','none'):return self.reply(403,{'error':'Cross-origin read rejected'})
        if self.headers.get('Origin') and self.headers['Origin'] not in ('http://'+self.headers['Host'] if self.headers['Host'].split(':')[0] in ('localhost','127.0.0.1') else 'https://'+self.headers['Host'],):return self.reply(403,{'error':'Cross-origin read rejected'})
        if parsed.fragment:raise ValueError('Fragments not accepted')
        if not self.budget(owner):return self.reply(429,{'error':'Request limit; wait 60 seconds'})
        query=parse_qs(parsed.query,keep_blank_values=True,max_num_fields=2)
        field='address' if path.endswith('/wallet') else 'signature' if path.endswith('/evidence') else None
        if (field and (set(query)!={field} or len(query[field])!=1)) or (not field and parsed.query):raise ValueError('Unexpected query parameters')
        runtime=self.server.runtime
        if field=='address':result=runtime.monitor.wallet(owner,validate_address(query[field][0]))
        elif field=='signature':result=runtime.monitor.evidence(owner,signature(query[field][0]))
        elif path=='/api/telegram':result={**runtime.notifications.status(owner),'availability':runtime.telegram_status}
        else:
            result=runtime.monitor.snapshot(owner)
            result['runtime']={'running':bool(runtime.worker and runtime.worker.is_alive()),'error':runtime.worker_error}
        return self.reply(404 if result.get('status')=='not_found' else 200,result)
    def route(self):
        if len(self.path)>(12544 if self.path.startswith('/api/holder-trades?') else 256):return self.reply(414,{'error':'Request URL too long'})
        host=self.headers.get('Host','').split(':')[0].lower()
        if host not in self.server.allowed_hosts:return self.reply(403,{'error':'Host is not configured in ALLOWED_HOSTS'})
        parsed=urlsplit(self.path)
        if parsed.scheme or parsed.netloc:return self.reply(400,{'error':'Absolute URLs not accepted'})
        path=parsed.path
        if path=='/workspace':
            self.send_response(308)
            self.send_header('Location','/workspace/'+('?' + parsed.query if parsed.query else ''))
            self.send_header('Content-Length','0')
            self.end_headers()
            return
        if path in ROUTES:
            file,mime=ROUTES[path]
            if mime == 'video/mp4':return self.media(file,mime)
            return self.reply(200,(STATIC/file).read_bytes(),mime)
        if path=='/health':return self.reply(200,{'status':'ok','build':'fomobill-live-0.3','telegramBuild':'receipts-0.3.4','chain':'solana','upstreamHealth':'not probed by health route'})
        if path=='/api/coverage':
            return self.reply(200,{'chain':'solana','market':'DexScreener: max 12 base-token pairs',
                                  'wallet':'Public RPC: max 20 finalized signatures referencing the exact account',
                                  'watchlist':'Browser-local bookmarks, separate explicit server observation (8/owner, expires in 7 days)',
                                  'monitor':'Bounded finalized exact-account Pump trades, co-buys and native transfer-source observations; max 6 RPC/minute, no ATA/AMM coverage',
                                  'telegram':self.server.runtime.telegram_status,
                                  'context':'Provider-reported metadata links only; no X content read',
                                  'holders':'Largest-account owner sample, potentially stale provider-report fallback; not global owner ranking',
                                  'holderHistory':'Five signatures per explicit round-robin page across up to four verified current exact-mint token accounts and owner; Pump events and narrowly verified Raydium AMM exact-in / Orca legacy individual fills, not net route proceeds or full history',
                                  'notImplemented':['full holder history','full exits','X content','AI','EVM'],
                                  'sources':[DEX,*RPCS],'refresh':'Manual, 60-second cache; failures cached 15 seconds'})
        if path in ('/api/monitor','/api/monitor/wallet','/api/monitor/evidence','/api/telegram'):
            return self.private_read(path,parsed)
        if path not in ('/api/token','/api/wallet','/api/holders','/api/holder-trades'):return self.reply(404,{'error':'Not found'})
        if not self.budget(self.owner()):return self.reply(429,{'error':'Too many requests; wait 60 seconds'})
        if parsed.fragment:raise ValueError('Fragments not accepted')
        query=parse_qs(parsed.query,keep_blank_values=True,max_num_fields=4)
        expected={'address','mint'} if path=='/api/holder-trades' else {'address'}
        if path=='/api/holder-trades' and 'cursor' in query:expected.add('cursor')
        if set(query)!=expected or any(len(v)!=1 for v in query.values()):raise ValueError('Unexpected query parameters')
        address=validate_address(query['address'][0]);key=path+':'+address
        mint=validate_address(query['mint'][0]) if path=='/api/holder-trades' else None
        cursor=query.get('cursor',[None])[0]
        if cursor is not None and not 1<=len(cursor)<=4096:raise ValueError('Invalid history cursor length')
        if mint:key+=':'+mint
        if cursor is not None:key+=':'+hashlib.sha256(cursor.encode()).hexdigest()
        cached=self.server.cache.get(key)
        if cached:return self.reply(200,cached)
        if not self.server.network.acquire(blocking=False):return self.reply(503,{'error':'Source requests busy; retry shortly'})
        try:
            cached=self.server.cache.get(key)
            if cached:return self.reply(200,cached)
            if mint:result=self.server.holders.wallet_history_page(mint,address,limit=5,cursor=cursor)
            else:result=(self.server.holders.top_holders if path=='/api/holders' else self.server.sources.market if path=='/api/token' else self.server.sources.wallet)(address)
            self.reply(200,self.server.cache.put(key,result))
        finally:self.server.network.release()

def make_server(host='127.0.0.1',port=8080,data_dir=None,sources=None,monitor=None):
    cache=Cache(data_dir or os.environ.get('DATA_DIR',str(Path.home()/'.local/share/fomobill-live')))
    server=Server((host,port),Handler)
    server.cache=cache;server.sources=sources or Sources();server.holders=PublicHolders()
    server.runtime=Runtime(cache.path.parent,monitor=monitor)
    server.allowed_hosts={h.strip().lower() for h in os.environ.get('ALLOWED_HOSTS','localhost,127.0.0.1').split(',') if h.strip()}
    railway=os.environ.get('RAILWAY_PUBLIC_DOMAIN')
    if railway:server.allowed_hosts.add(railway.lower())
    server.allowed_hosts.add('healthcheck.railway.app')
    return server

if __name__=='__main__':
    os.umask(0o077)
    server=make_server(os.environ.get('HOST','127.0.0.1'),int(os.environ.get('PORT','8080')))
    if os.environ.get('FOMOBILL_MONITOR_ENABLED','1')=='1':server.runtime.start(configure_telegram=True)
    def terminate(*_):
        server.runtime.stop_event.set()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,terminate)
    print('FOMOBILL read-only server on '+str(server.server_address)+'; Solana only',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

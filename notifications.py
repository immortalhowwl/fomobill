"""Opt-in Telegram adapter. Inert on import; no credentials outside dedicated env.

Owner arguments must come from server-authenticated anonymous sessions, never
request JSON. See docs/notifications.md for activation and concurrency contracts.
"""
import hashlib
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import math
from datetime import datetime, timezone


class DeliveryError(Exception):
    """Safe error code only; no URL, provider description or credential text."""
    def __init__(self, code='temporary', retry_after=0, permanent=False):
        super().__init__(code if code in ('temporary', 'rate_limited', 'rejected', 'invalid_response', 'disabled') else 'temporary')
        self.retry_after = retry_after if type(retry_after) in (int, float) and math.isfinite(retry_after) and retry_after >= 0 else 0
        self.permanent = permanent


def timestamp(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None: raise ValueError('Timezone required')
            value = parsed.timestamp()
        except (ValueError, OverflowError):
            raise ValueError('Invalid timestamp') from None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid timestamp')
    return value


class Notifications:
    def __init__(self, path, clock=time.time):
        self.path = Path(path).resolve()
        root = Path(__file__).resolve().parent
        if self.path == root or root in self.path.parents:
            raise ValueError('Notification database must be outside source/static tree')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS recipients (
                    owner TEXT PRIMARY KEY, chat INTEGER UNIQUE,
                    active INTEGER NOT NULL DEFAULT 0, since REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pairing (
                    digest TEXT PRIMARY KEY, owner TEXT UNIQUE NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS command_replies (
                    update_id INTEGER PRIMARY KEY, chat INTEGER NOT NULL, command TEXT NOT NULL,
                    text TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                    next_try REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'pending', message_id INTEGER);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value REAL NOT NULL);
                INSERT OR IGNORE INTO meta VALUES ('offset', 0);
                INSERT OR IGNORE INTO meta VALUES ('send_after', 0);
                CREATE TABLE IF NOT EXISTS outbox (
                    owner TEXT NOT NULL, event TEXT NOT NULL, text TEXT NOT NULL,
                    created REAL NOT NULL, expires REAL NOT NULL, next_try REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'pending',
                    PRIMARY KEY(owner,event));
            ''')
            # Additive migration: preserve recipients, cutoffs and all dedup rows.
            reply_columns = {r['name'] for r in db.execute('PRAGMA table_info(command_replies)')}
            if 'photo_state' not in reply_columns:
                db.execute("ALTER TABLE command_replies ADD COLUMN photo_state TEXT NOT NULL DEFAULT 'none'")
            if 'photo_message_id' not in reply_columns:
                db.execute('ALTER TABLE command_replies ADD COLUMN photo_message_id INTEGER')
            if 'message_id' not in {r['name'] for r in db.execute('PRAGMA table_info(outbox)')}:
                db.execute('ALTER TABLE outbox ADD COLUMN message_id INTEGER')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(recipients)')}
            if 'sales' not in columns:
                db.execute('ALTER TABLE recipients ADD COLUMN sales INTEGER NOT NULL DEFAULT 0')
            if 'sales_since' not in columns:
                db.execute('ALTER TABLE recipients ADD COLUMN sales_since REAL NOT NULL DEFAULT 0')
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _owner(owner):
        if not isinstance(owner, str) or not 1 <= len(owner) <= 128:
            raise ValueError('Invalid authenticated owner identifier')
        return owner

    def create_pairing(self, owner):
        self._owner(owner)
        token = secrets.token_urlsafe(32)
        with self.lock, self.connect() as db:
            db.execute('DELETE FROM pairing WHERE expires<=?', (self.clock(),))
            if db.execute('SELECT count(*) FROM pairing').fetchone()[0] >= 1000:
                raise ValueError('Pairing capacity reached; retry later')
            db.execute('INSERT OR REPLACE INTO pairing VALUES (?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), owner, self.clock() + 600))
        return token

    def status(self, owner):
        self._owner(owner)
        with self.lock, self.connect() as db:
            row = db.execute('SELECT active,since,sales,sales_since FROM recipients WHERE owner=?', (owner,)).fetchone()
        return {'paired': row is not None, 'active': bool(row['active']) if row else False,
                'since': row['since'] if row else 0, 'sales': bool(row['sales']) if row else False,
                'salesSince': row['sales_since'] if row else 0}

    def offset(self):
        with self.connect() as db:
            return int(db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()[0])

    def handle_update(self, update):
        """Commit consent, bounded acknowledgement and offset together; never send here."""
        if not isinstance(update, dict) or type(update.get('update_id')) is not int or update['update_id'] < 0:
            return 'ignored'
        with self.lock, self.connect() as db:
            if update['update_id'] < int(db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()[0]):
                return 'duplicate'
            db.execute("UPDATE meta SET value=? WHERE key='offset'", (update['update_id'] + 1,))
            msg = update.get('message')
            if not isinstance(msg, dict): return 'ignored'
            chat, sender = msg.get('chat'), msg.get('from')
            if not isinstance(chat, dict) or not isinstance(sender, dict): return 'ignored'
            cid = chat.get('id')
            if chat.get('type') != 'private' or type(cid) is not int or not 0 < cid < 2**52 or type(sender.get('id')) is not int or sender['id'] != cid:
                return 'ignored'
            text = msg.get('text')
            if not isinstance(text, str) or len(text) > 128: return 'ignored'
            outcome = self._command(db, cid, text)
            row = db.execute('SELECT * FROM recipients WHERE chat=?', (cid,)).fetchone()
            reply = self.command_reply(outcome, bool(row and row['active']))
            now = self.clock()
            # One outstanding reply per chat; latest command supersedes stale status.
            db.execute("UPDATE command_replies SET state='superseded' WHERE chat=? AND state='pending'", (cid,))
            db.execute('DELETE FROM command_replies WHERE created<?', (now - 7 * 86400,))
            db.execute('DELETE FROM command_replies WHERE update_id IN (SELECT update_id FROM command_replies ORDER BY created DESC,update_id DESC LIMIT -1 OFFSET 999)')
            db.execute('INSERT INTO command_replies(update_id,chat,command,text,created,expires,next_try) VALUES (?,?,?,?,?,?,?)',
                       (update['update_id'], cid, text.split(' ', 1)[0][:32], reply, now, now + 600, now))
            if text == '/start' or text.startswith('/start '):
                db.execute("UPDATE command_replies SET photo_state='pending' WHERE update_id=?", (update['update_id'],))
            return outcome

    def _command(self, db, cid, text):
        if text.startswith('/start '):
            token = text[7:]
            if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token): return 'invalid_pairing'
            digest = hashlib.sha256(token.encode()).hexdigest()
            row = db.execute('SELECT owner FROM pairing WHERE digest=? AND expires>?', (digest, self.clock())).fetchone()
            if not row: return 'invalid_pairing'
            bound = db.execute('SELECT owner,chat FROM recipients WHERE owner=? OR chat=?', (row['owner'], cid)).fetchall()
            if bound and any(r['owner'] != row['owner'] or r['chat'] != cid for r in bound): return 'pairing_conflict'
            if not bound and db.execute('SELECT count(*) FROM recipients').fetchone()[0] >= 1000: return 'capacity'
            db.execute('INSERT OR IGNORE INTO recipients(owner,chat) VALUES (?,?)', (row['owner'], cid))
            db.execute('DELETE FROM pairing WHERE digest=?', (digest,))
            return 'paired'
        row = db.execute('SELECT * FROM recipients WHERE chat=?', (cid,)).fetchone()
        if not row:
            if db.execute('SELECT count(*) FROM recipients').fetchone()[0] >= 1000: return 'capacity'
            # Disjoint from HMAC browser namespaces. Only authenticated private updates reach here.
            db.execute('INSERT INTO recipients(owner,chat) VALUES (?,?)', ('telegram_' + str(cid).zfill(32), cid))
            row = db.execute('SELECT * FROM recipients WHERE chat=?', (cid,)).fetchone()
        if text in ('/start', '/help'): return 'help'
        if text in ('/status', '/watches'): return 'status'
        if text.startswith(('/watch ', '/unwatch ')):
            db.execute('UPDATE recipients SET active=0 WHERE chat=?', (cid,))
            db.execute("UPDATE outbox SET state='cancelled' WHERE owner=? AND state='pending'", (row['owner'],))
            return 'watch_pending'
        if text == '/subscribe':
            db.execute('UPDATE recipients SET active=1,since=? WHERE chat=? AND active=0', (self.clock(), cid))
            return 'subscribed'
        if text == '/sales_on':
            db.execute('UPDATE recipients SET sales=1,sales_since=? WHERE chat=? AND sales=0', (self.clock(), cid))
            return 'sales_on'
        if text == '/sales_off':
            db.execute('UPDATE recipients SET sales=0 WHERE chat=?', (cid,))
            db.execute("UPDATE outbox SET state='cancelled' WHERE owner=? AND event LIKE 'pump:sale:%' AND state='pending'", (row['owner'],))
            return 'sales_off'
        if text == '/stop':
            db.execute('UPDATE recipients SET active=0 WHERE chat=?', (cid,))
            db.execute("UPDATE outbox SET state='cancelled' WHERE owner=? AND state='pending'", (row['owner'],))
            return 'stopped'
        return 'unknown'

    @staticmethod
    def command_reply(outcome, active):
        status = 'Alert subscription: ' + ('ON' if active else 'OFF') + '.'
        messages = {
            'help': ('WELCOME\n\nObserve Solana co-buys from wallets you select. No website required.'
                     '\n\nGET STARTED\n1. /watch ADDRESS — add a wallet; repeat for at least two wallets.\n2. /subscribe — enable future co-buy alerts.'
                     '\n\nOPTIONAL SALE ALERTS\n/sales_on — enable subsequent participant sales\n/sales_off — disable sales only\nRequires an active subscription and a NEW co-buy group after opt-in/resume.'
                     '\n\nCONTROLS\n/status or /watches — preferences and wallets\n/unwatch ADDRESS — remove a wallet\n/stop — pause both alert categories\n/help — this guide'
                     '\n\nCOVERAGE\n• Co-buy: at least two selected wallets buy the same mint within 5 minutes.\n• Wallet selection lasts 7 days. /watch renews it.\n• Changing wallets pauses alerts; use /subscribe afterward.\n• Bounded Pump sample, not complete history or proof of coordination. A sale is not a full exit.\n• No old events replayed; restart/resume skips old sale chains. No trading.'),
            'sales_on': 'SALE ALERTS ENABLED\n\nPreference saved. Requires /subscribe and a NEW co-buy group after opt-in/resume.\n\nOnly subsequent sales by selected participants in the same mint qualify. A sale is not a full exit; old groups are not replayed.\n\n/sales_off — disable sales only',
            'sales_off': 'SALE ALERTS OFF\n\nPending sale alerts cancelled. Your co-buy preference is unchanged.\n\n/sales_on — enable sales again',
            'paired': 'WEBSITE LINKED\n\nPairing confirmed. Your existing subscription preference is preserved.\n\nCheck wallets below, then /subscribe to opt in.',
            'subscribed': 'SUBSCRIPTION ENABLED\n\nFuture qualifying co-buys from your selected wallets can now alert. Old events are not replayed.\n\nCheck the wallet selection and collector status below. Subscription ON alone does not mean collection is active.\n\n/stop — pause all alerts',
            'stopped': 'ALERTS PAUSED\n\nPending trade alerts cancelled. Wallet choices and sale preference are preserved.\n\n/subscribe — resume opted-in categories for future qualifying events',
            'status': 'YOUR STATUS\n\nSubscription, sale preference and wallet selection are shown below.',
            'watch_pending': 'WALLET CHANGE PENDING\n\nAlerts paused before changing selection.\n\nIf interrupted, repeat the wallet command, then /subscribe to resume.',
            'invalid_pairing': 'LINK EXPIRED OR INVALID\n\nGet a fresh pairing link from the website, or use /start to continue directly in Telegram.\n\nhttps://fomobill.fun/workspace/',
            'pairing_conflict': 'PAIRING NOT CHANGED\n\nThis chat or website is already linked to another identity. Existing wallets and preferences are preserved.\n\nUse /status to check this chat; use the originally linked browser for website monitoring.',
            'capacity': 'ACCOUNT LIMIT REACHED\n\nNo new account was created. Please try again later.',
            'unknown': 'COMMAND NOT RECOGNIZED\n\n/help — setup and commands\n/status — your current settings'}
        return '△ FOMOBILL · ' + messages.get(outcome, messages['unknown']) + '\n\n' + status

    def drain_replies(self, send, send_photo=None):
        """One paced attempt per tick; three attempts/ten minutes, independent of opt-in."""
        now = self.clock()
        with self.lock, self.connect() as db:
            db.execute("UPDATE command_replies SET state='expired' WHERE state='pending' AND expires<=?", (now,))
            if db.execute("SELECT value FROM meta WHERE key='send_after'").fetchone()[0] > now: return 'cooldown'
            row = db.execute("SELECT * FROM command_replies WHERE state='pending' AND next_try<=? ORDER BY next_try,created,update_id LIMIT 1", (now,)).fetchone()
            if not row: return 'idle'
            # Full existing guide exceeds the caption limit; checkpoint media
            # separately so text retries never replay an accepted photo or pairing.
            if row['photo_state'] == 'pending' and send_photo is not None:
                attempts = row['attempts'] + 1
                photo_id, photo_state, delay = None, 'sent', 1
                try:
                    photo_id = send_photo(row['chat'])
                    if type(photo_id) is not int or photo_id <= 0: raise DeliveryError('invalid_response')
                except Exception as error:
                    delay = max(30 * 2 ** (attempts - 1), error.retry_after if isinstance(error, DeliveryError) else 0)
                    photo_state = 'fallback' if attempts >= 3 or (isinstance(error, DeliveryError) and error.permanent) else 'pending'
                    if isinstance(error, DeliveryError) and str(error) == 'rate_limited':
                        db.execute("UPDATE meta SET value=max(value,?) WHERE key='send_after'", (now + delay,))
                db.execute('UPDATE command_replies SET photo_state=?,photo_message_id=?,attempts=?,next_try=? WHERE update_id=?',
                           (photo_state, photo_id, attempts if photo_state == 'pending' else 0,
                            now + (delay if photo_state == 'pending' else 1), row['update_id']))
                db.execute("UPDATE meta SET value=max(value,?) WHERE key='send_after'", (now + 1,))
                return 'photo_' + photo_state
            attempts, message_id, state, delay = row['attempts'] + 1, None, 'sent', 1
            try:
                message_id = send(row['chat'], row['text'])
                if type(message_id) is not int or message_id <= 0: raise DeliveryError('invalid_response')
            except Exception as error:
                permanent = isinstance(error, DeliveryError) and error.permanent
                delay = max(30 * 2 ** (attempts - 1), error.retry_after if isinstance(error, DeliveryError) else 0)
                state = 'failed' if permanent or attempts >= 3 else 'pending'
                if isinstance(error, DeliveryError) and str(error) == 'rate_limited':
                    db.execute("UPDATE meta SET value=max(value,?) WHERE key='send_after'", (now + delay,))
            db.execute("UPDATE meta SET value=max(value,?) WHERE key='send_after'", (now + 1,))
            db.execute('UPDATE command_replies SET state=?,attempts=?,next_try=?,message_id=? WHERE update_id=?',
                       (state, attempts, now + delay, message_id, row['update_id']))
            return 'retry' if state == 'pending' else state

    def stop(self, owner):
        """Web pause; caller must authenticate this owner and protect against CSRF."""
        self._owner(owner)
        with self.lock, self.connect() as db:
            db.execute('UPDATE recipients SET active=0 WHERE owner=?', (owner,))
            db.execute("UPDATE outbox SET state='cancelled' WHERE owner=? AND state='pending'", (owner,))
            db.execute("UPDATE command_replies SET state='superseded' WHERE chat IN (SELECT chat FROM recipients WHERE owner=?) AND state='pending'", (owner,))

    def enqueue(self, owner, event_id, text, *, occurred_at, captured_at, sale_group_at=None):
        """Queue an already authorized owner/event association; no broadcast API.

        Event IDs must include source and monitor/group episode identity. Text must
        be server-rendered plain text, not a client-supplied arbitrary message.
        """
        self._owner(owner)
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 256:
            raise ValueError('Invalid event identity')
        if not isinstance(text, str) or not text or len(text.encode('utf-16-le')) // 2 > 3000:
            raise ValueError('Plain text must fit 3000 UTF-16 units')
        event_time, capture = timestamp(occurred_at), timestamp(captured_at)
        now = self.clock()
        if not now - 3600 < event_time <= capture <= now or capture < now - 300:
            return False
        message = text + '\n\nEVENT TIME · UTC\n' + datetime.fromtimestamp(event_time, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        with self.lock, self.connect() as db:
            db.execute('DELETE FROM outbox WHERE created<?', (now - 7 * 86400,))
            db.execute("UPDATE outbox SET state='expired' WHERE state='pending' AND expires<=?", (now,))
            row = db.execute('SELECT * FROM recipients WHERE owner=?', (owner,)).fetchone()
            if not row or not row['active'] or event_time <= row['since']: return False
            if event_id.startswith('pump:sale:'):
                if (not row['sales'] or sale_group_at is None
                    or not max(row['since'],row['sales_since']) < timestamp(sale_group_at) < event_time):return False
            if db.execute('SELECT count(*) FROM outbox').fetchone()[0] >= 10000: return False
            if db.execute("SELECT count(*) FROM outbox WHERE owner=? AND state='pending'", (owner,)).fetchone()[0] >= 100: return False
            result = db.execute('INSERT OR IGNORE INTO outbox(owner,event,text,created,expires,next_try) VALUES (?,?,?,?,?,?)',
                                (owner, event_id, message, now, event_time + 3600, now))
            return result.rowcount == 1

    def drain(self, send):
        """Attempt at most one message per tick; caller drains commands first.

        Singleton worker and shared Notifications object required. Lock serializes
        local consent changes with bounded transport. A send already in flight
        cannot be recalled. Failed sends never claim success.
        """
        now = self.clock()
        with self.lock, self.connect() as db:
            db.execute("UPDATE outbox SET state='expired' WHERE state='pending' AND expires<=?", (now,))
            if db.execute("SELECT value FROM meta WHERE key='send_after'").fetchone()[0] > now: return 'cooldown'
            row = db.execute("""SELECT o.*,r.chat FROM outbox o JOIN recipients r ON r.owner=o.owner
                                WHERE o.state='pending' AND r.active=1 AND o.next_try<=?
                                ORDER BY o.next_try,o.created LIMIT 1""", (now,)).fetchone()
            if not row: return 'idle'
            attempts = row['attempts'] + 1
            state, delay, outcome = 'sent', 1, 'sent'
            message_id = None
            try:
                accepted = send(row['chat'], row['text'])
                if type(accepted) is int and accepted > 0:
                    message_id = accepted
            except Exception as error:
                # Never store/log third-party exception text: URLs contain tokens.
                permanent = isinstance(error, DeliveryError) and error.permanent
                delay = max(30 * 2 ** (attempts - 1), error.retry_after if isinstance(error, DeliveryError) else 0)
                state = 'failed' if permanent or attempts >= 5 else 'pending'
                outcome = 'failed' if state == 'failed' else 'retry'
                if permanent:
                    db.execute('UPDATE recipients SET active=0 WHERE owner=?', (row['owner'],))
                    db.execute("UPDATE outbox SET state='cancelled' WHERE owner=? AND state='pending'", (row['owner'],))
            # A provider 429 applies bot-wide; ordinary recipient errors do not.
                if isinstance(error, DeliveryError) and str(error) == 'rate_limited':
                    db.execute("UPDATE meta SET value=? WHERE key='send_after'", (now + delay,))
            db.execute("UPDATE meta SET value=max(value,?) WHERE key='send_after'", (now + 1,))
            db.execute('UPDATE outbox SET state=?,attempts=?,next_try=?,message_id=? WHERE owner=? AND event=?',
                       (state, attempts, now + delay, message_id, row['owner'], row['event']))
            return outcome


# Only this dedicated secret is ever consulted. No gateway or generic fallback.
import json
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

MAX_TELEGRAM_RESPONSE = 256 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DeliveryError('rejected', permanent=True)


class Telegram:
    def __init__(self, token, opener=None):
        if not isinstance(token, str) or not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{30,100}', token):
            raise ValueError('Invalid dedicated Telegram token format')
        self._token = token
        self._opener = opener or build_opener(NoRedirect())

    @classmethod
    def from_env(cls):
        token = os.environ.get('FOMOBILL_TELEGRAM_BOT_TOKEN')
        return cls(token) if token else None

    def call(self, method, body):
        if method not in ('sendMessage', 'sendPhoto', 'getUpdates', 'getMe', 'getWebhookInfo'):
            raise ValueError('Telegram method not allowlisted')
        content_type = 'application/json'
        if method == 'sendPhoto':
            boundary = 'fomobill-' + secrets.token_hex(16)
            fields = []
            for key, value in body.items():
                fields.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="' + key + '"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n' + json.dumps(value) + '\r\n').encode())
            try:
                with (Path(__file__).parent / 'static/fomobill-home/assets/telegram-welcome.png').open('rb') as asset:
                    photo = asset.read(2_000_001)
                if len(photo) > 2_000_000 or not photo.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError()
            except (OSError, ValueError):
                raise DeliveryError('rejected', permanent=True) from None
            fields.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="photo"; filename="fomobill-welcome.png"\r\nContent-Type: image/png\r\n\r\n').encode() + photo + ('\r\n--' + boundary + '--\r\n').encode())
            payload = b''.join(fields)
            content_type = 'multipart/form-data; boundary=' + boundary
        else:
            payload = json.dumps(body, allow_nan=False).encode()
        request = Request('https://api.telegram.org/bot' + self._token + '/' + method,
                          data=payload,
                          headers={'Content-Type': content_type, 'User-Agent': 'FOMOBILL/notifications'})
        try:
            try:
                response = self._opener.open(request, timeout=8)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read(MAX_TELEGRAM_RESPONSE + 1)
            if len(raw) > MAX_TELEGRAM_RESPONSE: raise DeliveryError('invalid_response')
            data = json.loads(raw)
            if not isinstance(data, dict): raise DeliveryError('invalid_response')
            if data.get('ok') is not True:
                code = data.get('error_code')
                params = data.get('parameters')
                retry = params.get('retry_after', 30) if isinstance(params, dict) else 30
                raise DeliveryError('rate_limited' if code == 429 else 'rejected',
                                    retry_after=retry if code == 429 else 0,
                                    permanent=code in (400, 403))
            if 'result' not in data: raise DeliveryError('invalid_response')
            return data['result']
        except DeliveryError:
            raise
        except Exception:
            # Suppress chained exceptions too: urllib embeds token in request URL.
            raise DeliveryError('temporary') from None

    def send(self, chat, text):
        if type(chat) is not int or chat <= 0 or not isinstance(text, str) or len(text.encode('utf-16-le')) // 2 > 4096:
            raise ValueError('Invalid Telegram plain-text message')
        result = self.call('sendMessage', {'chat_id': chat, 'text': text,
                                         'link_preview_options': {'is_disabled': True},
                                         'protect_content': True, 'disable_notification': False})
        if not isinstance(result, dict) or type(result.get('message_id')) is not int or result['message_id'] <= 0:
            raise DeliveryError('invalid_response')
        target = result.get('chat')
        if not isinstance(target, dict) or type(target.get('id')) is not int or target['id'] != chat or target.get('type') != 'private':
            raise DeliveryError('invalid_response')
        return result['message_id']

    def send_welcome(self, chat):
        if type(chat) is not int or not 0 < chat < 2**52:
            raise ValueError('Invalid private chat')
        result = self.call('sendPhoto', {'chat_id': chat, 'protect_content': True, 'disable_notification': False})
        if not isinstance(result, dict) or type(result.get('message_id')) is not int or result['message_id'] <= 0:
            raise DeliveryError('invalid_response')
        target = result.get('chat')
        if not isinstance(target, dict) or type(target.get('id')) is not int or target['id'] != chat or target.get('type') != 'private':
            raise DeliveryError('invalid_response')
        return result['message_id']

    def updates(self, offset):
        if type(offset) is not int or offset < 0: raise ValueError('Invalid update offset')
        result = self.call('getUpdates', {'offset': offset, 'limit': 100, 'timeout': 0,
                                         'allowed_updates': ['message']})
        if not isinstance(result, list) or len(result) > 100: raise DeliveryError('invalid_response')
        return result


def x_context(user_url=None, metadata=None):
    """Pure link/provenance discovery; no URL is fetched and no AI is called.

    metadata is a DexScreener pair's info object from an already allowlisted
    token lookup, NOT an arbitrary client JSON object. Provider-reported links
    are not proof of project control, endorsement, or post content.
    """
    from urllib.parse import urlsplit
    import ipaddress

    def safe_url(value, kind):
        if not isinstance(value, str) or len(value) > 512 or re.search(r'[\s\\<>"\x00-\x1f\x7f]', value): return False
        try:
            parts = urlsplit(value)
            if parts.scheme != 'https' or parts.username or parts.password or parts.port or parts.query or parts.fragment: return False
            host = parts.hostname or ''
            if parts.netloc != host: return False
            if kind == 'x':
                return host in ('x.com', 'twitter.com', 'www.x.com', 'www.twitter.com') and bool(re.fullmatch(r'/[A-Za-z0-9_]{1,15}(?:/status/[0-9]{1,25})?/?', parts.path))
            if kind == 'telegram':
                return host == 't.me' and bool(re.fullmatch(r'/[A-Za-z0-9_]{5,32}/?', parts.path))
            if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host): return False
            if host.endswith(('.local', '.internal', '.localhost', '.invalid', '.test')): return False
            try: ipaddress.ip_address(host); return False
            except ValueError: return True
        except (ValueError, TypeError): return False

    links, seen = [], set()
    def add(url, kind, provenance):
        if not isinstance(url, str) or len(links) >= 12 or url in seen: return
        if safe_url(url, kind):
            seen.add(url)
            links.append({'url': url, 'kind': kind, 'provenance': provenance,
                          'contentRead': False, 'officialVerified': False})
    add(user_url, 'x', 'user_supplied')
    if isinstance(metadata, dict):
        websites, socials = metadata.get('websites'), metadata.get('socials')
        for row in websites[:12] if isinstance(websites, list) else []:
            if isinstance(row, dict): add(row.get('url'), 'website', 'dexscreener_metadata')
        for row in socials[:12] if isinstance(socials, list) else []:
            if not isinstance(row, dict) or not isinstance(row.get('type'), str): continue
            kind = {'twitter': 'x', 'x': 'x', 'telegram': 'telegram'}.get(row.get('type'))
            if kind: add(row.get('url'), kind, 'dexscreener_metadata')
    return {'status': 'links_only' if links else 'unavailable', 'links': links,
            'contentRead': False, 'aiConnected': False,
            'coverage': 'Links only; no X search, post reading, sentiment or project ownership verification.'}

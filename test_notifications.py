"""Offline tests only: no real Telegram credentials or network calls."""
import tempfile
import unittest
from pathlib import Path
import notifications as n


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = 1000.0
        self.path = Path(self.tmp.name) / 'notifications.sqlite3'
        self.store = n.Notifications(self.path, clock=lambda: self.clock)

    def update(self, uid, text, chat=12, kind='private', sender=None):
        return {'update_id': uid, 'message': {'text': text, 'chat': {'id': chat, 'type': kind},
                                            'from': {'id': chat if sender is None else sender}}}

    def test_pairing_is_expiring_one_time_and_requires_opt_in(self):
        token = self.store.create_pairing('owner-1')
        self.assertFalse(self.store.status('owner-1')['active'])
        self.assertEqual(self.store.handle_update(self.update(1, '/start ' + token)), 'paired')
        self.assertFalse(self.store.status('owner-1')['active'])
        self.assertEqual(self.store.handle_update(self.update(2, '/start ' + token, chat=13)), 'invalid_pairing')
        self.assertEqual(self.store.handle_update(self.update(3, '/subscribe')), 'subscribed')
        cutoff = self.store.status('owner-1')['since']
        self.clock += 1
        self.store.handle_update(self.update(4, '/subscribe'))
        self.assertEqual(self.store.status('owner-1')['since'], cutoff)
        self.store.handle_update(self.update(5, '/start'))
        self.assertTrue(self.store.status('owner-1')['active'])
        self.store.handle_update(self.update(6, '/stop'))
        self.assertFalse(self.store.status('owner-1')['active'])
        other = self.store.create_pairing('owner-2')
        self.clock += 601
        self.assertEqual(self.store.handle_update(self.update(7, '/start ' + other, chat=13)), 'invalid_pairing')
        reopened = n.Notifications(self.path, clock=lambda: self.clock)
        self.assertEqual(reopened.offset(), 8)
        self.assertFalse(reopened.status('owner-1')['active'])

    def test_native_private_identity_and_durable_command_reply(self):
        self.assertEqual(self.store.handle_update(self.update(1, '/start')), 'help')
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM recipients WHERE chat=12').fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['owner'], 'telegram_00000000000000000000000000000012')
        self.assertFalse(row['active'])
        self.assertEqual(self.store.handle_update(self.update(2, '/subscribe')), 'subscribed')
        self.assertTrue(self.store.status('telegram_00000000000000000000000000000012')['active'])
        self.store.handle_update(self.update(3, '/stop'))
        self.store.handle_update(self.update(4, '/status'))
        calls = []
        reopened = n.Notifications(self.path, clock=lambda: self.clock)
        self.assertEqual(reopened.drain_replies(lambda chat, text: calls.append((chat, text)) or 77), 'sent')
        self.assertEqual(calls[0][0], 12)
        self.assertIn('OFF', calls[0][1])
        with reopened.connect() as db:
            row = db.execute("SELECT * FROM command_replies WHERE state='sent'").fetchone()
        self.assertEqual(row['message_id'], 77)
        self.assertEqual(row['command'], '/status')

    def subscribe(self, owner='owner-1', chat=12, uid=1):
        self.store.handle_update(self.update(uid, '/start ' + self.store.create_pairing(owner), chat))
        self.store.handle_update(self.update(uid + 1, '/subscribe', chat))
        self.clock += 1

    def enqueue(self, owner='owner-1', event='e1', **kwargs):
        return self.store.enqueue(owner, event, 'Observed activity <not HTML>',
                                  occurred_at=kwargs.get('occurred_at', self.clock),
                                  captured_at=kwargs.get('captured_at', self.clock))

    def test_trade_delivery_retains_provider_receipt_after_restart(self):
        self.subscribe()
        self.assertTrue(self.enqueue())
        self.assertEqual(self.store.drain(lambda chat, text: 431), 'sent')
        reopened = n.Notifications(self.path, clock=lambda: self.clock)
        with reopened.connect() as db:
            row = dict(db.execute('SELECT * FROM outbox').fetchone())
        self.assertEqual(row.get('message_id'), 431)
        self.assertFalse(self.enqueue())
        self.clock += 2
        self.assertEqual(reopened.drain(lambda *a: self.fail('duplicate')), 'idle')

    def test_legacy_outbox_migration_preserves_consent_and_dedup(self):
        self.subscribe()
        self.enqueue()
        with self.store.connect() as db:
            db.execute('ALTER TABLE outbox DROP COLUMN message_id')
            before = tuple(db.execute('SELECT * FROM recipients').fetchone())
        reopened = n.Notifications(self.path, clock=lambda: self.clock)
        with reopened.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM recipients').fetchone()), before)
            self.assertIsNone(db.execute('SELECT message_id FROM outbox').fetchone()[0])
        self.assertFalse(self.enqueue())
        self.assertEqual(reopened.drain(lambda *a: 432), 'sent')
        with reopened.connect() as db:
            self.assertEqual(db.execute('SELECT message_id FROM outbox').fetchone()[0],432)

    def test_outbox_dedup_retry_stop_and_restart(self):
        self.assertFalse(self.enqueue())
        self.subscribe()
        self.assertTrue(self.enqueue())
        self.assertFalse(self.enqueue())
        calls = []
        def failing(chat, text):
            calls.append(chat)
            raise n.DeliveryError('temporary')
        self.store.drain(failing)
        self.assertEqual(calls, [12])
        self.store.drain(failing)
        self.assertEqual(calls, [12])
        self.clock += 31
        self.store = n.Notifications(self.path, clock=lambda: self.clock)
        self.store.drain(lambda chat, text: calls.append(chat))
        self.assertEqual(calls, [12, 12])
        self.assertFalse(self.enqueue())
        self.assertTrue(self.enqueue(event='e2'))
        self.clock += 1
        self.store.handle_update(self.update(3, '/stop'))
        self.assertEqual(self.store.drain(lambda *args: calls.append('unexpected')), 'idle')
        self.store.handle_update(self.update(4, '/subscribe'))
        self.clock += 31
        self.assertEqual(self.store.drain(lambda *args: calls.append('unexpected')), 'idle')
        self.assertEqual(calls, [12, 12])
        self.assertFalse(self.enqueue(event='old', occurred_at=1000))
        self.assertFalse(self.enqueue(event='future', occurred_at=self.clock + 1))
        self.assertFalse(self.enqueue(event='stale', captured_at=self.clock - 301))

    def test_transport_is_fixed_host_bounded_plain_text_and_redacts_errors(self):
        import json
        from unittest.mock import patch
        class Response:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return self.body[:size]
        class Opener:
            def __init__(self): self.requests = []; self.body = b'{"ok":true,"result":{"message_id":7,"chat":{"id":12,"type":"private"}}}'
            def open(self, request, timeout):
                self.requests.append((request, timeout))
                return Response(self.body)
        opener = Opener()
        with patch.dict('os.environ', {}, clear=True):
            self.assertIsNone(n.Telegram.from_env())
        bot = n.Telegram('12345:' + 'x' * 35, opener=opener)
        bot.send(12, '<b>plain</b>')
        request, timeout = opener.requests[-1]
        self.assertEqual(request.full_url, 'https://api.telegram.org/bot12345:' + 'x' * 35 + '/sendMessage')
        body = json.loads(request.data)
        self.assertNotIn('parse_mode', body)
        self.assertTrue(body['link_preview_options']['is_disabled'])
        self.assertEqual(timeout, 8)
        opener.body = b'{"ok":false,"error_code":429,"parameters":{"retry_after":900},"description":"secret"}'
        with self.assertRaises(n.DeliveryError) as caught: bot.send(12, 'test')
        self.assertEqual(caught.exception.retry_after, 900)
        self.assertNotIn('secret', str(caught.exception))
        opener.body = b'x' * (n.MAX_TELEGRAM_RESPONSE + 1)
        with self.assertRaises(n.DeliveryError): bot.send(12, 'test')
        with self.assertRaises(ValueError): bot.call('arbitrary', {})
        with self.assertRaises(ValueError): n.Telegram('https://attacker/token')
        with self.assertRaises(n.DeliveryError): n.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://attacker')

    def test_send_rejects_wrong_recipient_and_invalid_message_id(self):
        from unittest.mock import patch
        bot = n.Telegram('12345:' + 'x' * 35)
        for result in [{'message_id': 7, 'chat': {'id': 13, 'type': 'private'}},
                       {'message_id': 0, 'chat': {'id': 12, 'type': 'private'}},
                       {'message_id': 7},
                       {'message_id': 7, 'chat': {'id': 12, 'type': 'group'}}]:
            with patch.object(bot, 'call', return_value=result):
                with self.assertRaises(n.DeliveryError): bot.send(12, 'test')
        with patch.object(bot, 'call', return_value={'message_id': 7, 'chat': {'id': 12, 'type': 'private'}}):
            self.assertEqual(bot.send(12, 'test'), 7)

    def test_x_context_never_claims_read_content_or_project_verification(self):
        context = n.x_context('https://x.com/example/status/123', {
            'websites': [{'url': 'https://example.org'}],
            'socials': [{'type': 'twitter', 'url': 'https://x.com/example'},
                        {'type': 'twitter', 'url': 'https://x.com.evil.org/example'},
                        {'type': 'telegram', 'url': 'https://t.me/example'}]})
        self.assertEqual(context['status'], 'links_only')
        self.assertFalse(context['contentRead'])
        self.assertFalse(context['aiConnected'])
        self.assertEqual(len(context['links']), 4)
        self.assertEqual(context['links'][0]['provenance'], 'user_supplied')
        self.assertFalse(context['links'][1]['officialVerified'])
        for bad in ['http://x.com/a', 'https://x.com@evil.org/a', 'https://x.com:443/a',
                    'https://x.com/a?redirect=x', 'https://x.com/a\n', 'https://x.com/a/status/nope']:
            self.assertEqual(n.x_context(bad)['links'], [])
        self.assertEqual(n.x_context(metadata={'socials': [{'type': [], 'url': 'https://x.com/example'}]})['links'], [])
        self.assertEqual(n.x_context(metadata={'websites': [{'url': 'http://127.0.0.1'},
            {'url': 'https://localhost'}, {'url': 'javascript:alert(1)'}]})['links'], [])

    def test_two_recipients_failure_isolation_and_long_rate_limit(self):
        self.subscribe()
        self.subscribe('owner-2', 13, 3)
        self.assertTrue(self.enqueue())
        self.assertTrue(self.enqueue('owner-2'))
        calls = []
        def transport(chat, text):
            calls.append(chat)
            if chat == 12: raise n.DeliveryError('temporary')
        self.assertEqual(self.store.drain(transport), 'retry')
        self.clock += 1
        self.assertEqual(self.store.drain(transport), 'sent')
        self.assertEqual(calls, [12, 13])
        self.clock += 30
        def rate_limit(*args): raise n.DeliveryError('rate_limited', retry_after=900)
        self.assertEqual(self.store.drain(rate_limit), 'retry')
        self.store = n.Notifications(self.path, clock=lambda: self.clock)
        self.clock += 899
        self.assertEqual(self.store.drain(transport), 'cooldown')
        self.clock += 1
        self.assertEqual(self.store.drain(lambda *args: None), 'sent')

    def test_web_stop_suppresses_stale_subscription_reply(self):
        self.subscribe()
        self.store.stop('owner-1')
        self.assertEqual(self.store.drain_replies(lambda *a: self.fail('stale ON reply')), 'idle')

    def test_command_retries_pacing_boundaries_and_identity_isolation(self):
        self.store.handle_update(self.update(1, '/subscribe'))
        self.store.handle_update(self.update(2, '/start', chat=13))
        def fail(*args): raise n.DeliveryError('temporary')
        self.assertEqual(self.store.drain_replies(fail), 'retry')
        self.assertEqual(self.store.drain_replies(lambda *a: 5), 'cooldown')
        self.clock += 1
        self.assertEqual(self.store.drain_replies(lambda *a: 5), 'sent')
        self.clock += 30
        self.assertEqual(self.store.drain_replies(fail), 'retry')
        self.clock += 60
        self.assertEqual(self.store.drain_replies(fail), 'failed')
        token=self.store.create_pairing('a'*64)
        self.assertEqual(self.store.handle_update(self.update(3, '/start '+token)), 'pairing_conflict')
        self.assertFalse(self.store.status('a'*64)['paired'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT active FROM recipients WHERE chat=12').fetchone()[0],1)
        self.store.handle_update(self.update(4, '/stop'))
        self.clock += 1
        def rate(*args):raise n.DeliveryError('rate_limited',retry_after=901)
        self.assertEqual(self.store.drain_replies(rate),'retry')
        self.clock += 900
        self.assertEqual(self.store.drain_replies(lambda *a: 8),'cooldown')
        self.clock += 1
        self.assertEqual(self.store.drain_replies(lambda *a: 8),'idle')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT active FROM recipients WHERE chat=12').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT active FROM recipients WHERE chat=13').fetchone()[0],0)
        for i in range(5,1010):self.store.handle_update(self.update(i,'/status'))
        with self.store.connect() as db:
            self.assertLessEqual(db.execute('SELECT count(*) FROM command_replies').fetchone()[0],1000)
            self.assertEqual(db.execute("SELECT count(*) FROM command_replies WHERE state='pending'").fetchone()[0],1)
        self.assertEqual(self.store.handle_update(self.update(1009,'/subscribe')),'duplicate')

    def test_retry_attempt_cap_and_expiry(self):
        self.subscribe()
        self.assertTrue(self.enqueue())
        def fail(*args): raise n.DeliveryError('temporary')
        for index in range(5):
            self.assertEqual(self.store.drain(fail), 'retry' if index < 4 else 'failed')
            self.clock += 30 * 2 ** index
        self.assertEqual(self.store.drain(fail), 'idle')
        self.assertTrue(self.enqueue(event='expiring'))
        self.clock += 3601
        self.assertEqual(self.store.drain(lambda *args: self.fail('expired')), 'idle')

    def test_private_sender_replay_and_pair_conflict(self):
        token = self.store.create_pairing('owner-1')
        for uid, kind, sender in [(1, 'group', 12), (2, 'private', 99), (3, 'private', True)]:
            self.assertEqual(self.store.handle_update(self.update(uid, '/start ' + token, kind=kind, sender=sender)), 'ignored')
        self.assertFalse(self.store.status('owner-1')['paired'])
        self.store.handle_update(self.update(4, '/start ' + token))
        self.store.handle_update(self.update(5, '/subscribe'))
        self.store.handle_update(self.update(6, '/stop'))
        self.assertEqual(self.store.handle_update(self.update(5, '/subscribe')), 'duplicate')
        self.assertFalse(self.store.status('owner-1')['active'])
        new_token = self.store.create_pairing('owner-1')
        self.assertEqual(self.store.handle_update(self.update(7, '/start ' + new_token, chat=13)), 'pairing_conflict')
        self.assertEqual(self.store.handle_update({'update_id': 8, 'edited_message': self.update(1, '/subscribe')['message']}), 'ignored')
        with self.store.connect() as db:
            self.assertIsNone(db.execute('SELECT digest FROM pairing WHERE digest=?', (new_token,)).fetchone())

    def test_iso_timestamp_bounds_queue_capacity_and_web_stop(self):
        self.subscribe()
        iso = '1970-01-01T00:16:41+00:00'
        self.assertTrue(self.enqueue(event='iso', occurred_at=iso, captured_at=iso))
        for i in range(99): self.assertTrue(self.enqueue(event='cap-' + str(i)))
        self.assertFalse(self.enqueue(event='overflow'))
        for bad in [None, True, float('nan'), float('inf'), '2026-01-01', 'not-a-date']:
            with self.assertRaises(ValueError): self.enqueue(event='bad', occurred_at=bad)
        with self.assertRaises(ValueError):
            self.store.enqueue('owner-1', 'long', '😀' * 1501, occurred_at=self.clock, captured_at=self.clock)
        self.store.stop('owner-1')
        self.assertFalse(self.store.status('owner-1')['active'])
        self.store.handle_update(self.update(3, '/subscribe'))
        self.assertEqual(self.store.drain(lambda *args: self.fail('web-paused queue')), 'idle')

    def test_permanent_rejection_pauses_only_affected_recipient(self):
        self.subscribe()
        self.subscribe('owner-2', 13, 3)
        self.enqueue(); self.enqueue(event='e2'); self.enqueue('owner-2')
        def blocked(*args): raise n.DeliveryError('rejected', permanent=True)
        self.assertEqual(self.store.drain(blocked), 'failed')
        self.assertFalse(self.store.status('owner-1')['active'])
        self.assertTrue(self.store.status('owner-2')['active'])
        self.clock += 1
        calls = []
        self.store.drain(lambda chat, text: calls.append(chat))
        self.assertEqual(calls, [13])

    def test_producer_shaped_iso_capture_and_monitor_group_contract(self):
        # Synthetic inputs through actual producers, not live chain evidence.
        from unittest.mock import patch
        import app
        import monitoring
        self.subscribe()
        address = 'So11111111111111111111111111111111111111112'
        with patch.object(app, 'now', return_value='1970-01-01T00:16:41+00:00'):
            payload = app.Sources(fetch=lambda *args: {'result': []}).wallet(address)
        self.assertEqual(payload['observedAt'], '1970-01-01T00:16:41+00:00')
        trades = [{'id': 'synthetic-a', 'wallet': 'wallet-a', 'mint': address, 'side': 'buy',
                   'qualifiesForGroup': True, 'blockTime': 1000},
                  {'id': 'synthetic-b', 'wallet': 'wallet-b', 'mint': address, 'side': 'buy',
                   'qualifiesForGroup': True, 'blockTime': 1001}]
        group = monitoring.group_trades(trades)[0]
        self.assertTrue(self.store.enqueue('owner-1', 'pump:group:' + group['id'],
                        'TEST ONLY: observed group; not coordination',
                        occurred_at=group['triggerTime'], captured_at=payload['observedAt']))

    def test_transport_http_429_invalid_json_timeout_and_update_contract(self):
        import io
        import json
        from urllib.error import HTTPError
        class Opener:
            response = None
            def open(self, request, timeout):
                self.request = request
                if isinstance(self.response, Exception): raise self.response
                return io.BytesIO(self.response)
        opener = Opener()
        bot = n.Telegram('12345:' + 'x' * 35, opener=opener)
        opener.response = HTTPError('https://secret.invalid/token', 429, 'SECRET', {},
                                   io.BytesIO(b'{"ok":false,"error_code":429,"parameters":{"retry_after":901}}'))
        with self.assertRaises(n.DeliveryError) as caught: bot.send(12, 'test')
        self.assertEqual(caught.exception.retry_after, 901)
        for response in [b'not-json', b'[]', b'{"ok":true}', TimeoutError('SECRET')]:
            opener.response = response
            with self.assertRaises(n.DeliveryError) as caught: bot.send(12, 'test')
            self.assertNotIn('SECRET', str(caught.exception))
        opener.response = b'{"ok":true,"result":[]}'
        self.assertEqual(bot.updates(9), [])
        self.assertEqual(json.loads(opener.request.data), {'offset': 9, 'limit': 100, 'timeout': 0, 'allowed_updates': ['message']})


if __name__ == '__main__':
    unittest.main()

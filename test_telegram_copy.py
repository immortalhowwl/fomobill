"""Offline message UX checks; never contacts Telegram."""
import unittest
from notifications import Notifications


class CommandCopyTests(unittest.TestCase):
    def test_help_has_ordered_setup_and_separate_controls(self):
        text = Notifications.command_reply('help', False)
        for section in ('GET STARTED', '1. /watch ADDRESS', '2. /subscribe', 'OPTIONAL SALE ALERTS', 'CONTROLS', 'COVERAGE'):
            self.assertIn(section, text)
        self.assertIn('at least two', text)
        self.assertIn('7 days', text)
        self.assertIn('OFF', text)
        self.assertLess(len(text), 1800)
        self.assertIn('\n\n', text)

    def test_all_outcomes_have_title_body_and_status(self):
        for outcome in ('help','paired','subscribed','stopped','status','watch_pending','invalid_pairing','pairing_conflict','capacity','unknown','sales_on','sales_off'):
            for active in (True, False):
                text = Notifications.command_reply(outcome, active)
                self.assertTrue(text.startswith('△ FOMOBILL'), outcome)
                self.assertIn('\n\n', text)
                self.assertTrue(text.endswith('Alert subscription: '+('ON' if active else 'OFF')+'.'))
                self.assertNotIn('<b>',text)
                self.assertLess(len(text),1800)

class RuntimeCopyTests(unittest.TestCase):
    def test_status_empty_one_and_many_wallets_and_watch_errors(self):
        import tempfile
        from pathlib import Path
        import app
        from monitoring import b58
        from test_sales_notifications import update
        with tempfile.TemporaryDirectory() as d:
            runtime = app.Runtime(Path(d))
            owner = 'telegram_' + str(12).zfill(32)
            for i, command in enumerate(('/start', '/watch invalid', '/watch '+b58(bytes([1])*32)), 1):
                u = update(i, command)
                runtime.telegram_reply_context(u, runtime.notifications.handle_update(u))
                with runtime.notifications.connect() as db:
                    text = db.execute('SELECT text FROM command_replies WHERE update_id=?',(i,)).fetchone()[0]
                self.assertTrue(text.startswith('△ FOMOBILL'))
                self.assertIn('WALLETS', text)
                if i == 1:
                    self.assertIn('No trade alerts can be generated', text)
                if i == 2:
                    self.assertIn('WALLET NOT UPDATED',text)
                    self.assertIn('OFF',text)
                if i == 3:
                    self.assertIn('Add at least one more wallet',text)
            for i in range(2, 9):
                runtime.monitor.watch(owner,b58(bytes([i])*32))
            text = runtime.telegram_watch_status(owner)
            self.assertIn('COLLECTION',text)
            self.assertIn('8 selected wallets',text)
            self.assertLess(len(Notifications.command_reply('help',False)+'\n\n'+text),4000)

    def test_real_receipt_renderers_have_sections_and_event_times(self):
        from test_sales_notifications import HistoricalSalesTests
        fixture = HistoricalSalesTests()
        fixture.setUp()
        try:
            fixture.tick()
            rows = fixture.rows()
            self.assertEqual(len(rows),2)
            for row in rows:
                text = row['text']
                self.assertTrue(text.startswith('△ FOMOBILL'))
                self.assertIn('\n\nTOKEN\n',text)
                self.assertIn('UTC',text)
                self.assertIn('https://solscan.io/tx/',text)
                self.assertLess(len(text),4000)
            sale = next(r['text'] for r in rows if r['event'].startswith('pump:sale:'))
            self.assertIn('\n\nEVENT TIME · UTC\n',sale)
            self.assertIn('not a full exit',sale)
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()

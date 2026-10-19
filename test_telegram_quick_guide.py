"""Offline command-routing and neutral fallback regression tests."""
import tempfile
import unittest
from pathlib import Path
import app
from monitoring import b58


def update(number, text):
    return {'update_id': number, 'message': {'chat': {'id': 12, 'type': 'private'},
            'from': {'id': 12}, 'text': text}}


class QuickGuideTests(unittest.TestCase):
    def test_fallback_and_read_only_commands_preserve_choices(self):
        for active in (False, True):
            with self.subTest(active=active), tempfile.TemporaryDirectory() as directory:
                runtime = app.Runtime(Path(directory))
                notifications = runtime.notifications
                notifications.handle_update(update(1, '/start'))
                owner = 'telegram_' + str(12).zfill(32)
                runtime.monitor.watch(owner, b58(bytes([1]) * 32))
                notifications.handle_update(update(2, '/sales_on'))
                if active:
                    notifications.handle_update(update(3, '/subscribe'))
                before = notifications.status(owner)
                watches = runtime.telegram_watch_status(owner)
                cases = [('hello', 'unknown', 'QUICK GUIDE'),
                         ('/not_a_command', 'unknown', 'QUICK GUIDE'),
                         ('/subscribe_typo', 'unknown', 'QUICK GUIDE'),
                         ('/help', 'help', 'WELCOME'),
                         ('/start', 'help', 'WELCOME'),
                         ('/status', 'status', 'YOUR STATUS'),
                         ('/watches', 'status', 'YOUR STATUS')]
                for number, (command, expected, heading) in enumerate(cases, 10):
                    with self.subTest(command=command):
                        event = update(number, command)
                        outcome = notifications.handle_update(event)
                        self.assertEqual(outcome, expected)
                        runtime.telegram_reply_context(event, outcome)
                        with notifications.connect() as db:
                            row = db.execute('SELECT * FROM command_replies WHERE update_id=?', (number,)).fetchone()
                        self.assertTrue(row['text'].startswith('△ FOMOBILL · ' + heading))
                        self.assertNotIn('COMMAND NOT RECOGNIZED', row['text'])
                        if expected == 'unknown':
                            self.assertIn('/help', row['text'])
                            self.assertIn('/status', row['text'])
                        self.assertEqual(row['photo_state'], 'pending' if command == '/start' else 'none')
                        self.assertEqual(notifications.status(owner), before)
                        self.assertEqual(runtime.telegram_watch_status(owner), watches)


if __name__ == '__main__':
    unittest.main()

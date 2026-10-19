"""First-use public archive contract; no private runtime state or network."""
import hashlib
import json
from pathlib import Path
import unittest
import app
from monitoring import decode_transaction

ROOT=Path(__file__).parent
class FirstUseTests(unittest.TestCase):
    def test_historical_case_uses_exact_public_receipts(self):
        path=ROOT/'static/historical-case.json'
        self.assertTrue(path.exists(), 'Missing dated public historical case')
        case=json.loads(path.read_text())
        self.assertEqual(set(case), {'kind','receipts'})
        self.assertEqual(case['kind'], 'public-historical-receipts')
        trades=[]
        for r in case['receipts']:
            self.assertEqual(set(r), {'signature','endpoint','observedAt','sha256','request','rawResponse'})
            self.assertEqual(hashlib.sha256(r['rawResponse'].encode()).hexdigest(),r['sha256'])
            decoded=decode_transaction(json.loads(r['rawResponse'])['result'],r['signature'])
            self.assertFalse(decoded['gaps'])
            trades.extend(decoded['trades'])
        self.assertEqual(len(trades),3)
        buys=[t for t in trades if t['side']=='buy']; sells=[t for t in trades if t['side']=='sell']
        self.assertEqual(len({t['wallet'] for t in buys}),2)
        self.assertEqual(len({t['mint'] for t in trades}),1)
        self.assertTrue(all(t['qualifiesForGroup'] for t in trades))
        self.assertLessEqual(max(t['blockTime'] for t in buys)-min(t['blockTime'] for t in buys),300)
        self.assertGreater(sells[0]['blockTime'],max(t['blockTime'] for t in buys))
        self.assertIn(sells[0]['wallet'],[t['wallet'] for t in buys])
        self.assertIn('/historical-case.json',app.ROUTES)
        html=(ROOT/'static/index.html').read_text()
        from datetime import datetime, timezone
        for t,r in zip(trades,case['receipts']):
            self.assertIn(t['wallet'],html)
            self.assertIn(t['mint'],html)
            self.assertIn('https://solscan.io/tx/'+t['signature'],html)
            self.assertIn(datetime.fromtimestamp(t['blockTime'],timezone.utc).strftime('%H:%M:%S'),html)
            self.assertEqual(r['request']['params'][0],t['signature'])
            self.assertEqual(r['request']['params'][1]['commitment'],'finalized')

if __name__=='__main__':unittest.main()

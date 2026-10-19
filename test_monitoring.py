"""Offline tests; synthetic RPCs are test doubles, never production evidence."""
import unittest, json, tempfile, pathlib
from monitoring import Monitor, decode_transaction, PROGRAM

FIX = pathlib.Path(__file__).parent/'tests/fixtures'
class MonitoringTests(unittest.TestCase):
    def test_actual_receipt(self):
        r=json.loads((FIX/'real-pump-transaction.json').read_text())
        tx=json.loads(r['body'])['result']
        out=decode_transaction(tx,r['request']['params'][0])
        self.assertIsInstance(out,dict)
        self.assertIn('trades',out)
    def test_malformed(self):
        for tx in [None,{},[],{'meta':[]}, {'meta':{'err':None},'transaction':{}}]:
            self.assertEqual(decode_transaction(tx,'bad')['trades'],[])
    def test_persistence_caps_and_privacy(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'m.db';m=Monitor(p)
            m.watch('a'*32,PROGRAM)
            self.assertEqual(len(Monitor(p).snapshot('a'*32)['watches']),1)
            self.assertEqual(Monitor(p).snapshot('b'*32)['watches'],[])
            m.pause(True);self.assertEqual(m.tick()['status'],'paused')
            m.unwatch('a'*32,PROGRAM);self.assertEqual(m.snapshot('a'*32)['watches'],[])
if __name__=='__main__':unittest.main()

"""Resource, persistence and malformed-source acceptance checks (offline)."""
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile
import app

ADDRESS='So11111111111111111111111111111111111111112'
class LimitsTests(unittest.TestCase):
    def test_persistence_expiry_and_eviction(self):
        with tempfile.TemporaryDirectory() as d:
            cache=app.Cache(Path(d))
            cache.put('first',{'status':'empty','pairs':[]})
            self.assertTrue(app.Cache(Path(d)).get('first')['cache']['hit'])
            for n in range(270):cache.put(str(n),{'status':'empty','pairs':[]})
            with cache.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM cache').fetchone()[0],256)
                db.execute('UPDATE cache SET expires=0')
            self.assertIsNone(cache.get('269'))
    def test_failure_short_cache_and_rate_limits(self):
        with tempfile.TemporaryDirectory() as d:
            cache=app.Cache(Path(d))
            value=cache.put('outage',{'status':'unavailable','activity':[]})
            self.assertEqual(value['cache']['ttlSeconds'],15)
        limit=app.Limiter(2,max_keys=2)
        self.assertTrue(limit.allow('a'));self.assertTrue(limit.allow('a'));self.assertFalse(limit.allow('a'))
        limit.allow('b');limit.allow('c');self.assertEqual(len(limit.rows),2)
    def test_malformed_providers_fail_closed(self):
        for malformed in [None,{}, {'pairs':[]},'bad']:
            result=app.Sources(lambda *args:malformed).market(ADDRESS)
            self.assertEqual(result['status'],'unavailable');self.assertEqual(result['pairs'],[])
        for malformed in [None,{}, {'result':None},{'result':[{'signature':'bad'}]}]:
            result=app.Sources(lambda *args:malformed).wallet(ADDRESS)
            self.assertEqual(result['status'],'unavailable');self.assertEqual(result['activity'],[])
    def test_source_transport_restrictions(self):
        for url in ['http://api.mainnet-beta.solana.com','https://example.org','https://api.mainnet-beta.solana.com/evil']:
            with self.assertRaises(app.ProviderError):app.fetch_json(url)
        with self.assertRaises(app.ProviderError):app.NoRedirect().redirect_request(None,None,302,'',{},'http://localhost')
        with patch('app.build_opener') as opener:
            opener.return_value.open.side_effect=TimeoutError('test-only timeout')
            with self.assertRaisesRegex(app.ProviderError,'timed out'):app.fetch_json(app.RPCS[0],{})
    def test_receipt_hash_reconstructs_original_response(self):
        import os, json, hashlib
        raw=b'{ "result": [], "jsonrpc": "2.0" }'
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'RECEIPTS_DIR':directory}), patch('app.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=raw
            app.fetch_json(app.RPCS[0],{'method':'getSignaturesForAddress'})
            receipt=json.loads(next(Path(directory).glob('receipt-*.json')).read_text())
            self.assertIn('rawResponse',receipt,'Raw bytes must remain reconstructable from a receipt')
            self.assertEqual(hashlib.sha256(receipt['rawResponse'].encode()).hexdigest(),receipt['sha256'])

    def test_numeric_unknowns_stay_unknown(self):
        for value in [None,'bad','NaN','Infinity',True,-1]:self.assertIsNone(app.number(value))
        self.assertEqual(app.number(0),0)

if __name__=='__main__':unittest.main()

"""Offline tests; synthetic provider payloads below are test-only, never app data."""
import unittest
import importlib.util
from pathlib import Path

class ValidationTests(unittest.TestCase):
    def test_solana_address_validation(self):
        self.assertIsNotNone(importlib.util.find_spec('app'), 'Backend not implemented')
        from app import validate_address
        self.assertEqual(validate_address('So11111111111111111111111111111111111111112'), 'So11111111111111111111111111111111111111112')
        for bad in ['0x'+'a'*40, 'https://example.com', '../data', 'a'*44, '', ' So11111111111111111111111111111111111111112', '1'*31]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_address(bad)

class SourceTests(unittest.TestCase):
    def test_market_identity_and_outage(self):
        import app
        self.assertTrue(hasattr(app, 'Sources'), 'Real source adapter missing')
        address = 'So11111111111111111111111111111111111111112'
        pair = {'chainId':'solana','pairAddress':'11111111111111111111111111111111', 'baseToken':{'address':address,'name':'Fixture only','symbol':'TEST'},'quoteToken':{'symbol':'USD'},'dexId':'fixture','priceUsd':'123.5','liquidity':{'usd':200},'volume':{'h24':300}}
        result = app.Sources(fetch=lambda url, body=None: [pair, dict(pair,chainId='ethereum')]).market(address)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['pairs']), 1)
        self.assertEqual(result['pairs'][0]['priceUsd'], '123.5')
        self.assertEqual(result['pairs'][0]['tokenAddress'], address)
        def down(*args): raise app.ProviderError('HTTP 429; retry later')
        result = app.Sources(fetch=down).market(address)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['pairs'], [])
        self.assertIn('429', result['errors'][0]['message'])

class WalletTests(unittest.TestCase):
    def test_bounded_account_activity_and_provider_fallback(self):
        import app
        self.assertTrue(hasattr(app.Sources, 'wallet'), 'Wallet lookup missing')
        address = 'So11111111111111111111111111111111111111112'
        calls = []
        def provider(url, body=None):
            calls.append((url,body))
            if url == app.RPCS[0]: raise app.ProviderError('HTTP 403')
            return {'jsonrpc':'2.0','result':[{'signature':'2'*88,'slot':10,'blockTime':None,'err':None,'confirmationStatus':'finalized'}]*25}
        result = app.Sources(fetch=provider).wallet(address)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['activity']), 1)
        self.assertIsNone(result['activity'][0]['blockTime'])
        self.assertEqual(calls[1][1]['params'][1], {'limit':20,'commitment':'finalized'})
        self.assertEqual(result['source'], app.RPCS[1])
        self.assertEqual(len(result['errors']), 1)
        def outage(*args): return {'error':{'code':-32000,'message':'test-only outage'}}
        result = app.Sources(fetch=outage).wallet(address)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['activity'], [])
        self.assertEqual(len(result['errors']), 2)
        result = app.Sources(fetch=lambda *a: {'result':[]}).wallet(address)
        self.assertEqual(result['status'], 'empty')

if __name__ == '__main__': unittest.main()

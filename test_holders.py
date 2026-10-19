"""Synthetic fixtures only; live receipt is documented separately."""
import copy
import json
import unittest
import importlib.util
from monitoring import b58

MINT=b58(bytes([1])*32)
OWNER=b58(bytes([2])*32)
A=b58(bytes([3])*32)
B=b58(bytes([4])*32)
PROGRAM='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'

def report():
    return {'mint':MINT,'tokenProgram':PROGRAM,'token':{'supply':1000000,'decimals':2},'topHolders':[
        {'address':A,'owner':OWNER,'amount':12345,'decimals':2},
        {'address':B,'owner':OWNER,'amount':55,'decimals':2}], 'knownAccounts':{}}

def transport_report(data):
    def send(url,payload):
        if payload is not None: return b'{}',403,{}
        return json.dumps(data).encode(),200,{}
    return send

class HolderTests(unittest.TestCase):
    def test_public_report_fallback_aggregates_exact_integer_balances(self):
        self.assertIsNotNone(importlib.util.find_spec('holders'),'holders adapter missing')
        import holders
        d=holders.Holders(transport=transport_report(report())).top_holders(MINT)
        self.assertEqual(d['status'],'partial')
        self.assertEqual(d['commitment'],'provider-report')
        self.assertEqual(d['holders'][0]['amountRaw'],'12400')
        self.assertEqual(d['holders'][0]['amount'],'124')
        self.assertEqual(d['holders'][0]['supplyPercent'],'1.24')
        self.assertEqual(d['holders'][0]['classification'],'unknown')
        self.assertIsNone(d['sourceTime'])
        self.assertFalse(d['coverage']['complete'])

class RpcTests(unittest.TestCase):
    def test_rpc_resolves_exact_mint_and_aggregates_owner(self):
        import holders
        def send(url,payload):
            method=payload['method']
            values={'getTokenSupply':{'amount':'1000000','decimals':2},
                'getTokenLargestAccounts':[{'address':A,'amount':'12345','decimals':2},{'address':B,'amount':'55','decimals':2}],
                'getMultipleAccounts':[{'owner':PROGRAM,'data':{'parsed':{'type':'account','info':{'mint':MINT,'owner':OWNER,'tokenAmount':{'amount':str(n),'decimals':2}}}}} for n in (12345,55)]}
            return json.dumps({'result':{'context':{'slot':100},'value':values[method]}}).encode(),200,{}
        d=holders.Holders(transport=send).top_holders(MINT)
        self.assertEqual(d['commitment'],'finalized')
        self.assertEqual(d['holders'][0]['amountRaw'],'12400')
        self.assertEqual(d['coverage']['slots']['accounts'],100)

    def test_explicit_classifications_not_creator_guess(self):
        import holders
        data=report(); data['knownAccounts']={OWNER:{'type':'AMM','name':'Example pool'}}
        d=holders.Holders(transport=transport_report(data)).top_holders(MINT)
        self.assertEqual(d['holders'][0]['classification'],'pool')
        self.assertEqual(d['holders'][0]['classificationEvidence'][0]['account'],OWNER)
        data['knownAccounts'][OWNER]['type']='CREATOR'
        self.assertEqual(holders.Holders(transport=transport_report(data)).top_holders(MINT)['holders'][0]['classification'],'unknown')

    def test_rejects_identity_float_duplicate_and_bad_limits(self):
        import holders
        for change in ('mint','float','duplicate','decimals'):
            data=report()
            if change=='mint': data['mint']=OWNER
            if change=='float': data['topHolders'][0]['amount']=123.0
            if change=='duplicate': data['topHolders'][1]['address']=A
            if change=='decimals': data['topHolders'][0]['decimals']=9
            self.assertEqual(holders.Holders(transport=transport_report(data)).top_holders(MINT)['status'],'unavailable')
        for mint in (' '+MINT,'bad',MINT.lower()):
            if mint==MINT.lower(): continue
            with self.assertRaises(ValueError): holders.Holders().top_holders(mint)
        for limit in (0,21,True):
            with self.assertRaises(ValueError): holders.Holders().top_holders(MINT,limit)

class HistoryTests(unittest.TestCase):
    def test_real_decoder_filters_wallet_and_mint_and_reports_units(self):
        import holders
        from pathlib import Path
        from monitoring import decode_transaction
        receipt=json.loads((Path(__file__).parent/'tests/fixtures/live-tx-1.json').read_text())
        tx=json.loads(receipt['body'])['result']; sig=receipt['request']['params'][0]
        trade=decode_transaction(tx,sig)['trades'][0]
        calls=[]
        def send(url,payload):
            calls.append(payload)
            if payload['method']=='getSignaturesForAddress':
                result=[{'signature':sig,'slot':tx['slot'],'blockTime':tx['blockTime'],'err':None}]
                return json.dumps({'result':result}).encode(),200,{}
            return receipt['body'].encode(),200,{}
        client=holders.Holders(transport=send)
        self.assertTrue(hasattr(client,'wallet_history'),'bounded history API missing')
        result=client.wallet_history(trade['mint'],trade['wallet'],limit=1)
        self.assertEqual(len(result['trades']),1)
        self.assertEqual(result['trades'][0]['nativeUnit'],'SOL')
        self.assertIsNone(result['trades'][0]['usdAmount'])
        self.assertEqual(result['coverage']['nextBefore'],sig)
        self.assertEqual(client.wallet_history(MINT,trade['wallet'],limit=1)['trades'],[])
        self.assertEqual(client.wallet_history(trade['mint'],OWNER,limit=1)['trades'],[])

    def test_malformed_known_accounts_fails_as_dto_not_crash(self):
        import holders
        data=report(); data['knownAccounts']=['not a map']
        d=holders.Holders(transport=transport_report(data)).top_holders(MINT)
        self.assertEqual(d['status'],'unavailable')
        self.assertTrue(d['errors'])

    def test_null_transaction_does_not_advance_cursor(self):
        import holders
        from pathlib import Path
        receipt=json.loads((Path(__file__).parent/'tests/fixtures/live-tx-1.json').read_text())
        sig=receipt['request']['params'][0]
        def send(url,payload):
            result=[{'signature':sig,'err':None}] if payload['method']=='getSignaturesForAddress' else None
            return json.dumps({'result':result}).encode(),200,{}
        d=holders.Holders(transport=send).wallet_history(MINT,OWNER,1)
        self.assertIsNone(d['coverage']['nextBefore'])
        self.assertEqual(len(d['coverage']['gaps']),1)
        self.assertEqual(d['trades'],[])

    def test_transport_allowlist_and_amount_bounds(self):
        import holders
        for url in ('https://evil.example','https://api.rugcheck.xyz/v1/tokens/../report','http://api.mainnet-beta.solana.com'):
            with self.assertRaises(ValueError): holders.public_transport(url,None)
        for n in (-1,True,1.0,'1e3',str(2**64)):
            with self.assertRaises(ValueError): holders.integer(n)
        self.assertEqual(holders.scaled(2**64-1,9),'18446744073.709551615')
        with self.assertRaises(ValueError): holders.NoRedirect().redirect_request(None,None,None,None,None,None)
        with self.assertRaises(ValueError): holders.Holders().wallet_history(MINT,OWNER,before='1'*65)

    def test_history_failure_not_empty_success(self):
        import holders
        client=holders.Holders(transport=lambda u,p:(b'{}',403,{}))
        self.assertTrue(hasattr(client,'wallet_history'),'bounded history API missing')
        result=client.wallet_history(MINT,OWNER)
        self.assertEqual(result['status'],'unavailable')
        self.assertTrue(result['errors'])

if __name__=='__main__': unittest.main()

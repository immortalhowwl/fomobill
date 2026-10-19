"""Bounded token-account pagination; synthetic discovery, real receipt decoding."""
import json
import unittest
from pathlib import Path
from holders import Holders
from monitoring import decode_transaction, b58

ACCOUNT = b58(bytes([7])*32)
PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'

class PaginationTests(unittest.TestCase):
    def setUp(self):
        r=json.loads((Path(__file__).parent/'tests/fixtures/live-tx-1.json').read_text())
        self.tx=json.loads(r['body'])['result']; self.sig=r['request']['params'][0]
        t=decode_transaction(self.tx,self.sig)['trades'][0]
        self.mint=t['mint']; self.wallet=t['wallet']; self.calls=[]

    def send(self,url,p):
        self.calls.append(p)
        if p['method']=='getTokenAccountsByOwner':
            value={'value':[{'pubkey':ACCOUNT,'account':{'owner':PROGRAM,'data':{'parsed':{'type':'account','info':{'mint':self.mint,'owner':self.wallet}}}}}]}
        elif p['method']=='getSignaturesForAddress':
            value=([{'signature':self.sig,'err':None}] if p['params'][0]==ACCOUNT and 'before' not in p['params'][1] else [])
        else: value=self.tx
        return json.dumps({'result':value}).encode(),200,{}

    def test_gap_retains_account_cursor_across_round_robin(self):
        def missing(url,p):
            if p['method']=='getTransaction': return b'{"result":null}',200,{}
            return self.send(url,p)
        c=Holders(missing)
        first=c.wallet_history_page(self.mint,self.wallet,1)
        second=c.wallet_history_page(self.mint,self.wallet,1,cursor=first['coverage']['nextCursor'])
        third=c.wallet_history_page(self.mint,self.wallet,1,cursor=second['coverage']['nextCursor'])
        self.assertEqual(third['coverage']['discoveryAddress'],ACCOUNT)
        self.assertIsNone(third['coverage']['before'])
        self.assertTrue(third['coverage']['gaps'])

    def test_cursor_identity_and_owner_revalidation(self):
        c=Holders(self.send)
        first=c.wallet_history_page(self.mint,self.wallet,1)
        with self.assertRaises(ValueError): c.wallet_history_page(ACCOUNT,self.wallet,cursor=first['coverage']['nextCursor'])
        import base64
        state=json.loads(base64.urlsafe_b64decode(first['coverage']['nextCursor']))
        state['index']=0; state['addresses'][0]=b58(bytes([8])*32)
        cursor=base64.urlsafe_b64encode(json.dumps(state).encode()).decode()
        before=len(self.calls)
        d=c.wallet_history_page(self.mint,self.wallet,cursor=cursor)
        self.assertEqual(d['status'],'unavailable')
        self.assertEqual(d['coverage']['nextCursor'],cursor)
        self.assertEqual(len(self.calls)-before,1)
        for invalid in ('!', 'a'*4097, base64.urlsafe_b64encode(b'null').decode()):
            with self.assertRaises(ValueError):c.wallet_history_page(self.mint,self.wallet,cursor=invalid)

    def test_deadline_stops_requests_and_preserves_continuation(self):
        from unittest.mock import patch
        clock=[0]
        def slow(url,p):
            result=self.send(url,p)
            clock[0]+=11
            return result
        with patch('time.monotonic',side_effect=lambda:clock[0]):
            out=Holders(slow).wallet_history_page(self.mint,self.wallet,limit=1)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(out['coverage']['transactionsFetched'],0)
        self.assertIsNone(out['coverage']['nextBefore'])
        self.assertTrue(out['coverage']['nextCursor'])
        self.assertIn('deadline',out['coverage']['gaps'][0]['message'])

    def test_account_page_finds_exact_trade_and_continues_without_fanout(self):
        c=Holders(self.send)
        self.assertTrue(hasattr(c,'wallet_history_page'),'token-account continuation missing')
        first=c.wallet_history_page(self.mint,self.wallet,limit=1)
        self.assertEqual(len(first['trades']),1)
        self.assertEqual(first['trades'][0]['wallet'],self.wallet)
        cov=first['coverage']; self.assertTrue(cov['ataExpanded'])
        self.assertEqual(cov['discoveryAddress'],ACCOUNT)
        self.assertTrue(cov['nextCursor'])
        self.assertEqual(len(self.calls),3)
        second=c.wallet_history_page(self.mint,self.wallet,limit=1,cursor=cov['nextCursor'])
        self.assertEqual(second['coverage']['discoveryAddress'],self.wallet)
        third=c.wallet_history_page(self.mint,self.wallet,limit=1,cursor=second['coverage']['nextCursor'])
        self.assertIsNone(third['coverage']['nextCursor'])
        self.assertFalse(third['coverage']['complete'])
        sigcalls=[p for p in self.calls if p['method']=='getSignaturesForAddress']
        self.assertEqual(sigcalls[-1]['params'][1]['before'],self.sig)

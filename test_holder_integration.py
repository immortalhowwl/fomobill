"""Isolated HTTP integration; synthetic transport is never production evidence."""
import unittest
import test_integration
from holders import Holders
from monitoring import PROGRAM

MINT='So11111111111111111111111111111111111111112'

class HolderHTTPTests(unittest.TestCase):
    setUp = test_integration.IntegrationTests.setUp
    tearDown = test_integration.IntegrationTests.tearDown
    req = test_integration.IntegrationTests.req
    owner = test_integration.IntegrationTests.owner
    # Reuse lifecycle/request helpers, not inherited test cases.
    def test_public_holders_route_and_cache(self):
        calls=[]
        def transport(url, payload):
            calls.append((url,payload))
            return b'{}',503,{}
        self.server.holders=Holders(transport)
        status,data,headers=self.req('/api/holders?address='+MINT)
        self.assertEqual(status,200)
        self.assertEqual(data['schema'],'holders.v1')
        self.assertEqual(data['mint'],MINT)
        self.assertEqual(data['status'],'unavailable')
        self.assertEqual(data['cache']['ttlSeconds'],15)
        count=len(calls)
        self.assertTrue(self.req('/api/holders?address='+MINT)[1]['cache']['hit'])
        self.assertEqual(len(calls),count)
        self.assertNotIn('Set-Cookie',headers)
        self.assertIsNone(self.server.runtime.worker)

    def test_history_keys_validation_and_no_enrollment(self):
        calls=[]
        class Public:
            def wallet_history_page(self,mint,wallet,limit=10,cursor=None):
                calls.append((mint,wallet,limit,cursor))
                return {'schema':'holder-history.v1','status':'partial','mint':mint,'wallet':wallet,'trades':[]}
        self.server.holders=Public()
        a,b=self.owner(),self.owner()
        self.req('/api/monitor/watch','POST',{'address':PROGRAM},a)
        url='/api/holder-trades?address='+PROGRAM+'&mint='+MINT
        first=self.req(url,cookie=a)
        self.assertEqual(first[0],200)
        self.assertFalse(first[1]['cache']['hit'])
        self.assertTrue(self.req(url,cookie=b)[1]['cache']['hit'])
        self.assertEqual(len(calls),1)
        self.assertEqual(self.req('/api/holder-trades?address='+MINT+'&mint='+PROGRAM)[0],200)
        self.assertEqual(len(calls),2)
        self.assertEqual(self.req('/api/monitor',cookie=b)[1]['watches'],[])
        self.assertEqual(len(self.req('/api/monitor',cookie=a)[1]['watches']),1)
        self.assertFalse(self.req('/api/telegram',cookie=a)[1]['active'])
        for suffix in ['&owner=evil','&limit=100','&mint='+MINT,'&before=bad']:
            self.assertEqual(self.req(url+suffix)[0],400)
        for path in ['/api/holder-trades?address='+PROGRAM, '/api/holder-trades?address=bad&mint='+MINT, '/api/holders?address='+MINT+'&limit=0']:
            self.assertEqual(self.req(path)[0],400)
        self.assertIsNone(self.server.runtime.worker)

    def test_history_cursor_http_cache_and_bounds(self):
        calls=[]
        class Public:
            def wallet_history_page(self,mint,wallet,limit=5,cursor=None):
                calls.append(cursor)
                return {'status':'partial','trades':[], 'coverage':{'nextCursor':cursor}}
        self.server.holders=Public()
        url='/api/holder-trades?address='+PROGRAM+'&mint='+MINT
        for cursor in (None,'a'*4096,'b'*400):
            target=url+('&cursor='+cursor if cursor else '')
            self.assertEqual(self.req(target)[0],200)
            self.assertTrue(self.req(target)[1]['cache']['hit'])
        self.assertEqual(calls,[None,'a'*4096,'b'*400])
        for suffix in ('&cursor=','&cursor=x&cursor=y','&cursor='+'x'*4097):
            self.assertEqual(self.req(url+suffix)[0],400)

    def test_shared_request_limit_network_slots_and_cache_hit(self):
        import app
        self.server.holders=Holders(lambda *a:(b'{}',503,{}))
        self.server.network.acquire();self.server.network.acquire()
        try:self.assertEqual(self.req('/api/holders?address='+MINT)[0],503)
        finally:self.server.network.release();self.server.network.release()
        self.assertEqual(self.req('/api/holders?address='+MINT)[0],200)
        self.server.network.acquire();self.server.network.acquire()
        try:self.assertTrue(self.req('/api/holders?address='+MINT)[1]['cache']['hit'])
        finally:self.server.network.release();self.server.network.release()
        self.server.limiter=app.Limiter(1)
        self.assertEqual(self.req('/api/token?address='+MINT)[0],200)
        self.assertEqual(self.req('/api/holders?address='+MINT)[0],429)

    def test_transport_bytes_allowlist_redirects_and_socket_timeout(self):
        import app
        from unittest.mock import patch
        class Response:
            status=200;headers={}
            def __init__(self):self.chunks=iter([b'{"result":[]}',b''])
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read1(self,n):return next(self.chunks)
        client=app.PublicHolders().client()
        with self.assertRaises(ValueError):client.transport('https://evil.example/',None)
        with patch.object(app,'UPSTREAM_LIMIT',app.Limiter(30)),patch.object(app,'build_opener') as opener:
            opener.return_value.open.return_value=Response()
            result=client.wallet_history(MINT,PROGRAM,limit=5)
            self.assertEqual(result['status'],'partial')
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'],3)
            self.assertIsInstance(opener.call_args.args[0],app.NoRedirect)
        with patch.object(app,'UPSTREAM_LIMIT',app.Limiter(30)),patch.object(app,'HOLDER_MAX_BODY',3),patch.object(app,'build_opener') as opener:
            opener.return_value.open.side_effect=lambda *a,**k:Response()
            result=client.top_holders(MINT)
            self.assertEqual(result['status'],'unavailable')
            self.assertTrue(all('4 MiB' in e['message'] for e in result['errors']))

    def test_same_receipt_amm_history_http_and_exact_quote_units(self):
        import json
        from test_holder_history_sources import REAL,SIG,BONK,WALLET
        calls=[]
        def transport(url,body):
            calls.append(body['method'])
            data={'value':[]} if body['method']=='getTokenAccountsByOwner' else [{'signature':SIG,'err':None,'blockTime':REAL['blockTime']}] if body['method']=='getSignaturesForAddress' else REAL
            return json.dumps({'result':data}).encode(),200,{}
        self.server.holders=Holders(transport)
        status,data,_=self.req('/api/holder-trades?address='+WALLET+'&mint='+BONK)
        self.assertEqual(status,200)
        self.assertEqual(len(data['trades']),2)
        self.assertEqual(calls,['getTokenAccountsByOwner','getSignaturesForAddress','getTransaction'])
        self.assertEqual(len({t['id'] for t in data['trades']}),2)
        self.assertEqual(data['trades'][0]['quoteUnit'],'WSOL')
        self.assertEqual(data['trades'][0]['nativeUnit'],'WSOL')
        self.assertEqual(data['trades'][0]['quoteAmount'],'0.035245023')
        self.assertIsNone(data['trades'][0]['solAmountRaw'])
        self.assertIsNone(data['trades'][0]['usdAmount'])
        self.assertIn('not net wallet proceeds',data['trades'][0]['claim'])
        self.assertTrue(data['coverage']['ammSupported'])
        self.assertFalse(data['coverage']['complete'])
        self.assertEqual(data['coverage']['nextBefore'],SIG)

    def test_malformed_receipt_http_is_coverage_not_crash(self):
        import copy,json
        from test_holder_history_sources import REAL,SIG,BONK,WALLET
        malformed=copy.deepcopy(REAL);malformed['transaction']['signatures']=[]
        def transport(url,body):
            data={'value':[]} if body['method']=='getTokenAccountsByOwner' else [{'signature':SIG,'err':None}] if body['method']=='getSignaturesForAddress' else malformed
            return json.dumps({'result':data}).encode(),200,{}
        self.server.holders=Holders(transport)
        status,data,_=self.req('/api/holder-trades?address='+WALLET+'&mint='+BONK)
        self.assertEqual(status,200)
        self.assertEqual(data['trades'],[])
        self.assertTrue(data['coverage']['gaps'])
        self.assertIsNone(data['coverage']['nextBefore'])
        self.assertTrue(data['coverage']['nextCursor'])

    def test_default_transport_shares_budget_and_deadline(self):
        from unittest.mock import patch
        import app
        self.assertTrue(hasattr(self.server,'holders'))
        with patch.object(app,'UPSTREAM_LIMIT',app.Limiter(0)), patch.object(app,'build_opener') as opener:
            result=self.server.holders.top_holders(MINT)
            self.assertEqual(result['status'],'unavailable')
            self.assertTrue(all('budget' in e['message'] for e in result['errors']))
            opener.assert_not_called()
        with patch.object(app.time,'monotonic',side_effect=[0,21,21,21]), patch.object(app,'build_opener') as opener:
            result=self.server.holders.top_holders(MINT)
            self.assertEqual(result['status'],'unavailable')
            self.assertTrue(all('deadline' in e['message'] for e in result['errors']))
            opener.assert_not_called()


if __name__=='__main__':unittest.main()

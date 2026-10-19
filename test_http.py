"""Offline HTTP/security tests. Provider data here is synthetic and isolated."""
import unittest
import tempfile
import threading
import urllib.request
import urllib.error
from pathlib import Path
import json
import app

class HTTPTests(unittest.TestCase):
    def test_api_validation_cache_and_route_isolation(self):
        self.assertTrue(hasattr(app, 'make_server'), 'HTTP backend missing')
        with tempfile.TemporaryDirectory() as directory:
            calls=[]
            def fake(url, body=None):
                calls.append(url)
                return []
            server=app.make_server('127.0.0.1',0,Path(directory),app.Sources(fake))
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            base='http://127.0.0.1:'+str(server.server_port)
            try:
                def get(path):
                    try:
                        with urllib.request.urlopen(base+path) as r:return r.status,json.load(r)
                    except urllib.error.HTTPError as e:return e.code,json.load(e)
                self.assertEqual(get('/health')[0],200)
                for path in ['/app.py','/../app.py','/%2e%2e/app.py','/data/cache.sqlite','/.env','/api/token?address=http://localhost']:
                    self.assertIn(get(path)[0],[400,404])
                address='So11111111111111111111111111111111111111112'
                url='/api/token?address='+address
                first=get(url); second=get(url)
                self.assertEqual(first[1]['status'],'empty')
                self.assertFalse(first[1]['cache']['hit'])
                self.assertTrue(second[1]['cache']['hit'])
                self.assertEqual(len(calls),1)
                self.assertEqual(get(url+'&address='+address)[0],400)
                self.assertEqual(get(url+'&url=https://example.org')[0],400)
                req=urllib.request.Request(base+'/api/token',data=b'{}',method='POST')
                with self.assertRaises(urllib.error.HTTPError) as c: urllib.request.urlopen(req)
                self.assertEqual(c.exception.code,405)
                with self.assertRaises(ValueError): app.Cache(Path(__file__).parent/'static')
                with self.assertRaises(app.ProviderError):app.fetch_json('http://127.0.0.1/private')
            finally:
                server.shutdown(); server.server_close();worker.join()

if __name__=='__main__':unittest.main()

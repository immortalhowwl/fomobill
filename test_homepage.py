"""Static route, binary media and unchanged terminal regression."""
import tempfile, threading, unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import app

class HomepageTests(unittest.TestCase):
    def test_routes_binary_ranges_and_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            server=app.make_server('127.0.0.1',0,Path(directory))
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            base='http://127.0.0.1:'+str(server.server_port)
            def get(path,method='GET',headers=None):
                try:
                    with urlopen(Request(base+path,method=method,headers=headers or {})) as r:return r.status,r.headers,r.read()
                except HTTPError as e:return e.code,e.headers,e.read()
            try:
                for route,(file,mime) in app.ROUTES.items():
                    status,headers,body=get(route)
                    self.assertEqual(status,200,route);self.assertEqual(body,(app.STATIC/file).read_bytes(),route)
                    self.assertEqual(headers['Content-Type'],mime)
                for route in ['/legacy','/legacy/']:
                    self.assertEqual(get(route)[2],(app.STATIC/'index.html').read_bytes())
                # Assert the redirect itself: urllib's 308 handling varies by Python version.
                from http.client import HTTPConnection
                connection=HTTPConnection('127.0.0.1',server.server_port)
                try:
                    connection.request('GET','/workspace')
                    response=connection.getresponse()
                    self.assertEqual(response.status,308)
                    self.assertEqual(response.getheader('Location'),'/workspace/')
                    self.assertEqual(response.read(),b'')
                finally:connection.close()
                self.assertIn(b'workspace-core.js',get('/workspace/')[2])
                self.assertIn(b'preview: false',get('/workspace/runtime-config.js')[2])
                home=get('/')[2].decode()
                self.assertNotIn('id="motion-toggle"',home)
                for anchor in ['tokens','wallets','alerts','lookup','sourceLimits']:
                    self.assertIn('/workspace/#'+anchor,home)
                self.assertIn("media-src 'self'",get('/')[1]['Content-Security-Policy'])
                self.assertIn("font-src 'self'",get('/')[1]['Content-Security-Policy'])
                media='/fomobill-home/assets/inferno-loop.mp4';data=get(media)[2]
                for value,start,end in [('bytes=0-31',0,31),('bytes=-32',len(data)-32,len(data)-1),('bytes=100-',100,len(data)-1)]:
                    status,headers,body=get(media,headers={'Range':value});self.assertEqual(status,206);self.assertEqual(body,data[start:end+1]);self.assertEqual(headers['Content-Range'],f'bytes {start}-{end}/{len(data)}')
                for value in ['bytes=999999999-','bytes=10-2','bytes=-0','bytes=0-1,2-3','nonsense']:
                    self.assertEqual(get(media,headers={'Range':value})[0],416)
                status,headers,body=get(media,'HEAD');self.assertEqual(status,200);self.assertEqual(body,b'');self.assertEqual(int(headers['Content-Length']),len(data))
                for route in ['/fomobill-home/../app.py','/fomobill-home/%2e%2e/app.py','/fomobill-home/PACKAGE.json','/workspace/app.js','/owner.key']:
                    self.assertEqual(get(route)[0],404)
            finally:server.shutdown();server.server_close();worker.join()

import base64
import threading
import unittest
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scanbridge import config, destinations
from scanbridge.config import DestinationConfig
from scanbridge.destinations.webdav import WebdavDestination


class FakeWebdav(ThreadingHTTPServer):
    """A WebDAV server with one folder that answers HEAD and PUT, honouring If-None-Match: * unless told not to."""

    daemon_threads = True

    def __init__(self, folder='/dav/Scans', auth=None, status=None, honour_if_none_match=True):
        super().__init__(('127.0.0.1', 0), _Handler)
        self.honour_if_none_match = honour_if_none_match
        self.folder = folder
        self.auth = auth  # (username, password) required, or None
        self.status = status  # answer every PUT with this status
        self.files = {}  # unquoted path -> bytes
        self.requests = []  # (raw path, headers)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server_address[1]}{self.folder}'


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, status):
        self.send_response(status)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _authorized(self):
        auth = self.server.auth
        expected = 'Basic ' + base64.b64encode(':'.join(auth).encode()).decode() if auth else None
        return not auth or self.headers.get('Authorization') == expected

    def do_HEAD(self):
        if not self._authorized():
            return self._reply(401)
        self._reply(200 if urllib.parse.unquote(self.path) in self.server.files else 404)

    def do_PUT(self):
        server = self.server
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        server.requests.append((self.path, dict(self.headers)))
        path = urllib.parse.unquote(self.path)
        if not self._authorized():
            return self._reply(401)
        if server.status:
            return self._reply(server.status)
        if path.rpartition('/')[0] != server.folder:
            return self._reply(404)
        if path in server.files:
            if self.headers.get('If-None-Match') == '*' and server.honour_if_none_match:
                return self._reply(412)
            server.files[path] = body
            return self._reply(204)
        server.files[path] = body
        self._reply(201)


class WebdavTest(unittest.TestCase):
    def serve(self, **kwargs):
        server = FakeWebdav(**kwargs)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(5)

        self.addCleanup(stop)
        return server

    def test_uploads_file(self):
        server = self.serve()
        where = WebdavDestination('dav', url=server.url, username='', password='').store('scan.pdf', b'PDF')
        self.assertEqual(where, server.url + '/scan.pdf')
        self.assertEqual(server.files, {'/dav/Scans/scan.pdf': b'PDF'})
        self.assertEqual(server.requests[0][1]['If-None-Match'], '*')

    def test_trailing_slash_of_url_is_fine(self):
        server = self.serve()
        WebdavDestination('dav', url=server.url + '/', username='', password='').store('scan.pdf', b'PDF')
        self.assertEqual(server.requests[0][0], '/dav/Scans/scan.pdf')

    def test_existing_file_is_never_overwritten(self):
        server = self.serve()
        server.files['/dav/Scans/scan.pdf'] = b'old'
        dest = WebdavDestination('dav', url=server.url, username='', password='')
        self.assertEqual(dest.store('scan.pdf', b'new'), server.url + '/scan%20%281%29.pdf')
        self.assertEqual(dest.store('scan.pdf', b'newer'), server.url + '/scan%20%282%29.pdf')
        self.assertEqual(server.files, {'/dav/Scans/scan.pdf': b'old', '/dav/Scans/scan (1).pdf': b'new',
                                        '/dav/Scans/scan (2).pdf': b'newer'})

    def test_file_name_is_quoted(self):
        server = self.serve()
        WebdavDestination('dav', url=server.url, username='', password='').store('Scan 2026 #1 é.pdf', b'x')
        self.assertEqual(server.requests[0][0], '/dav/Scans/Scan%202026%20%231%20%C3%A9.pdf')
        self.assertEqual(list(server.files), ['/dav/Scans/Scan 2026 #1 é.pdf'])

    def test_basic_auth(self):
        server = self.serve(auth=('alice', 'app pass'))
        WebdavDestination('dav', url=server.url, username='alice', password='app pass').store('a.pdf', b'x')
        expected = 'Basic ' + base64.b64encode(b'alice:app pass').decode()
        self.assertEqual(server.requests[0][1]['Authorization'], expected)
        self.assertEqual(list(server.files), ['/dav/Scans/a.pdf'])

    def test_no_username_sends_no_credentials(self):
        server = self.serve()
        WebdavDestination('dav', url=server.url, username='', password='ignored').store('a.pdf', b'x')
        self.assertNotIn('Authorization', server.requests[0][1])

    def test_wrong_or_missing_credentials_raise(self):
        server = self.serve(auth=('alice', 'right'))
        for username, password in (('alice', 'wrong'), ('', '')):
            with self.subTest(username=username):
                dest = WebdavDestination('dav', url=server.url, username=username, password=password)
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    dest.store('a.pdf', b'x')
                self.assertEqual(cm.exception.code, 401)
        self.assertEqual(server.files, {})

    def test_server_ignoring_if_none_match_does_not_overwrite(self):
        server = self.serve(honour_if_none_match=False)
        server.files['/dav/Scans/scan.pdf'] = b'old'
        WebdavDestination('dav', url=server.url, username='', password='').store('scan.pdf', b'new')
        self.assertEqual(server.files, {'/dav/Scans/scan.pdf': b'old', '/dav/Scans/scan (1).pdf': b'new'})

    def test_missing_folder_raises_with_hint(self):
        server = self.serve()
        dest = WebdavDestination('dav', url=server.url + 'Missing', username='', password='')
        with self.assertRaisesRegex(OSError, 'folder .* probably does not exist'):
            dest.store('a.pdf', b'x')

    def test_conflict_status_means_missing_folder(self):
        server = self.serve(status=409)  # what WebDAV answers when the parent collection is missing
        with self.assertRaisesRegex(OSError, 'probably does not exist'):
            WebdavDestination('dav', url=server.url, username='', password='').store('a.pdf', b'x')

    def test_server_error_raises(self):
        server = self.serve(status=500)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            WebdavDestination('dav', url=server.url, username='', password='').store('a.pdf', b'x')
        self.assertEqual(cm.exception.code, 500)

    def test_unexpected_success_status_raises(self):
        server = self.serve(status=202)
        with self.assertRaisesRegex(OSError, 'unexpected HTTP status 202'):
            WebdavDestination('dav', url=server.url, username='', password='').store('a.pdf', b'x')

    def test_gives_up_when_every_name_is_taken(self):
        server = self.serve(status=412)
        with self.assertRaisesRegex(OSError, 'No free file name'):
            WebdavDestination('dav', url=server.url, username='', password='').store('a.pdf', b'x')
        self.assertEqual(len(server.requests), 100)

    def test_unreachable_server_raises(self):
        server = FakeWebdav()
        url = server.url
        server.server_close()
        with self.assertRaises(OSError):
            WebdavDestination('dav', url=url, username='', password='').store('a.pdf', b'x')

    def test_secret_and_insecure(self):
        self.assertEqual(WebdavDestination.SECRETS, {'password'})
        self.assertTrue(WebdavDestination.insecure({'url': 'http://cloud.example.com/dav'}))
        self.assertFalse(WebdavDestination.insecure({'url': 'https://cloud.example.com/dav'}))
        self.assertFalse(WebdavDestination.insecure({'url': 'http://127.0.0.1:8080/dav'}))


class WebdavConfigTest(unittest.TestCase):
    ENV = {'SCANBRIDGE_PRINTER_HOST': '192.0.2.1', 'SCANBRIDGE_WEBDAV_URL': 'https://cloud.example.com/dav/Scans'}

    def test_from_environment(self):
        cfg = config.load(environ={**self.ENV, 'SCANBRIDGE_WEBDAV_USERNAME': 'alice',
                                   'SCANBRIDGE_WEBDAV_PASSWORD': 'hunter2-app-password'})
        dc = cfg.destinations['webdav']
        self.assertEqual(dc.type, 'webdav')
        self.assertEqual(dc.options, {'url': 'https://cloud.example.com/dav/Scans', 'username': 'alice',
                                      'password': 'hunter2-app-password'})
        self.assertIsInstance(destinations.create(dc), WebdavDestination)
        text = '\n'.join(cfg.describe())
        self.assertIn("username='alice'", text)
        self.assertRegex(text, r'password=.?\*\*\*')
        self.assertNotIn('hunter2', text)

    def test_defaults(self):
        dc = config.load(environ=self.ENV).destinations['webdav']
        self.assertEqual((dc.options['username'], dc.options['password']), ('', ''))

    def test_url_is_required(self):
        with self.assertRaisesRegex(config.ConfigError, 'url is required'):
            config.load(environ={'SCANBRIDGE_PRINTER_HOST': 'h', 'SCANBRIDGE_WEBDAV_USERNAME': 'alice'})

    def test_create_from_config(self):
        dest = destinations.create(DestinationConfig('nc', 'webdav', {'url': 'https://x', 'username': '',
                                                                      'password': ''}))
        self.assertIsInstance(dest, WebdavDestination)
        self.assertEqual(str(dest), 'nc (webdav)')


if __name__ == '__main__':
    unittest.main()

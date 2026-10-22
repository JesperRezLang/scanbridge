import email.parser
import email.policy
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scanbridge import config, destinations
from scanbridge.destinations.paperless import PaperlessDestination

TASK = '0f3c9a52-7d1e-4c58-9a43-2b6d8e1f7a90'


def parse_multipart(content_type, body):
    """[(field name, file name or None, bytes)] in the order of the parts."""
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b'Content-Type: ' + content_type.encode() + b'\r\nMIME-Version: 1.0\r\n\r\n' + body)
    return [(p.get_param('name', header='content-disposition'), p.get_filename(), p.get_payload(decode=True))
            for p in msg.iter_parts()]


class FakePaperless(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, token='secret-token', status=200):
        super().__init__(('127.0.0.1', 0), _Handler)
        self.token = token
        self.status = status
        self.requests = []  # (method, path, headers, parsed parts)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server_address[1]}'


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        parts = parse_multipart(self.headers['Content-Type'], body)
        self.server.requests.append(('POST', self.path, dict(self.headers), parts))
        if self.path != '/api/documents/post_document/':
            self._reply(404, {'detail': 'Not found.'})
        elif self.headers.get('Authorization') != f'Token {self.server.token}':
            self._reply(401, {'detail': 'Invalid token.'})
        elif self.server.status != 200:
            self._reply(self.server.status, {'detail': 'error'})
        else:
            self._reply(200, TASK)


class PaperlessTest(unittest.TestCase):
    def serve(self, **kwargs):
        server = FakePaperless(**kwargs)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(5)

        self.addCleanup(stop)
        return server

    def dest(self, server, **options):
        return PaperlessDestination('paperless', url=server.url, token='secret-token',
                                    **{'tags': '', 'correspondent': '', 'document_type': '', **options})

    def test_uploads_document(self):
        server = self.serve()
        where = self.dest(server).store('Scan 2026-01-02.pdf', b'%PDF data')
        self.assertEqual(where, f'task {TASK}')
        (method, path, headers, parts), = server.requests
        self.assertEqual((method, path), ('POST', '/api/documents/post_document/'))
        self.assertEqual(headers['Authorization'], 'Token secret-token')
        self.assertTrue(headers['Content-Type'].startswith('multipart/form-data; boundary='))
        self.assertEqual(parts, [('title', None, b'Scan 2026-01-02'),
                                 ('document', 'Scan 2026-01-02.pdf', b'%PDF data')])

    def test_binary_data_is_sent_unchanged(self):
        server = self.serve()
        data = bytes(range(256)) * 40 + b'\r\n--not-a-boundary\r\n'
        self.dest(server).store('x.pdf', data)
        self.assertEqual(server.requests[0][3][-1], ('document', 'x.pdf', data))

    def test_tags_correspondent_and_document_type(self):
        server = self.serve()
        self.dest(server, tags='3, 7,12', correspondent='5', document_type=' 2 ').store('a.pdf', b'x')
        fields = [(name, value) for name, _, value in server.requests[0][3] if name != 'document']
        self.assertEqual(fields, [('title', b'a'), ('tags', b'3'), ('tags', b'7'), ('tags', b'12'),
                                  ('correspondent', b'5'), ('document_type', b'2')])

    def test_trailing_slash_and_odd_file_names(self):
        server = self.serve()
        PaperlessDestination('p', url=server.url + '/', token='secret-token', tags='', correspondent='',
                             document_type='').store('Receipt "é".v2.jpg', b'x')
        parts = server.requests[0][3]
        self.assertEqual(parts[0], ('title', None, 'Receipt "é".v2'.encode()))
        self.assertEqual(parts[1][1], 'Receipt %22é%22.v2.jpg')

    def test_http_errors_raise(self):
        for status in (400, 500):
            with self.subTest(status=status):
                server = self.serve(status=status)
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    self.dest(server).store('a.pdf', b'x')
                self.assertEqual(cm.exception.code, status)

    def test_wrong_token_raises(self):
        server = self.serve()
        dest = PaperlessDestination('p', url=server.url, token='wrong', tags='', correspondent='', document_type='')
        with self.assertRaises(urllib.error.HTTPError) as cm:
            dest.store('a.pdf', b'x')
        self.assertEqual(cm.exception.code, 401)

    def test_unreachable_server_raises(self):
        server = FakePaperless()
        url = server.url
        server.server_close()
        with self.assertRaises(OSError):
            PaperlessDestination('p', url=url, token='t', tags='', correspondent='', document_type='').store('a', b'x')

    def test_invalid_ids_are_a_config_error(self):
        env = {'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_PAPERLESS_URL': 'https://paperless.example.com',
               'SCANBRIDGE_PAPERLESS_TOKEN': 't'}
        for key, value in (('TAGS', '1,x'), ('TAGS', 'inbox'), ('CORRESPONDENT', 'bob'), ('DOCUMENT_TYPE', '1.5'),
                           ('CORRESPONDENT', '1,2'), ('TAGS', '-1')):
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(config.ConfigError, r'destinations\.paperless: .*ID'):
                    config.load({**env, f'SCANBRIDGE_PAPERLESS_{key}': value})

    def test_validate(self):
        PaperlessDestination.validate({'tags': '1,2', 'correspondent': '3', 'document_type': ''})
        with self.assertRaisesRegex(ValueError, "tags: 'x' is not a numeric ID"):
            PaperlessDestination.validate({'tags': '1,x', 'correspondent': '', 'document_type': ''})

    def test_secret_and_insecure(self):
        self.assertEqual(PaperlessDestination.SECRETS, {'token'})
        self.assertTrue(PaperlessDestination.insecure({'url': 'http://paperless.example.com'}))
        self.assertFalse(PaperlessDestination.insecure({'url': 'https://paperless.example.com'}))


class PaperlessConfigTest(unittest.TestCase):
    ENV = {'SCANBRIDGE_PRINTER_HOST': '192.0.2.1', 'SCANBRIDGE_PAPERLESS_URL': 'https://paperless.example.com',
           'SCANBRIDGE_PAPERLESS_TOKEN': 'abcdef0123456789'}

    def test_from_environment(self):
        cfg = config.load(environ={**self.ENV, 'SCANBRIDGE_PAPERLESS_TAGS': '1,2',
                                   'SCANBRIDGE_PAPERLESS_CORRESPONDENT': '4'})
        dc = cfg.destinations['paperless']
        self.assertEqual(dc.type, 'paperless')
        self.assertEqual(dc.options, {'url': 'https://paperless.example.com', 'token': 'abcdef0123456789',
                                      'tags': '1,2', 'correspondent': '4', 'document_type': ''})
        self.assertIsInstance(destinations.create(dc), PaperlessDestination)
        text = '\n'.join(cfg.describe())
        self.assertRegex(text, r'token=.?\*\*\*')
        self.assertNotIn('abcdef0123456789', text)

    def test_token_is_required(self):
        env = {k: v for k, v in self.ENV.items() if not k.endswith('TOKEN')}
        with self.assertRaisesRegex(config.ConfigError, 'token is required'):
            config.load(environ=env)

    def test_ids_in_toml_are_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'c.toml')
            for value, ok in (('"1,2"', True), ('3', False)):
                with open(path, 'w') as f:
                    f.write('[[printers]]\nhost = "h"\n[destinations.paperless]\n'
                            f'url = "https://p"\ntoken = "t"\ntags = {value}\n')
                with self.subTest(value=value):
                    if ok:
                        self.assertEqual(config.load(environ={}, path=path).destinations['paperless'].options['tags'],
                                         '1,2')
                    else:
                        with self.assertRaisesRegex(config.ConfigError, 'tags: expected text'):
                            config.load(environ={}, path=path)


if __name__ == '__main__':
    unittest.main()

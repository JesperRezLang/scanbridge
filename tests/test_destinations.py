import json
import os
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scanbridge import destinations
from scanbridge.config import DestinationConfig
from scanbridge.destinations.local import LocalDestination, unique_path, write_atomic
from scanbridge.destinations.seafile import SeafileDestination, upload


class TempDirCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name


class LocalDestinationTest(TempDirCase):
    def test_stores_file(self):
        dest = LocalDestination('local', path=self.tmp)
        where = dest.store('scan.pdf', b'%PDF data')
        self.assertEqual(where, os.path.join(self.tmp, 'scan.pdf'))
        with open(where, 'rb') as f:
            self.assertEqual(f.read(), b'%PDF data')

    def test_creates_missing_folder(self):
        folder = os.path.join(self.tmp, 'a', 'b')
        where = LocalDestination('local', path=folder).store('x.pdf', b'1')
        self.assertTrue(os.path.isfile(where))
        self.assertEqual(os.path.dirname(where), folder)

    def test_collision_gets_a_number(self):
        dest = LocalDestination('local', path=self.tmp)
        first = dest.store('scan.pdf', b'1')
        second = dest.store('scan.pdf', b'2')
        third = dest.store('scan.pdf', b'3')
        self.assertEqual(os.path.basename(first), 'scan.pdf')
        self.assertEqual(os.path.basename(second), 'scan (1).pdf')
        self.assertEqual(os.path.basename(third), 'scan (2).pdf')
        contents = {}
        for name in os.listdir(self.tmp):
            with open(os.path.join(self.tmp, name), 'rb') as f:
                contents[name] = f.read()
        self.assertEqual(contents, {'scan.pdf': b'1', 'scan (1).pdf': b'2', 'scan (2).pdf': b'3'})

    def test_no_part_file_left_behind(self):
        dest = LocalDestination('local', path=self.tmp)
        dest.store('scan.pdf', b'1')
        dest.store('scan.pdf', b'2')
        self.assertFalse([n for n in os.listdir(self.tmp) if n.endswith('.part')])

    def test_str_and_options(self):
        dest = LocalDestination('docs', path=self.tmp)
        self.assertEqual(str(dest), 'docs (local)')
        self.assertEqual(dest.options, {'path': self.tmp})

    def test_unique_path_without_extension(self):
        write_atomic(os.path.join(self.tmp, 'noext'), b'')
        self.assertEqual(unique_path(self.tmp, 'noext'), os.path.join(self.tmp, 'noext (1)'))

    def test_unique_path_free_name_is_unchanged(self):
        self.assertEqual(unique_path(self.tmp, 'a.pdf'), os.path.join(self.tmp, 'a.pdf'))

    def test_write_atomic_replaces_part_file(self):
        path = os.path.join(self.tmp, 'f.bin')
        write_atomic(path, b'abc')
        self.assertEqual(sorted(os.listdir(self.tmp)), ['f.bin'])


class FactoryTest(unittest.TestCase):
    def test_types(self):
        self.assertEqual(set(destinations.TYPES), {'local', 'seafile', 'webdav', 'paperless', 'ftp'})

    def test_create_local(self):
        dest = destinations.create(DestinationConfig('nas', 'local', {'path': '/x'}))
        self.assertIsInstance(dest, LocalDestination)
        self.assertEqual((dest.name, dest.options), ('nas', {'path': '/x'}))

    def test_create_seafile(self):
        opts = {'url': 'https://sf', 'token': 't', 'directory': '/'}
        dest = destinations.create(DestinationConfig('sf', 'seafile', opts))
        self.assertIsInstance(dest, SeafileDestination)
        self.assertEqual(dest.options, opts)

    def test_seafile_token_is_a_secret(self):
        self.assertIn('token', SeafileDestination.SECRETS)


class FakeSeafile(ThreadingHTTPServer):
    """Answers the two Seafile requests and records what it received."""

    daemon_threads = True

    def __init__(self, link='https://seafile.invalid/seafhttp/upload-api/abc', fail_link=False, fail_upload=False,
                 result=None):
        super().__init__(('127.0.0.1', 0), _Handler)
        self.link = link
        self.fail_link = fail_link
        self.fail_upload = fail_upload
        self.result = [{'name': 'x.pdf', 'id': '1', 'size': 3}] if result is None else result
        self.requests = []  # (method, path, headers, body)

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

    def do_GET(self):
        self.server.requests.append(('GET', self.path, dict(self.headers), b''))
        if self.server.fail_link or not self.path.startswith('/api/v2.1/via-repo-token/upload-link/?path='):
            self._reply(500, 'error')
        else:
            self._reply(200, self.server.link)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.server.requests.append(('POST', self.path, dict(self.headers), body))
        if self.server.fail_upload or not self.path.startswith('/seafhttp/upload-api/abc?'):
            self._reply(500, 'error')
        else:
            self._reply(200, self.server.result)


class SeafileTest(unittest.TestCase):
    def serve(self, **kwargs):
        server = FakeSeafile(**kwargs)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(5)

        self.addCleanup(stop)
        return server

    def test_store_uploads_and_returns_path(self):
        server = self.serve()
        dest = SeafileDestination('sf', url=server.url, token='secret-token', directory='/')
        where = dest.store('x.pdf', b'PDF')
        self.assertEqual(where, '/x.pdf')

        (get, post) = server.requests
        self.assertEqual(get[0], 'GET')
        self.assertEqual(get[1], '/api/v2.1/via-repo-token/upload-link/?path=%2F')
        self.assertEqual(get[2]['Authorization'], 'Token secret-token')
        # The link names an unresolvable host: the upload must go to the configured URL instead.
        self.assertEqual(post[0], 'POST')
        self.assertEqual(post[1], '/seafhttp/upload-api/abc?ret-json=1')
        self.assertEqual(post[2]['Authorization'], 'Token secret-token')
        self.assertTrue(post[2]['Content-Type'].startswith('multipart/form-data; boundary='))
        self.assertIn(b'name="parent_dir"\r\n\r\n/\r\n', post[3])
        self.assertIn(b'name="file"; filename="x.pdf"', post[3])
        self.assertIn(b'\r\n\r\nPDF\r\n', post[3])

    def test_boundary_in_body_matches_header(self):
        server = self.serve()
        SeafileDestination('sf', url=server.url, token='t', directory='/').store('x.pdf', b'PDF')
        post = server.requests[1]
        boundary = post[2]['Content-Type'].split('boundary=')[1]
        self.assertTrue(post[3].startswith(f'--{boundary}\r\n'.encode()))
        self.assertTrue(post[3].endswith(f'--{boundary}--\r\n'.encode()))

    def test_binary_data_is_sent_unchanged(self):
        server = self.serve()
        data = bytes(range(256)) * 20
        SeafileDestination('sf', url=server.url, token='t', directory='/').store('x.pdf', data)
        self.assertIn(b'\r\n\r\n' + data + b'\r\n', server.requests[1][3])

    def test_directory_is_used_for_link_and_return_value(self):
        server = self.serve(result=[{'name': 'x (1).pdf', 'id': '2', 'size': 3}])
        dest = SeafileDestination('sf', url=server.url + '/', token='t', directory='/Scans/2026/')
        where = dest.store('x.pdf', b'PDF')
        self.assertEqual(where, '/Scans/2026/x (1).pdf')  # Seafile renamed the file; trailing slash of url is fine
        self.assertIn('path=%2FScans%2F2026%2F', server.requests[0][1])
        self.assertIn(b'name="parent_dir"\r\n\r\n/Scans/2026/\r\n', server.requests[1][3])

    def test_empty_result_falls_back_to_given_name(self):
        server = self.serve(result=[])
        where = SeafileDestination('sf', url=server.url, token='t', directory='/d').store('x.pdf', b'PDF')
        self.assertEqual(where, '/d/x.pdf')

    def test_link_with_query_string_gets_ret_json_appended(self):
        server = self.serve(link='https://seafile.invalid/seafhttp/upload-api/abc?x=1')
        upload(server.url, 't', 'x.pdf', b'PDF')
        self.assertEqual(server.requests[1][1], '/seafhttp/upload-api/abc?x=1&ret-json=1')

    def test_upload_returns_server_json(self):
        server = self.serve()
        self.assertEqual(upload(server.url, 't', 'x.pdf', b'PDF'), [{'name': 'x.pdf', 'id': '1', 'size': 3}])

    def test_upload_link_failure_raises(self):
        server = self.serve(fail_link=True)
        dest = SeafileDestination('sf', url=server.url, token='t', directory='/')
        with self.assertRaises(urllib.error.HTTPError) as cm:
            dest.store('x.pdf', b'PDF')
        self.assertEqual(cm.exception.code, 500)
        self.assertEqual(len(server.requests), 1)

    def test_upload_failure_raises(self):
        server = self.serve(fail_upload=True)
        dest = SeafileDestination('sf', url=server.url, token='t', directory='/')
        with self.assertRaises(urllib.error.HTTPError) as cm:
            dest.store('x.pdf', b'PDF')
        self.assertEqual(cm.exception.code, 500)

    def test_unreachable_server_raises(self):
        server = FakeSeafile()
        url = server.url
        server.server_close()  # nothing listens on this port any more
        with self.assertRaises(OSError):
            SeafileDestination('sf', url=url, token='t', directory='/').store('x.pdf', b'PDF')


if __name__ == '__main__':
    unittest.main()

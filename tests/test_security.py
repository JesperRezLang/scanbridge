import logging
import os
import stat
import tempfile
import unittest

from scanbridge import config
from scanbridge.delivery import Delivery
from scanbridge.destinations.base import Destination
from scanbridge.destinations.local import LocalDestination

POSIX = os.name == 'posix'


class DownDestination(Destination):
    type = 'fake'

    def store(self, filename, data):
        raise OSError('destination is down')


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@unittest.skipUnless(POSIX, 'needs POSIX permissions')
class QueuePermissionsTest(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.queue = os.path.join(tmp.name, 'data', 'queue')
        old = os.umask(0o022)
        self.addCleanup(os.umask, old)

    def test_failed_delivery_is_queued_privately(self):
        Delivery({'fake': DownDestination('fake')}, self.queue).deliver(['fake'], 'a.pdf', b'data')
        self.assertEqual(mode(self.queue), 0o700)
        self.assertEqual(mode(os.path.join(self.queue, 'fake')), 0o700)
        self.assertEqual(mode(os.path.join(self.queue, 'fake', 'a.pdf')), 0o600)
        self.assertEqual(os.listdir(os.path.join(self.queue, 'fake')), ['a.pdf'])  # no .part left behind

    def test_existing_loose_folders_are_tightened(self):
        folder = os.path.join(self.queue, 'fake')
        os.makedirs(folder, mode=0o755)
        os.chmod(self.queue, 0o755)
        with open(os.path.join(folder, 'b.pdf.part'), 'wb') as f:  # left over from a crash, with loose rights
            f.write(b'old')
        os.chmod(os.path.join(folder, 'b.pdf.part'), 0o644)
        Delivery({'fake': DownDestination('fake')}, self.queue).enqueue('fake', 'b.pdf', b'new')
        self.assertEqual(mode(self.queue), 0o700)
        self.assertEqual(mode(folder), 0o700)
        path = os.path.join(folder, 'b.pdf')
        self.assertEqual(mode(path), 0o600)
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), b'new')

    def test_local_destination_keeps_normal_permissions(self):
        folder = os.path.join(self.tmp, 'scans')
        path = LocalDestination('local', path=folder).store('c.pdf', b'data')
        self.assertEqual(mode(path), 0o644)
        self.assertEqual(mode(folder), 0o755)


class InsecureDestinationsTest(unittest.TestCase):
    def flagged(self, url):
        cfg = config.load(environ={'SCANBRIDGE_PRINTER_HOST': '192.0.2.1', 'SCANBRIDGE_SEAFILE_URL': url,
                                   'SCANBRIDGE_SEAFILE_TOKEN': 'x'})
        return config.insecure_destinations(cfg)

    def test_http_to_other_hosts_is_flagged(self):
        for url in ('http://seafile.example.com', 'http://192.0.2.5:8082', 'HTTP://Seafile.example.com/'):
            with self.subTest(url=url):
                self.assertEqual(self.flagged(url), ['seafile'])

    def test_https_and_loopback_are_not_flagged(self):
        for url in ('https://seafile.example.com', 'https://192.0.2.5:8082', 'http://localhost:8082',
                    'http://127.0.0.1', 'http://127.1.2.3:80', 'http://[::1]:8000'):
            with self.subTest(url=url):
                self.assertEqual(self.flagged(url), [])

    def test_destinations_without_url_are_ignored(self):
        cfg = config.load(environ={'SCANBRIDGE_PRINTER_HOST': '192.0.2.1', 'SCANBRIDGE_LOCAL_PATH': '/scans'})
        self.assertEqual(config.insecure_destinations(cfg), [])

    def test_malformed_url_does_not_fail(self):
        self.assertEqual(self.flagged('http://[::1'), [])


if __name__ == '__main__':
    unittest.main()

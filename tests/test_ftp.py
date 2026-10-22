import ftplib
import os
import socket
import socketserver
import tempfile
import threading
import unittest

from scanbridge import config, destinations
from scanbridge.destinations.ftp import FtpDestination


class FakeFtp(socketserver.ThreadingTCPServer):
    """A minimal plain FTP server (passive mode only) serving a folder; `log` lists the commands received."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, root, username='scanner', password='pw'):
        super().__init__(('127.0.0.1', 0), _Session)
        self.root, self.username, self.password = root, username, password
        self.log = []

    @property
    def port(self):
        return self.server_address[1]


class _Session(socketserver.StreamRequestHandler):
    def reply(self, line):
        self.wfile.write(line.encode() + b'\r\n')

    def path(self, name=''):
        parts = (self.cwd.rstrip('/') + '/' + name).split('/') if not name.startswith('/') else name.split('/')
        return os.path.join(self.server.root, *[p for p in parts if p])

    def data_connection(self):
        if self.active:
            conn, self.active = socket.create_connection(self.active), None
            return conn
        with self.listener:
            conn, _ = self.listener.accept()
        self.listener = None
        return conn

    def handle(self):
        self.cwd, self.listener, self.active, self.user, self.rnfr = '/', None, None, None, None
        self.reply('220 fake ftp')
        try:
            while line := self.rfile.readline():
                cmd, _, arg = line.decode().rstrip('\r\n').partition(' ')
                cmd = cmd.upper()
                self.server.log.append(cmd)
                if cmd == 'QUIT':
                    return self.reply('221 bye')
                getattr(self, 'do_' + cmd, self.unknown)(arg)
        finally:
            if self.listener:
                self.listener.close()

    def unknown(self, arg):
        self.reply('502 not implemented')

    def do_USER(self, arg):
        self.user = arg
        self.reply('331 password please')

    def do_PASS(self, arg):
        ok = (self.user, arg) == (self.server.username, self.server.password)
        self.reply('230 logged in' if ok else '530 login incorrect')

    def do_TYPE(self, arg):
        self.reply('200 type set')

    def do_PWD(self, arg):
        self.reply(f'257 "{self.cwd}"')

    def do_CWD(self, arg):
        if os.path.isdir(self.path(arg)):
            self.cwd = '/' + os.path.relpath(self.path(arg), self.server.root).strip('.')
            self.reply('250 ok')
        else:
            self.reply('550 no such directory')

    def do_PASV(self, arg):
        if self.listener:
            self.listener.close()
        self.listener = socket.create_server(('127.0.0.1', 0))
        port = self.listener.getsockname()[1]
        self.reply(f'227 Entering Passive Mode (127,0,0,1,{port >> 8},{port & 255})')

    def do_PORT(self, arg):
        *host, hi, lo = arg.split(',')
        self.active = ('.'.join(host), int(hi) * 256 + int(lo))
        self.reply('200 port ok')

    def do_NLST(self, arg):
        names = sorted(os.listdir(self.path()))
        if not names:
            return self.reply('550 No files found')
        self.reply('150 listing')
        with self.data_connection() as conn:
            conn.sendall(''.join(n + '\r\n' for n in names).encode())
        self.reply('226 done')

    def do_STOR(self, arg):
        self.reply('150 send it')
        with self.data_connection() as conn, open(self.path(arg), 'wb') as f:
            while chunk := conn.recv(65536):
                f.write(chunk)
        self.reply('226 stored')

    def do_RNFR(self, arg):
        self.rnfr = arg
        self.reply('350 ready for RNTO')

    def do_RNTO(self, arg):
        os.replace(self.path(self.rnfr), self.path(arg))
        self.reply('250 renamed')

    def do_DELE(self, arg):
        os.remove(self.path(arg))
        self.reply('250 deleted')


class FtpTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.server = FakeFtp(self.root)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(5)

        self.addCleanup(stop)

    def dest(self, **options):
        return FtpDestination('ftp', **{'host': '127.0.0.1', 'port': self.server.port, 'username': 'scanner',
                                        'password': 'pw', 'directory': '/', 'tls': False, 'passive': True, **options})

    def read(self, *path):
        with open(os.path.join(self.root, *path), 'rb') as f:
            return f.read()

    def test_uploads_with_final_name_and_no_part_file(self):
        where = self.dest().store('scan.pdf', b'%PDF data')
        self.assertEqual(where, 'ftp://127.0.0.1/scan.pdf')
        self.assertEqual(os.listdir(self.root), ['scan.pdf'])
        self.assertEqual(self.read('scan.pdf'), b'%PDF data')
        cmds = self.server.log
        self.assertLess(cmds.index('STOR'), cmds.index('RNFR'))
        self.assertLess(cmds.index('RNFR'), cmds.index('RNTO'))
        self.assertEqual(cmds[-1], 'QUIT')

    def test_binary_data_larger_than_a_buffer(self):
        data = bytes(range(256)) * 2000
        self.dest().store('big.pdf', data)
        self.assertEqual(self.read('big.pdf'), data)

    def test_existing_file_is_never_overwritten(self):
        with open(os.path.join(self.root, 'scan.pdf'), 'wb') as f:
            f.write(b'old')
        dest = self.dest()
        self.assertEqual(dest.store('scan.pdf', b'new'), 'ftp://127.0.0.1/scan (1).pdf')
        self.assertEqual(dest.store('scan.pdf', b'newer'), 'ftp://127.0.0.1/scan (2).pdf')
        self.assertEqual(sorted(os.listdir(self.root)), ['scan (1).pdf', 'scan (2).pdf', 'scan.pdf'])
        self.assertEqual(self.read('scan.pdf'), b'old')
        self.assertEqual(self.read('scan (1).pdf'), b'new')
        self.assertEqual(self.read('scan (2).pdf'), b'newer')

    def test_empty_folder_is_not_an_error(self):
        self.dest().store('scan.pdf', b'x')  # the fake answers "550 No files found" to NLST
        self.assertEqual(os.listdir(self.root), ['scan.pdf'])

    def test_subdirectory(self):
        os.mkdir(os.path.join(self.root, 'Scans'))
        where = self.dest(directory='/Scans/').store('a b.pdf', b'x')
        self.assertEqual(where, 'ftp://127.0.0.1/Scans/a b.pdf')
        self.assertEqual(self.read('Scans', 'a b.pdf'), b'x')

    def test_missing_directory_raises_and_creates_nothing(self):
        with self.assertRaises(ftplib.error_perm):
            self.dest(directory='/Nope').store('a.pdf', b'x')
        self.assertEqual(os.listdir(self.root), [])

    def test_wrong_password_raises(self):
        with self.assertRaises(ftplib.error_perm):
            self.dest(password='wrong').store('a.pdf', b'x')
        self.assertEqual(os.listdir(self.root), [])

    def test_unreachable_server_raises(self):
        dest = self.dest()
        self.server.shutdown()
        self.server.server_close()
        with self.assertRaises(OSError):
            dest.store('a.pdf', b'x')

    def test_tls_is_required_before_credentials_are_sent(self):
        with self.assertRaises(ftplib.error_perm):  # the fake does not know AUTH TLS
            self.dest(tls=True).store('a.pdf', b'x')
        self.assertEqual(self.server.log, ['AUTH'])
        self.assertEqual(os.listdir(self.root), [])

    def test_active_mode(self):
        self.dest(passive=False).store('a.pdf', b'x')
        self.assertEqual(self.read('a.pdf'), b'x')
        self.assertIn('PORT', self.server.log)
        self.assertNotIn('PASV', self.server.log)

    def test_insecure_without_tls(self):
        opts = {'host': 'ftp.example.com', 'tls': False}
        self.assertTrue(FtpDestination.insecure(opts))
        self.assertFalse(FtpDestination.insecure({**opts, 'tls': True}))
        self.assertFalse(FtpDestination.insecure({'host': '127.0.0.1', 'tls': False}))
        self.assertFalse(FtpDestination.insecure({'host': 'localhost', 'tls': False}))

    def test_password_is_a_secret(self):
        self.assertEqual(FtpDestination.SECRETS, {'password'})


class FtpConfigTest(unittest.TestCase):
    ENV = {'SCANBRIDGE_PRINTER_HOST': '192.0.2.1', 'SCANBRIDGE_FTP_HOST': 'ftp.example.com'}

    def test_defaults(self):
        dc = config.load(environ=self.ENV).destinations['ftp']
        self.assertEqual(dc.type, 'ftp')
        self.assertEqual(dc.options, {'host': 'ftp.example.com', 'port': 21, 'username': 'anonymous', 'password': '',
                                      'directory': '/', 'tls': True, 'passive': True})

    def test_from_environment_converts_types(self):
        cfg = config.load(environ={**self.ENV, 'SCANBRIDGE_FTP_PORT': '2121', 'SCANBRIDGE_FTP_TLS': 'false',
                                   'SCANBRIDGE_FTP_PASSIVE': 'no', 'SCANBRIDGE_FTP_USERNAME': 'scanner',
                                   'SCANBRIDGE_FTP_PASSWORD': 'hunter2-ftp', 'SCANBRIDGE_FTP_DIRECTORY': '/Scans'})
        dc = cfg.destinations['ftp']
        self.assertEqual(dc.options, {'host': 'ftp.example.com', 'port': 2121, 'username': 'scanner',
                                      'password': 'hunter2-ftp', 'directory': '/Scans', 'tls': False,
                                      'passive': False})
        self.assertIs(dc.options['tls'], False)
        dest = destinations.create(dc)
        self.assertIsInstance(dest, FtpDestination)
        text = '\n'.join(cfg.describe())
        self.assertRegex(text, r'password=.?\*\*\*')
        self.assertNotIn('hunter2', text)

    def test_host_is_required(self):
        with self.assertRaisesRegex(config.ConfigError, 'host is required'):
            config.load(environ={'SCANBRIDGE_PRINTER_HOST': 'h', 'SCANBRIDGE_FTP_PORT': '21'})

    def test_invalid_values(self):
        for key, value in (('PORT', 'ftp'), ('TLS', 'maybe')):
            with self.subTest(key=key), self.assertRaises(config.ConfigError):
                config.load(environ={**self.ENV, f'SCANBRIDGE_FTP_{key}': value})


if __name__ == '__main__':
    unittest.main()

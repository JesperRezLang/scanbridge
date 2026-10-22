import contextlib
import datetime
import io
import logging
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from scanbridge import app, config
from scanbridge.config import Printer, Target
from scanbridge.drivers.brother import BrotherDriver
from tests.test_pdf import make_jpeg


def packet(user='MYPC', func='FILE', regid=16422, seq=5):
    return (b'\x02\x00\x79\x30' + f'TYPE=BR;BUTTON=SCAN;USER="{user}";FUNC={func};HOST=192.0.2.233:54935;APPNUM=4;'
            f'P1=0;P2=0;P3=0;P4=0;REGID={regid};SEQ={seq};'.encode())


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


class FakeDriver:
    udp_port = 54925
    parse_event = staticmethod(BrotherDriver.parse_event)

    def __init__(self, jpeg=None):
        self.jpeg = jpeg or make_jpeg(600, 300)
        self.scans = []  # (ip, profile)
        self.registrations = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.error = None

    def register(self, names, advertise_ip):
        self.registrations.append((list(names), advertise_ip))
        return [0] * (4 * len(names))

    def scan(self, ip, profile):
        self.scans.append((ip, profile))
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError('test did not release the scan')
        if self.error:
            raise self.error
        return self.jpeg


class ServiceCase(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.data_dir = os.path.join(self.tmp, 'data')
        self.out = os.path.join(self.tmp, 'out')

    def make_service(self, **extra):
        env = {'SCANBRIDGE_PRINTER_HOST': '127.0.0.1', 'SCANBRIDGE_DATA_DIR': self.data_dir,
               'SCANBRIDGE_DISPLAY_NAME': 'MYPC', 'SCANBRIDGE_LOCAL_PATH': self.out, **extra}
        self.service = app.Service(config.load(environ=env))
        self.rt = self.service.printers[0]
        self.rt.driver = FakeDriver()
        self.rt.ip = '127.0.0.1'
        self.sock = FakeSocket()
        return self.service

    def press(self, data, ip='127.0.0.1'):
        self.service.on_packet(self.sock, data, (ip, 54925))

    def wait_idle(self):
        self.assertTrue(wait_for(lambda: not self.rt.busy.locked()), 'scan did not finish')

    def delivered(self):
        return sorted(os.listdir(self.out)) if os.path.isdir(self.out) else []

    def read(self, name):
        with open(os.path.join(self.out, name), 'rb') as f:
            return f.read()


class MakeFilenameTest(unittest.TestCase):
    NOW = datetime.datetime(2026, 1, 2, 3, 4, 5)

    def printer(self):
        return Printer(host='192.0.2.20', name='Office')

    def test_default_template(self):
        target = Target('PC', ['local'])
        self.assertEqual(app.make_filename(target, self.printer(), 'FILE', 'pdf', self.NOW),
                         'Scan_2026-01-02_030405.pdf')

    def test_all_template_fields(self):
        target = Target('MY PC', ['local'], '{date:%Y%m%d}_{target}_{function}_{printer}')
        self.assertEqual(app.make_filename(target, self.printer(), 'IMAGE', 'jpg', self.NOW),
                         '20260102_MY PC_image_Office.jpg')

    def test_defaults_to_current_time(self):
        target = Target('PC', ['local'], '{date:%Y}')
        name = app.make_filename(target, self.printer(), 'FILE', 'pdf')
        self.assertEqual(name, f'{datetime.datetime.now():%Y}.pdf')

    def test_slashes_and_control_characters_are_replaced(self):
        target = Target('a/b\\c', ['local'], '{target}\n{function}\x00\t')
        self.assertEqual(app.make_filename(target, self.printer(), 'FILE', 'pdf', self.NOW), 'a_b_c_file__.pdf')

    def test_path_traversal_is_neutralised(self):
        target = Target('PC', ['local'], '../../etc/{target}')
        name = app.make_filename(target, self.printer(), 'FILE', 'pdf', self.NOW)
        self.assertNotIn('/', name)
        self.assertEqual(name, '.._.._etc_PC.pdf')

    def test_empty_name_becomes_scan(self):
        target = Target('PC', ['local'], '  ')
        self.assertEqual(app.make_filename(target, self.printer(), 'FILE', 'pdf', self.NOW), 'Scan.pdf')

    def test_extension_is_appended(self):
        target = Target('PC', ['local'], 'x')
        self.assertEqual(app.make_filename(target, self.printer(), 'FILE', 'jpg', self.NOW), 'x.jpg')


class OnPacketTest(ServiceCase):
    def setUp(self):
        super().setUp()
        self.make_service()

    def test_packet_from_unknown_host_is_ignored_and_not_acknowledged(self):
        self.press(packet(), ip='10.9.9.9')
        self.assertEqual(self.sock.sent, [])
        self.assertEqual(self.rt.driver.scans, [])
        self.assertFalse(self.rt.busy.locked())

    def test_packet_before_first_registration_is_ignored(self):
        self.rt.ip = None
        self.press(packet())
        self.assertEqual(self.sock.sent, [])

    def test_known_printer_gets_packet_echoed(self):
        data = packet()
        self.press(data)
        self.wait_idle()
        self.assertEqual(self.sock.sent, [(data, ('127.0.0.1', 54925))])

    def test_duplicate_regid_and_seq_is_ignored_but_acknowledged(self):
        self.press(packet())
        self.wait_idle()
        self.press(packet())
        self.wait_idle()
        self.assertEqual(len(self.rt.driver.scans), 1)
        self.assertEqual(len(self.sock.sent), 2)
        self.assertEqual(len(self.delivered()), 1)

    def test_new_seq_is_a_new_press(self):
        self.press(packet(seq=5))
        self.wait_idle()
        self.press(packet(seq=6))
        self.wait_idle()
        self.assertEqual(len(self.rt.driver.scans), 2)

    def test_new_regid_is_a_new_press(self):
        self.press(packet(regid=1))
        self.wait_idle()
        self.press(packet(regid=2))
        self.wait_idle()
        self.assertEqual(len(self.rt.driver.scans), 2)

    def test_unknown_user_is_acknowledged_but_not_scanned(self):
        data = packet(user='SOMEONE ELSE')
        self.press(data)
        self.assertEqual(self.sock.sent, [(data, ('127.0.0.1', 54925))])
        self.assertEqual(self.rt.driver.scans, [])
        self.assertFalse(self.rt.busy.locked())
        self.assertEqual(self.delivered(), [])

    def test_matching_press_scans_and_delivers_pdf(self):
        self.press(packet())
        self.wait_idle()
        self.assertEqual(self.rt.driver.scans, [('127.0.0.1', config.Profile('pdf', 300, 'color'))])
        files = self.delivered()
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0], r'^Scan_\d{4}-\d\d-\d\d_\d{6}\.pdf$')
        data = self.read(files[0])
        self.assertTrue(data.startswith(b'%PDF-1.4'))
        self.assertIn(self.rt.driver.jpeg, data)

    def test_image_function_delivers_the_jpeg(self):
        self.press(packet(func='IMAGE'))
        self.wait_idle()
        files = self.delivered()
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].endswith('.jpg'))
        self.assertEqual(self.read(files[0]), self.rt.driver.jpeg)

    def test_unknown_function_falls_back_to_pdf(self):
        self.press(packet(func='WEIRD'))
        self.wait_idle()
        self.assertTrue(self.delivered()[0].endswith('.pdf'))

    def test_custom_filename_template(self):
        self.make_service(SCANBRIDGE_FILENAME='{target}-{function}-{printer}')
        self.press(packet())
        self.wait_idle()
        self.assertEqual(self.delivered(), ['MYPC-file-127.0.0.1.pdf'])

    def test_delivers_to_every_destination_of_the_target(self):
        second = os.path.join(self.tmp, 'second')
        toml = os.path.join(self.tmp, 'c.toml')
        with open(toml, 'w', encoding='utf-8') as f:
            f.write(f'data_dir = {self.data_dir!r}\n[destinations.a]\ntype = "local"\npath = {self.out!r}\n'
                    f'[destinations.b]\ntype = "local"\npath = {second!r}\n'
                    '[[printers]]\nhost = "127.0.0.1"\n[[printers.targets]]\nname = "MYPC"\n'
                    'destinations = ["a", "b"]\n')
        self.service = app.Service(config.load(environ={}, path=toml))
        self.rt = self.service.printers[0]
        self.rt.driver, self.rt.ip = FakeDriver(), '127.0.0.1'
        self.press(packet())
        self.wait_idle()
        self.assertEqual(len(self.delivered()), 1)
        self.assertEqual(len(os.listdir(second)), 1)

    def test_busy_lock_prevents_second_concurrent_scan(self):
        driver = self.rt.driver
        driver.release.clear()
        self.press(packet(seq=1))
        self.assertTrue(driver.started.wait(5))
        self.press(packet(seq=2))  # while the first scan is still running
        self.assertEqual(len(driver.scans), 1)
        self.assertEqual(len(self.sock.sent), 2)  # still acknowledged
        driver.release.set()
        self.wait_idle()
        self.assertEqual(len(driver.scans), 1)
        self.assertEqual(len(self.delivered()), 1)

    def test_next_press_works_after_a_scan_finished(self):
        self.press(packet(seq=1))
        self.wait_idle()
        self.press(packet(seq=2))
        self.wait_idle()
        self.assertEqual(len(self.rt.driver.scans), 2)
        self.assertEqual(len(self.delivered()), 2)

    def test_failed_scan_releases_the_lock_and_delivers_nothing(self):
        self.rt.driver.error = ConnectionError('printer went away')
        self.press(packet(seq=1))
        self.wait_idle()
        self.assertEqual(self.delivered(), [])
        self.rt.driver.error = None
        self.press(packet(seq=2))
        self.wait_idle()
        self.assertEqual(len(self.delivered()), 1)

    def test_two_printers_are_independent(self):
        cfg = config.Config(
            [Printer('127.0.0.1', 'one', targets=[Target('MYPC', ['local'])]),
             Printer('127.0.0.2', 'two', targets=[Target('MYPC', ['local'])])],
            {'local': config.DestinationConfig('local', 'local', {'path': self.out})}, data_dir=self.data_dir)
        self.service = app.Service(cfg)
        for rt, ip in zip(self.service.printers, ('127.0.0.1', '127.0.0.2'), strict=True):
            rt.driver, rt.ip = FakeDriver(), ip
        self.service.on_packet(self.sock, packet(), ('127.0.0.2', 54925))
        first, second = self.service.printers
        self.assertTrue(wait_for(lambda: not second.busy.locked()))
        self.assertEqual((len(first.driver.scans), len(second.driver.scans)), (0, 1))
        self.assertEqual(self.sock.sent[0][1], ('127.0.0.2', 54925))


class FailingDestinationTest(ServiceCase):
    def test_failed_delivery_goes_to_the_queue(self):
        blocker = os.path.join(self.tmp, 'blocker')
        with open(blocker, 'wb') as f:  # a file where the destination wants a folder: store() fails
            f.write(b'x')
        self.make_service(SCANBRIDGE_LOCAL_PATH=os.path.join(blocker, 'sub'))
        self.press(packet())
        self.wait_idle()
        queued = os.listdir(os.path.join(self.data_dir, 'queue', 'local'))
        self.assertEqual(len(queued), 1)
        self.assertTrue(queued[0].endswith('.pdf'))


class RegisterLoopTest(ServiceCase):
    def test_registers_names_with_advertise_ip_and_resolves_host(self):
        self.make_service(SCANBRIDGE_ADVERTISE_IP='10.0.0.2')
        self.rt.ip = None
        driver = self.rt.driver
        driver.register = lambda names, ip: (self.service.stop.set(), [0] * 4)[1]
        self.service.register_loop(self.rt)
        self.assertEqual(self.rt.ip, '127.0.0.1')

    def test_register_arguments(self):
        self.make_service(SCANBRIDGE_ADVERTISE_IP='10.0.0.2')
        calls = []

        def register(names, ip):
            calls.append((list(names), ip))
            self.service.stop.set()
            return [0, 0, 0, 0]

        self.rt.driver.register = register
        self.service.register_loop(self.rt)
        self.assertEqual(calls, [(['MYPC'], '10.0.0.2')])

    def test_registration_failure_does_not_end_the_loop(self):
        self.make_service(SCANBRIDGE_ADVERTISE_IP='10.0.0.2')
        calls = []

        def register(names, ip):
            calls.append(ip)
            self.service.stop.set()
            raise OSError('printer unreachable')

        self.rt.driver.register = register
        with mock.patch.object(self.service.stop, 'wait', side_effect=lambda _: None) as wait:
            self.service.register_loop(self.rt)
        self.assertEqual(calls, ['10.0.0.2'])
        wait.assert_called_with(30)

    def test_local_ip_towards_loopback(self):
        self.assertEqual(app.local_ip_towards('127.0.0.1'), '127.0.0.1')


class MigrateLegacyQueueTest(ServiceCase):
    SEAFILE = {'SCANBRIDGE_SEAFILE_URL': 'http://127.0.0.1:9', 'SCANBRIDGE_SEAFILE_TOKEN': 't'}

    def pending(self, *names):
        folder = os.path.join(self.data_dir, 'pending')
        os.makedirs(folder, exist_ok=True)
        for name in names:
            with open(os.path.join(folder, name), 'wb') as f:
                f.write(name.encode())
        return folder

    def test_moves_pending_files_to_seafile_queue(self):
        service = self.make_service(**self.SEAFILE)
        old = self.pending('a.pdf', 'b.pdf')
        service.migrate_legacy_queue()
        new = os.path.join(self.data_dir, 'queue', 'seafile')
        self.assertEqual(sorted(os.listdir(new)), ['a.pdf', 'b.pdf'])
        self.assertEqual(os.listdir(old), [])
        with open(os.path.join(new, 'a.pdf'), 'rb') as f:
            self.assertEqual(f.read(), b'a.pdf')

    def test_part_files_stay_and_names_do_not_collide(self):
        service = self.make_service(**self.SEAFILE)
        old = self.pending('a.pdf', 'b.pdf.part')
        new = os.path.join(self.data_dir, 'queue', 'seafile')
        os.makedirs(new)
        with open(os.path.join(new, 'a.pdf'), 'wb') as f:
            f.write(b'already queued')
        service.migrate_legacy_queue()
        self.assertEqual(sorted(os.listdir(new)), ['a (1).pdf', 'a.pdf'])
        self.assertEqual(os.listdir(old), ['b.pdf.part'])
        with open(os.path.join(new, 'a.pdf'), 'rb') as f:
            self.assertEqual(f.read(), b'already queued')

    def test_nothing_happens_without_seafile_destination(self):
        service = self.make_service()
        old = self.pending('a.pdf')
        service.migrate_legacy_queue()
        self.assertEqual(os.listdir(old), ['a.pdf'])
        self.assertFalse(os.path.exists(os.path.join(self.data_dir, 'queue')))

    def test_nothing_happens_without_pending_folder(self):
        service = self.make_service(**self.SEAFILE)
        service.migrate_legacy_queue()
        self.assertFalse(os.path.exists(os.path.join(self.data_dir, 'queue')))

    def test_empty_pending_folder_creates_no_queue(self):
        service = self.make_service(**self.SEAFILE)
        self.pending()
        service.migrate_legacy_queue()
        self.assertFalse(os.path.exists(os.path.join(self.data_dir, 'queue', 'seafile')))


class MainTest(unittest.TestCase):
    def run_main(self, argv, env):
        out, err = io.StringIO(), io.StringIO()
        with (mock.patch.dict(os.environ, env, clear=True), mock.patch('scanbridge.app.logging.basicConfig'),
              contextlib.redirect_stdout(out), contextlib.redirect_stderr(err)):
            code = app.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_check_prints_summary(self):
        code, out, err = self.run_main(['--check'], {'SCANBRIDGE_PRINTER_HOST': '192.0.2.20',
                                                     'SCANBRIDGE_SEAFILE_URL': 'https://sf.example',
                                                     'SCANBRIDGE_SEAFILE_TOKEN': 'super-secret'})
        self.assertEqual(code, 0)
        self.assertIn('Printer 192.0.2.20', out)
        self.assertIn('"SCANBRIDGE" -> seafile', out)
        self.assertIn('Destination seafile (seafile)', out)
        self.assertNotIn('super-secret', out)
        self.assertEqual(err, '')

    def test_check_with_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'c.toml')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('[[printers]]\nhost = "10.0.0.1"\nname = "Hall"\n')
            code, out, _ = self.run_main(['--check', '--config', path], {})
        self.assertEqual(code, 0)
        self.assertIn('Printer Hall: 10.0.0.1', out)

    def test_check_does_not_start_the_service(self):
        with mock.patch.object(app.Service, 'run') as run:
            self.run_main(['--check'], {'SCANBRIDGE_PRINTER_HOST': 'p'})
        run.assert_not_called()

    def test_config_error_returns_2(self):
        code, out, err = self.run_main(['--check'], {})
        self.assertEqual(code, 2)
        self.assertEqual(out, '')
        self.assertIn('Configuration error: no printer configured', err)

    def test_invalid_display_name_returns_2(self):
        code, _, err = self.run_main(['--check'], {'SCANBRIDGE_PRINTER_HOST': 'p',
                                                   'SCANBRIDGE_DISPLAY_NAME': 'x' * 16})
        self.assertEqual(code, 2)
        self.assertIn('display name', err)

    def test_missing_config_file_returns_2(self):
        code, _, err = self.run_main(['--check', '--config', '/nonexistent/scanbridge.toml'], {})
        self.assertEqual(code, 2)
        self.assertIn('cannot read', err)

    def test_version(self):
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()) as out:
            app.main(['--version'])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn('scanbridge', out.getvalue())


if __name__ == '__main__':
    unittest.main()

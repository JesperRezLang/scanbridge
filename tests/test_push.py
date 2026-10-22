import unittest
from unittest import mock

from scanbridge.config import Printer, Profile
from scanbridge.drivers.brother import FUNCTIONS, UDP_PORT, BrotherDriver, phoenix, push

PACKET = (b'\x02\x00\x79\x30' + b'TYPE=BR;BUTTON=SCAN;USER="MYPC";FUNC=FILE;HOST=192.0.2.233:54935;APPNUM=4;'
          b'P1=0;P2=0;P3=0;P4=0;REGID=16422;SEQ=5;')


class RegistrationStringTest(unittest.TestCase):
    def test_exact_output(self):
        self.assertEqual(push.registration_string('MYPC', 'FILE', '192.0.2.233', 1, 360),
                         'TYPE=BR;BUTTON=SCAN;USER="MYPC";FUNC=FILE;HOST=192.0.2.233:54925;APPNUM=1;'
                         'DURATION=360;BRID=;CC=1;')

    def test_other_values(self):
        self.assertEqual(push.registration_string('A B', 'OCR', '10.0.0.2', 3, 60),
                         'TYPE=BR;BUTTON=SCAN;USER="A B";FUNC=OCR;HOST=10.0.0.2:54925;APPNUM=3;DURATION=60;BRID=;CC=1;')

    def test_constants(self):
        self.assertEqual(UDP_PORT, 54925)
        self.assertEqual(FUNCTIONS, ('FILE', 'IMAGE', 'OCR', 'EMAIL'))
        self.assertEqual(push.OID_REGISTER, '1.3.6.1.4.1.2435.2.3.9.2.11.1.1.0')


class ParseEventTest(unittest.TestCase):
    def test_real_packet(self):
        self.assertEqual(push.parse_event(PACKET), {
            'TYPE': 'BR', 'BUTTON': 'SCAN', 'USER': 'MYPC', 'FUNC': 'FILE', 'HOST': '192.0.2.233:54935',
            'APPNUM': '4', 'P1': '0', 'P2': '0', 'P3': '0', 'P4': '0', 'REGID': '16422', 'SEQ': '5'})

    def test_user_with_space(self):
        ev = push.parse_event(b'\x02\x00\x20\x30TYPE=BR;USER="MY PC";FUNC=IMAGE;SEQ=1;')
        self.assertEqual((ev['USER'], ev['FUNC'], ev['SEQ']), ('MY PC', 'IMAGE', '1'))

    def test_header_is_skipped(self):
        self.assertEqual(push.parse_event(b'ABCDX=1;'), {'X': '1'})

    def test_garbage_gives_empty_dict(self):
        self.assertEqual(push.parse_event(b'\x02\x00\x00\x30\xff\xfe'), {})
        self.assertEqual(push.parse_event(b''), {})

    def test_driver_exposes_parse_event(self):
        self.assertEqual(BrotherDriver.parse_event(PACKET)['USER'], 'MYPC')
        self.assertEqual(BrotherDriver(None).parse_event(PACKET)['SEQ'], '5')
        self.assertEqual(BrotherDriver.udp_port, 54925)


class RegisterTest(unittest.TestCase):
    def setUp(self):
        self.printer = Printer(host='192.0.2.20', name='P', username='admin', password='pw', register_duration=200)
        self.driver = BrotherDriver(self.printer)

    def test_four_items_per_name(self):
        with mock.patch.object(phoenix, 'mib_set', return_value=[0] * 8) as mib_set:
            status = self.driver.register(['ONE', 'TWO'], '10.0.0.2')
        self.assertEqual(status, [0] * 8)
        mib_set.assert_called_once()
        host, user, password, items = mib_set.call_args.args
        self.assertEqual((host, user, password), ('192.0.2.20', 'admin', 'pw'))
        self.assertEqual(len(items), 8)
        self.assertTrue(all(oid == push.OID_REGISTER for oid, _ in items))
        expected = [push.registration_string(name, func, '10.0.0.2', appnum, 200)
                    for name in ('ONE', 'TWO') for appnum, func in enumerate(FUNCTIONS, 1)]
        self.assertEqual([value for _, value in items], expected)

    def test_appnums_one_to_four_per_name(self):
        with mock.patch.object(phoenix, 'mib_set', return_value=[0] * 4) as mib_set:
            self.driver.register(['ONE'], '10.0.0.2')
        values = [v for _, v in mib_set.call_args.args[3]]
        self.assertEqual([f'APPNUM={n};' in v for n, v in enumerate(values, 1)], [True] * 4)
        self.assertEqual([v.split('FUNC=')[1].split(';')[0] for v in values], ['FILE', 'IMAGE', 'OCR', 'EMAIL'])
        self.assertTrue(all('USER="ONE"' in v and 'HOST=10.0.0.2:54925' in v for v in values))

    def test_scan_delegates_to_protocol(self):
        with mock.patch('scanbridge.drivers.brother.protocol.scan', return_value=b'jpeg') as scan:
            self.assertEqual(self.driver.scan('1.2.3.4', Profile('pdf', 300, 'color')), b'jpeg')
        scan.assert_called_once_with('1.2.3.4', dpi=300, color='C24BIT', area=(0, 0, 2409, 3437))


if __name__ == '__main__':
    unittest.main()

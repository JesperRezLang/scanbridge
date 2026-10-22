import os
import tempfile
import unittest
from unittest import mock

from scanbridge import config
from scanbridge.config import ConfigError, Profile
from scanbridge.drivers.brother import BrotherDriver
from tests.test_app import ServiceCase, packet
from tests.test_pdf import make_jpeg

BASE = {'SCANBRIDGE_PRINTER_HOST': 'p'}
DEFAULTS = {'FILE': Profile('pdf', 300, 'color'), 'IMAGE': Profile('jpg', 300, 'color'),
            'OCR': Profile('pdf', 300, 'color'), 'EMAIL': Profile('pdf', 300, 'color')}


def load(env=None, path=None):
    return config.load(environ={**BASE, **(env or {})}, path=path)


def profiles_of(cfg):
    return cfg.printers[0].targets[0].profiles


class TomlCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def load_toml(self, target_toml, env=None, **kwargs):
        path = os.path.join(self.tmp, 'c.toml')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('[[printers]]\nhost = "h"\n[[printers.targets]]\nname = "A"\n' + target_toml)
        return config.load(environ=env or {}, path=path)

    def assertRejected(self, target_toml, pattern, env=None):
        with self.assertRaisesRegex(ConfigError, pattern):
            self.load_toml(target_toml, env)


class DefaultsTest(unittest.TestCase):
    def test_defaults_are_todays_behaviour(self):
        self.assertEqual(profiles_of(load()), DEFAULTS)

    def test_always_contains_all_four_functions(self):
        self.assertEqual(list(profiles_of(load())), ['FILE', 'IMAGE', 'OCR', 'EMAIL'])

    def test_target_built_by_hand_has_the_defaults(self):
        self.assertEqual(config.Target('X', ['local']).profiles, DEFAULTS)

    def test_targets_do_not_share_one_dict(self):
        a, b = config.Target('A', ['local']), config.Target('B', ['local'])
        a.profiles['FILE'] = Profile('jpg', 600, 'gray')
        self.assertEqual(b.profiles['FILE'], DEFAULTS['FILE'])

    def test_str(self):
        self.assertEqual(str(Profile('jpg', 600, 'gray')), 'jpg/600/gray')


class TomlTest(TomlCase):
    def test_override_one_key_keeps_the_others(self):
        cfg = self.load_toml('[printers.targets.profiles.file]\ndpi = 200\n')
        self.assertEqual(profiles_of(cfg), {**DEFAULTS, 'FILE': Profile('pdf', 200, 'color')})

    def test_image_default_format_survives_another_override(self):
        cfg = self.load_toml('[printers.targets.profiles.image]\ncolor = "gray"\n')
        self.assertEqual(profiles_of(cfg)['IMAGE'], Profile('jpg', 300, 'gray'))

    def test_all_keys_and_functions(self):
        cfg = self.load_toml('[printers.targets.profiles.file]\nformat = "jpg"\ndpi = 100\ncolor = "gray"\n'
                             '[printers.targets.profiles.image]\nformat = "pdf"\ndpi = 600\n'
                             '[printers.targets.profiles.ocr]\ndpi = 200\n'
                             '[printers.targets.profiles.email]\ncolor = "gray"\n')
        self.assertEqual(profiles_of(cfg), {
            'FILE': Profile('jpg', 100, 'gray'), 'IMAGE': Profile('pdf', 600, 'color'),
            'OCR': Profile('pdf', 200, 'color'), 'EMAIL': Profile('pdf', 300, 'gray')})

    def test_profiles_are_per_target(self):
        path = os.path.join(self.tmp, 'c.toml')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('[[printers]]\nhost = "h"\n[[printers.targets]]\nname = "A"\n'
                    '[printers.targets.profiles.file]\ndpi = 600\n[[printers.targets]]\nname = "B"\n')
        a, b = config.load(environ={}, path=path).printers[0].targets
        self.assertEqual((a.profiles['FILE'].dpi, b.profiles['FILE'].dpi), (600, 300))

    def test_values_are_case_insensitive_text(self):
        cfg = self.load_toml('[printers.targets.profiles.file]\nformat = "PDF"\ncolor = "Gray"\n')
        self.assertEqual(profiles_of(cfg)['FILE'], Profile('pdf', 300, 'gray'))

    def test_empty_profiles_table(self):
        self.assertEqual(profiles_of(self.load_toml('[printers.targets.profiles]\n')), DEFAULTS)

    def test_dpi_as_whole_float_is_accepted(self):
        cfg = self.load_toml('[printers.targets.profiles.file]\ndpi = 200.0\n')
        self.assertEqual(profiles_of(cfg)['FILE'].dpi, 200)


class EnvironmentTest(TomlCase):
    def test_apply_to_the_first_target_of_the_first_printer(self):
        cfg = load({'SCANBRIDGE_PROFILE_FILE_DPI': '200', 'SCANBRIDGE_PROFILE_IMAGE_COLOR': 'gray',
                    'SCANBRIDGE_PROFILE_OCR_FORMAT': 'jpg'})
        self.assertEqual(profiles_of(cfg), {
            'FILE': Profile('pdf', 200, 'color'), 'IMAGE': Profile('jpg', 300, 'gray'),
            'OCR': Profile('jpg', 300, 'color'), 'EMAIL': DEFAULTS['EMAIL']})
        self.assertIsInstance(profiles_of(cfg)['FILE'].dpi, int)

    def test_override_the_file_and_keep_other_keys_of_the_file(self):
        cfg = self.load_toml('[printers.targets.profiles.file]\ndpi = 600\ncolor = "gray"\n',
                             {'SCANBRIDGE_PROFILE_FILE_DPI': '100'})
        self.assertEqual(profiles_of(cfg)['FILE'], Profile('pdf', 100, 'gray'))

    def test_only_the_first_target_is_affected(self):
        path = os.path.join(self.tmp, 'c.toml')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('[[printers]]\nhost = "h"\n[[printers.targets]]\nname = "A"\n[[printers.targets]]\nname = "B"\n'
                    '[[printers]]\nhost = "h2"\n')
        a, b = config.load(environ={'SCANBRIDGE_PROFILE_FILE_DPI': '600'}, path=path).printers[0].targets
        self.assertEqual((a.profiles['FILE'].dpi, b.profiles['FILE'].dpi), (600, 300))
        other = config.load(environ={'SCANBRIDGE_PROFILE_FILE_DPI': '600'}, path=path).printers[1]
        self.assertEqual(other.targets[0].profiles, DEFAULTS)

    def test_file_variant(self):
        path = os.path.join(self.tmp, 'dpi')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('600\n')
        cfg = load({'SCANBRIDGE_PROFILE_FILE_DPI_FILE': path})
        self.assertEqual(profiles_of(cfg)['FILE'].dpi, 600)

    def test_other_variables_still_work_next_to_profiles(self):
        cfg = load({'SCANBRIDGE_DISPLAY_NAME': 'ARCHIVE', 'SCANBRIDGE_PROFILE_FILE_DPI': '200'})
        t = cfg.printers[0].targets[0]
        self.assertEqual((t.name, t.profiles['FILE'].dpi), ('ARCHIVE', 200))

    def test_unknown_function_variable_is_ignored(self):
        self.assertEqual(profiles_of(load({'SCANBRIDGE_PROFILE_FAX_DPI': '200'})), DEFAULTS)

    def test_bad_env_values(self):
        for var, value, pattern in [('FILE_DPI', '150', 'dpi.*is not one of 100, 200, 300, 600'),
                                    ('FILE_DPI', 'high', 'expected a number'),
                                    ('FILE_DPI', '', 'expected a number'),
                                    ('IMAGE_FORMAT', 'png', 'format.*is not one of pdf, jpg'),
                                    ('OCR_COLOR', 'bw', 'color.*is not one of color, gray')]:
            with self.subTest(var=var, value=value), self.assertRaisesRegex(ConfigError, pattern):
                load({f'SCANBRIDGE_PROFILE_{var}': value})

    def test_error_names_the_setting(self):
        with self.assertRaisesRegex(ConfigError, r'printers\[0\]\.targets\[0\]\.profiles\.file\.dpi'):
            load({'SCANBRIDGE_PROFILE_FILE_DPI': '150'})


class ValidationTest(TomlCase):
    def test_unknown_function(self):
        self.assertRejected('[printers.targets.profiles.fax]\ndpi = 200\n', 'unknown function.*fax.*known: file, image')

    def test_function_names_are_lower_case(self):
        self.assertRejected('[printers.targets.profiles.FILE]\ndpi = 200\n', 'unknown function.*FILE')

    def test_unknown_key(self):
        self.assertRejected('[printers.targets.profiles.file]\nquality = 5\n',
                            'profiles.file: unknown setting.*quality')

    def test_black_and_white_is_not_offered(self):
        self.assertRejected('[printers.targets.profiles.file]\ncolor = "bw"\n', 'color.*is not one of color, gray')

    def test_bad_format(self):
        self.assertRejected('[printers.targets.profiles.file]\nformat = "tiff"\n', 'format.*is not one of pdf, jpg')

    def test_bad_dpi_values(self):
        for dpi in ('150', '0', '-300', '1200', '300.5', '"fast"', '[300]', 'true', 'inf', 'nan'):
            with self.subTest(dpi=dpi):
                self.assertRejected(f'[printers.targets.profiles.file]\ndpi = {dpi}\n', 'profiles.file.dpi')

    def test_wrongly_typed_values(self):
        for line in ('format = 1', 'format = ["pdf"]', 'color = true', 'color = 0', 'dpi = {a = 1}'):
            with self.subTest(line=line):
                self.assertRejected(f'[printers.targets.profiles.file]\n{line}\n', 'profiles.file')

    def test_wrongly_typed_tables(self):
        self.assertRejected('profiles = 5\n', 'profiles: expected a table')
        self.assertRejected('profiles = []\n', 'profiles: expected a table')
        self.assertRejected('[printers.targets.profiles]\nfile = "pdf"\n', 'profiles.file: expected a table')

    def test_wrongly_typed_tables_with_environment_overrides(self):
        for toml in ('profiles = 5\n', '[printers.targets.profiles]\nfile = 5\n'):
            with self.subTest(toml=toml):
                self.assertRejected(toml, 'profiles', {'SCANBRIDGE_PROFILE_FILE_DPI': '200'})

    def test_reserved_destination_name_profile(self):
        toml = '[[printers]]\nhost = "h"\n[destinations.profile]\ntype = "local"\npath = "/x"\n'
        path = os.path.join(self.tmp, 'd.toml')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(toml)
        with self.assertRaisesRegex(ConfigError, 'invalid destination name.*"printer" or "profile"'):
            config.load(environ={}, path=path)


class DriverTest(unittest.TestCase):
    def scan(self, profile):
        with mock.patch('scanbridge.drivers.brother.protocol.scan', return_value=b'jpeg') as scan:
            self.assertEqual(BrotherDriver(None).scan('1.2.3.4', profile), b'jpeg')
        scan.assert_called_once()
        return scan.call_args

    def test_area_is_scaled_from_the_300_dpi_capture(self):
        for dpi, area in [(100, (0, 0, 803, 1146)), (200, (0, 0, 1606, 2291)), (300, (0, 0, 2409, 3437)),
                          (600, (0, 0, 4818, 6874))]:
            with self.subTest(dpi=dpi):
                call = self.scan(Profile('pdf', dpi, 'color'))
                self.assertEqual(call.args, ('1.2.3.4',))
                self.assertEqual(call.kwargs, {'dpi': dpi, 'color': 'C24BIT', 'area': area})

    def test_colour_mapping(self):
        self.assertEqual(self.scan(Profile('pdf', 300, 'color')).kwargs['color'], 'C24BIT')
        self.assertEqual(self.scan(Profile('pdf', 300, 'gray')).kwargs['color'], 'GRAY64')

    def test_gray_at_600(self):
        self.assertEqual(self.scan(Profile('jpg', 600, 'gray')).kwargs,
                         {'dpi': 600, 'color': 'GRAY64', 'area': (0, 0, 4818, 6874)})

    def test_every_allowed_value_has_a_brother_code(self):
        from scanbridge.drivers import brother
        self.assertEqual(set(brother.COLORS), set(config.PROFILE_CHOICES['color']))


class ServiceTest(ServiceCase):
    def setUp(self):
        super().setUp()
        self.make_service(SCANBRIDGE_FILENAME='{function}', SCANBRIDGE_PROFILE_FILE_DPI='600',
                          SCANBRIDGE_PROFILE_FILE_COLOR='gray', SCANBRIDGE_PROFILE_IMAGE_DPI='200',
                          SCANBRIDGE_PROFILE_EMAIL_FORMAT='jpg')

    def press_and_collect(self, func):
        self.press(packet(func=func))
        self.wait_idle()
        return self.rt.driver.scans[-1][1], next(n for n in self.delivered() if n.startswith(func.lower()))

    def test_each_function_uses_its_own_profile(self):
        for func, profile, ext in [('FILE', Profile('pdf', 600, 'gray'), '.pdf'),
                                   ('IMAGE', Profile('jpg', 200, 'color'), '.jpg'),
                                   ('OCR', Profile('pdf', 300, 'color'), '.pdf'),
                                   ('EMAIL', Profile('jpg', 300, 'color'), '.jpg')]:
            with self.subTest(func=func):
                self.rt.last_event = None
                scanned, name = self.press_and_collect(func)
                self.assertEqual(scanned, profile)
                self.assertTrue(name.endswith(ext), name)

    def test_unknown_function_uses_the_file_profile(self):
        scanned, name = self.press_and_collect('WEIRD')
        self.assertEqual(scanned, Profile('pdf', 600, 'gray'))
        self.assertTrue(name.endswith('.pdf'))

    def test_jpeg_profile_delivers_the_jpeg_unchanged(self):
        self.press(packet(func='IMAGE'))
        self.wait_idle()
        data = self.read(self.delivered()[0])
        self.assertEqual(data, self.rt.driver.jpeg)
        self.assertTrue(data.startswith(b'\xff\xd8'))

    def test_pdf_profile_embeds_the_jpeg_at_the_profile_dpi(self):
        self.rt.driver.jpeg = make_jpeg(4800, 6000, components=1)
        self.press(packet(func='FILE'))
        self.wait_idle()
        data = self.read(self.delivered()[0])
        self.assertTrue(data.startswith(b'%PDF-1.4'))
        self.assertIn(self.rt.driver.jpeg, data)
        self.assertIn(b'/DeviceGray', data)
        self.assertIn(b'/MediaBox [0 0 576.00 720.00]', data)  # 4800 px at 600 dpi = 8 inch = 576 pt

    def test_default_setup_still_scans_300_dpi_colour(self):
        self.make_service()
        self.press(packet(func='FILE'))
        self.wait_idle()
        self.assertEqual(self.rt.driver.scans, [('127.0.0.1', Profile('pdf', 300, 'color'))])


class DescribeTest(TomlCase):
    def test_defaults_add_no_line(self):
        self.assertFalse(any('profile' in line for line in load().describe()))

    def test_only_non_default_profiles_are_listed(self):
        lines = load({'SCANBRIDGE_PROFILE_FILE_DPI': '200', 'SCANBRIDGE_PROFILE_IMAGE_COLOR': 'gray'}).describe()
        i = next(n for n, line in enumerate(lines) if line.lstrip().startswith('"SCANBRIDGE"'))
        self.assertEqual(lines[i + 1].strip(),
                         'profiles (format/dpi/colour; others default): file=pdf/200/color, image=jpg/300/gray')

    def test_one_line_per_target(self):
        cfg = self.load_toml('[printers.targets.profiles.ocr]\nformat = "jpg"\n')
        lines = [line for line in cfg.describe() if 'profiles' in line]
        self.assertEqual(len(lines), 1)
        self.assertIn('ocr=jpg/300/color', lines[0])


if __name__ == '__main__':
    unittest.main()

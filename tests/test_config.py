import os
import tempfile
import unittest

from scanbridge import config
from scanbridge.config import ConfigError


def load(env, path=None):
    return config.load(environ=env, path=path)


class TempDirCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path


class MinimalTest(unittest.TestCase):
    def test_minimal_environment(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': '192.0.2.20'})
        self.assertEqual(len(cfg.printers), 1)
        p = cfg.printers[0]
        self.assertEqual((p.host, p.name, p.driver), ('192.0.2.20', '192.0.2.20', 'brother'))
        self.assertEqual((p.username, p.password, p.register_duration), ('Public', '0000', 360))
        self.assertIsNone(p.advertise_ip)
        self.assertEqual(len(p.targets), 1)
        t = p.targets[0]
        self.assertEqual(t.name, 'SCANBRIDGE')
        self.assertEqual(t.destinations, ['local'])
        self.assertEqual(t.filename, config.DEFAULT_FILENAME)
        self.assertEqual(list(cfg.destinations), ['local'])
        local = cfg.destinations['local']
        self.assertEqual(local.type, 'local')
        self.assertEqual(local.options, {'path': os.path.join('./data', 'scans')})
        self.assertEqual((cfg.data_dir, cfg.log_level, cfg.retry_interval), ('./data', 'INFO', 300))
        self.assertEqual(cfg.legacy, [])

    def test_default_local_destination_follows_data_dir(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_DATA_DIR': '/var/sb'})
        self.assertEqual(cfg.data_dir, '/var/sb')
        self.assertEqual(cfg.destinations['local'].options['path'], os.path.join('/var/sb', 'scans'))

    def test_global_settings(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_LOG_LEVEL': 'debug',
                    'SCANBRIDGE_RETRY_INTERVAL': '45'})
        self.assertEqual(cfg.log_level, 'DEBUG')
        self.assertEqual(cfg.retry_interval, 45)


class PrinterAndTargetEnvTest(unittest.TestCase):
    def test_all_printer_and_target_variables(self):
        cfg = load({
            'SCANBRIDGE_PRINTER_HOST': 'printer.lan',
            'SCANBRIDGE_PRINTER_NAME': 'Office',
            'SCANBRIDGE_PRINTER_USERNAME': 'admin',
            'SCANBRIDGE_PRINTER_PASSWORD': 'secret',
            'SCANBRIDGE_PRINTER_REGISTER_DURATION': '120',
            'SCANBRIDGE_PRINTER_ADVERTISE_IP': '10.0.0.5',
            'SCANBRIDGE_DISPLAY_NAME': 'MY PC',
            'SCANBRIDGE_DESTINATIONS': 'local, seafile',
            'SCANBRIDGE_FILENAME': '{target}-{function}-{printer}',
            'SCANBRIDGE_LOCAL_PATH': '/scans',
            'SCANBRIDGE_SEAFILE_URL': 'https://sf',
            'SCANBRIDGE_SEAFILE_TOKEN': 't',
        })
        p = cfg.printers[0]
        self.assertEqual((p.host, p.name, p.username, p.password), ('printer.lan', 'Office', 'admin', 'secret'))
        self.assertEqual(p.register_duration, 120)
        self.assertIsInstance(p.register_duration, int)
        self.assertEqual(p.advertise_ip, '10.0.0.5')
        t = p.targets[0]
        self.assertEqual(t.name, 'MY PC')
        self.assertEqual(t.destinations, ['local', 'seafile'])
        self.assertEqual(t.filename, '{target}-{function}-{printer}')

    def test_global_advertise_ip_falls_through_to_printer(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_ADVERTISE_IP': '10.0.0.9'})
        self.assertEqual(cfg.printers[0].advertise_ip, '10.0.0.9')

    def test_printer_advertise_ip_wins_over_global(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_ADVERTISE_IP': '10.0.0.9',
                    'SCANBRIDGE_PRINTER_ADVERTISE_IP': '10.0.0.7'})
        self.assertEqual(cfg.printers[0].advertise_ip, '10.0.0.7')

    def test_destinations_list_is_split_and_stripped(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_LOCAL_PATH': '/a',
                    'SCANBRIDGE_SEAFILE_URL': 'u', 'SCANBRIDGE_SEAFILE_TOKEN': 't',
                    'SCANBRIDGE_DESTINATIONS': ' seafile ,local,'})
        self.assertEqual(cfg.printers[0].targets[0].destinations, ['seafile', 'local'])

    def test_targets_default_to_all_destinations(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_LOCAL_PATH': '/a',
                    'SCANBRIDGE_SEAFILE_URL': 'u', 'SCANBRIDGE_SEAFILE_TOKEN': 't'})
        self.assertCountEqual(cfg.printers[0].targets[0].destinations, ['local', 'seafile'])


class DestinationEnvTest(unittest.TestCase):
    def test_seafile_from_environment(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_SEAFILE_URL': 'https://sf.example',
                    'SCANBRIDGE_SEAFILE_TOKEN': 'abc'})
        self.assertEqual(list(cfg.destinations), ['seafile'])
        d = cfg.destinations['seafile']
        self.assertEqual(d.type, 'seafile')
        self.assertEqual(d.options, {'url': 'https://sf.example', 'token': 'abc', 'directory': '/'})
        self.assertEqual(cfg.printers[0].targets[0].destinations, ['seafile'])

    def test_seafile_directory(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_SEAFILE_URL': 'u', 'SCANBRIDGE_SEAFILE_TOKEN': 't',
                    'SCANBRIDGE_SEAFILE_DIRECTORY': '/Scans'})
        self.assertEqual(cfg.destinations['seafile'].options['directory'], '/Scans')

    def test_local_path(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_LOCAL_PATH': '/mnt/share'})
        self.assertEqual(list(cfg.destinations), ['local'])
        self.assertEqual(cfg.destinations['local'].options, {'path': '/mnt/share'})

    def test_seafile_and_local_together(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_LOCAL_PATH': '/x',
                    'SCANBRIDGE_SEAFILE_URL': 'u', 'SCANBRIDGE_SEAFILE_TOKEN': 't'})
        self.assertCountEqual(cfg.destinations, ['local', 'seafile'])


class FileVariantTest(TempDirCase):
    def test_secret_read_from_file_without_trailing_newline(self):
        secret = self.write('token', 'file-token\n')
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_SEAFILE_URL': 'u',
                    'SCANBRIDGE_SEAFILE_TOKEN_FILE': secret})
        self.assertEqual(cfg.destinations['seafile'].options['token'], 'file-token')

    def test_crlf_stripped(self):
        secret = self.write('pw', 'pass\r\n')
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_PRINTER_PASSWORD_FILE': secret})
        self.assertEqual(cfg.printers[0].password, 'pass')

    def test_inner_whitespace_kept(self):
        secret = self.write('pw', ' a b \n')
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_PRINTER_PASSWORD_FILE': secret})
        self.assertEqual(cfg.printers[0].password, ' a b ')

    def test_plain_variable_wins_over_file(self):
        secret = self.write('pw', 'from-file')
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_PRINTER_PASSWORD': 'plain',
                    'SCANBRIDGE_PRINTER_PASSWORD_FILE': secret})
        self.assertEqual(cfg.printers[0].password, 'plain')

    def test_host_from_file(self):
        host = self.write('host', '192.0.2.99\n')
        cfg = load({'SCANBRIDGE_PRINTER_HOST_FILE': host})
        self.assertEqual(cfg.printers[0].host, '192.0.2.99')

    def test_missing_file_is_a_config_error(self):
        missing = os.path.join(self.tmp, 'nope')
        with self.assertRaisesRegex(ConfigError, 'SCANBRIDGE_PRINTER_PASSWORD_FILE'):
            load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_PRINTER_PASSWORD_FILE': missing})


TOML = '''
data_dir = "/d"
log_level = "debug"
retry_interval = 60
advertise_ip = "10.0.0.2"

[destinations.nas]
type = "local"
path = "/mnt/nas"

[destinations.seafile]
url = "https://sf.example"
token = "file-token"

[[printers]]
host = "192.0.2.10"
name = "Office"
register_duration = 200

[[printers.targets]]
name = "TO_NAS"
destinations = ["nas"]
filename = "{target}_{date:%Y}"

[[printers.targets]]
name = "TO_BOTH"
destinations = ["nas", "seafile"]

[[printers]]
host = "192.0.2.11"
advertise_ip = "10.1.0.2"
driver = "brother"
'''


class TomlTest(TempDirCase):
    def test_two_printers_several_targets_and_destinations(self):
        cfg = load({}, self.write('c.toml', TOML))
        self.assertEqual((cfg.data_dir, cfg.log_level, cfg.retry_interval), ('/d', 'DEBUG', 60))
        self.assertEqual(list(cfg.destinations), ['nas', 'seafile'])
        self.assertEqual(cfg.destinations['nas'].type, 'local')
        self.assertEqual(cfg.destinations['nas'].options, {'path': '/mnt/nas'})
        self.assertEqual(cfg.destinations['seafile'].type, 'seafile')  # type defaults to the name
        self.assertEqual(cfg.destinations['seafile'].options['directory'], '/')

        first, second = cfg.printers
        self.assertEqual((first.host, first.name, first.register_duration), ('192.0.2.10', 'Office', 200))
        self.assertEqual([t.name for t in first.targets], ['TO_NAS', 'TO_BOTH'])
        self.assertEqual(first.targets[0].destinations, ['nas'])
        self.assertEqual(first.targets[0].filename, '{target}_{date:%Y}')
        self.assertEqual(first.targets[1].destinations, ['nas', 'seafile'])
        self.assertEqual(first.targets[1].filename, config.DEFAULT_FILENAME)
        self.assertEqual(first.advertise_ip, '10.0.0.2')  # global value

        self.assertEqual((second.host, second.name), ('192.0.2.11', '192.0.2.11'))
        self.assertEqual(second.advertise_ip, '10.1.0.2')
        self.assertEqual([t.name for t in second.targets], ['SCANBRIDGE'])
        self.assertEqual(second.targets[0].destinations, ['nas', 'seafile'])

    def test_config_path_from_environment(self):
        path = self.write('c.toml', TOML)
        cfg = load({'SCANBRIDGE_CONFIG': path})
        self.assertEqual(len(cfg.printers), 2)

    def test_path_argument_wins_over_environment(self):
        good = self.write('good.toml', TOML)
        cfg = load({'SCANBRIDGE_CONFIG': os.path.join(self.tmp, 'missing.toml')}, good)
        self.assertEqual(len(cfg.printers), 2)

    def test_env_overrides_toml_destination_by_name(self):
        cfg = load({'SCANBRIDGE_NAS_PATH': '/other', 'SCANBRIDGE_SEAFILE_TOKEN': 'env-token'},
                   self.write('c.toml', TOML))
        self.assertEqual(cfg.destinations['nas'].options['path'], '/other')
        self.assertEqual(cfg.destinations['seafile'].options['token'], 'env-token')
        self.assertEqual(cfg.destinations['seafile'].options['url'], 'https://sf.example')

    def test_env_overrides_first_printer_only(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': '10.9.9.9', 'SCANBRIDGE_PRINTER_PASSWORD': 'pw'},
                   self.write('c.toml', TOML))
        self.assertEqual(cfg.printers[0].host, '10.9.9.9')
        self.assertEqual(cfg.printers[0].password, 'pw')
        self.assertEqual(cfg.printers[1].host, '192.0.2.11')
        self.assertEqual(cfg.printers[1].password, '0000')

    def test_env_display_name_overrides_first_target(self):
        cfg = load({'SCANBRIDGE_DISPLAY_NAME': 'RENAMED'}, self.write('c.toml', TOML))
        self.assertEqual([t.name for t in cfg.printers[0].targets], ['RENAMED', 'TO_BOTH'])

    def test_env_global_overrides_toml_global(self):
        cfg = load({'SCANBRIDGE_LOG_LEVEL': 'warning', 'SCANBRIDGE_DATA_DIR': '/e'}, self.write('c.toml', TOML))
        self.assertEqual((cfg.log_level, cfg.data_dir), ('WARNING', '/e'))

    def test_env_creates_destination_next_to_toml_ones(self):
        cfg = load({'SCANBRIDGE_LOCAL_PATH': '/extra'}, self.write('c.toml', TOML))
        self.assertEqual(list(cfg.destinations), ['nas', 'seafile', 'local'])

    def test_printers_only_toml_uses_default_local_destination(self):
        cfg = load({}, self.write('c.toml', '[[printers]]\nhost = "h"\n'))
        self.assertEqual(list(cfg.destinations), ['local'])


class LegacyTest(TempDirCase):
    FULL = {
        'PRINTER_IP': '10.0.0.5', 'DISPLAY_NAME': 'MYPC', 'ADVERTISE_IP': '10.0.0.2', 'PHOENIX_USER': 'admin',
        'PHOENIX_PASSWORD': 'pw', 'REGISTER_DURATION': '120', 'LOG_LEVEL': 'debug', 'OUTPUT_DIR': '/out',
        'SEAFILE_URL': 'https://sf.example', 'SEAFILE_REPO_TOKEN': 'tok', 'SEAFILE_DIR': '/scans',
    }

    def test_all_legacy_variables_are_mapped(self):
        cfg = load(dict(self.FULL))
        p = cfg.printers[0]
        self.assertEqual((p.host, p.username, p.password, p.register_duration),
                         ('10.0.0.5', 'admin', 'pw', 120))
        self.assertEqual(p.advertise_ip, '10.0.0.2')
        self.assertEqual(p.targets[0].name, 'MYPC')
        self.assertEqual((cfg.log_level, cfg.data_dir), ('DEBUG', '/out'))
        self.assertEqual(list(cfg.destinations), ['seafile'])
        self.assertEqual(cfg.destinations['seafile'].options,
                         {'url': 'https://sf.example', 'token': 'tok', 'directory': '/scans'})
        self.assertCountEqual(cfg.legacy, list(self.FULL))

    def test_legacy_variables_use_the_current_defaults(self):
        cfg = load({'PRINTER_IP': '10.0.0.5'})
        t = cfg.printers[0].targets[0]
        self.assertEqual(t.name, 'SCANBRIDGE')
        self.assertEqual(t.filename, 'Scan_{date:%Y-%m-%d_%H%M%S}')
        self.assertEqual(cfg.legacy, ['PRINTER_IP'])

    def test_no_legacy_defaults_with_new_names_only(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p'})
        self.assertEqual(cfg.printers[0].targets[0].name, 'SCANBRIDGE')

    def test_legacy_variable_with_config_file_in_environment(self):
        path = self.write('c.toml', '[[printers]]\nhost = "h"\n[[printers.targets]]\nname = "FROM_TOML"\n')
        cfg = load({'SCANBRIDGE_CONFIG': path, 'LOG_LEVEL': 'debug'})
        self.assertEqual(cfg.printers[0].targets[0].name, 'FROM_TOML')
        self.assertEqual(cfg.log_level, 'DEBUG')
        self.assertEqual(cfg.legacy, ['LOG_LEVEL'])

    def test_legacy_variable_with_config_file_argument(self):
        path = self.write('c.toml', '[[printers]]\nhost = "h"\n[[printers.targets]]\nname = "FROM_TOML"\n')
        cfg = load({'LOG_LEVEL': 'debug'}, path)
        self.assertEqual(cfg.printers[0].targets[0].name, 'FROM_TOML')

    def test_output_dir_without_seafile_becomes_local_path(self):
        cfg = load({'PRINTER_IP': 'p', 'OUTPUT_DIR': '/srv/scans'})
        self.assertEqual(list(cfg.destinations), ['local'])
        self.assertEqual(cfg.destinations['local'].options, {'path': '/srv/scans'})
        self.assertEqual(cfg.printers[0].targets[0].destinations, ['local'])

    def test_output_dir_with_seafile_does_not_create_local_destination(self):
        cfg = load({'PRINTER_IP': 'p', 'OUTPUT_DIR': '/srv/scans', 'SEAFILE_URL': 'u', 'SEAFILE_REPO_TOKEN': 't'})
        self.assertEqual(list(cfg.destinations), ['seafile'])

    def test_new_names_win_over_legacy_ones(self):
        cfg = load({'PRINTER_IP': 'old', 'SCANBRIDGE_PRINTER_HOST': 'new',
                    'DISPLAY_NAME': 'OLD', 'SCANBRIDGE_DISPLAY_NAME': 'NEW',
                    'SEAFILE_URL': 'old-url', 'SEAFILE_REPO_TOKEN': 'old-tok',
                    'SCANBRIDGE_SEAFILE_URL': 'new-url', 'SCANBRIDGE_SEAFILE_TOKEN': 'new-tok',
                    'OUTPUT_DIR': '/old', 'SCANBRIDGE_DATA_DIR': '/new'})
        self.assertEqual(cfg.printers[0].host, 'new')
        self.assertEqual(cfg.printers[0].targets[0].name, 'NEW')
        self.assertEqual(cfg.destinations['seafile'].options['url'], 'new-url')
        self.assertEqual(cfg.destinations['seafile'].options['token'], 'new-tok')
        self.assertEqual(cfg.data_dir, '/new')

    def test_new_default_names_win_over_legacy_defaults(self):
        cfg = load({'PRINTER_IP': 'p', 'SCANBRIDGE_FILENAME': 'x'})
        self.assertEqual(cfg.printers[0].targets[0].filename, 'x')

    def test_new_file_variable_wins_over_legacy_variable(self):
        host = self.write('host', 'new-host\n')
        cfg = load({'PRINTER_IP': 'old-host', 'SCANBRIDGE_PRINTER_HOST_FILE': host})
        self.assertEqual(cfg.printers[0].host, 'new-host')

    def test_input_environment_is_not_modified(self):
        env = {'PRINTER_IP': 'p'}
        load(env)
        self.assertEqual(env, {'PRINTER_IP': 'p'})


class ValidationTest(TempDirCase):
    def base(self, **extra):
        return {'SCANBRIDGE_PRINTER_HOST': 'p', **extra}

    def assertRejected(self, env, pattern=None, path=None):
        with self.assertRaises(ConfigError) as cm:
            load(env, path)
        if pattern:
            self.assertRegex(str(cm.exception), pattern)

    def test_no_printer(self):
        self.assertRejected({}, 'no printer')

    def test_no_printer_with_only_destinations(self):
        self.assertRejected({'SCANBRIDGE_LOCAL_PATH': '/x'}, 'no printer')

    def test_display_name_too_long(self):
        self.assertRejected(self.base(SCANBRIDGE_DISPLAY_NAME='A' * 16), 'display name')

    def test_display_name_maximum_length_is_accepted(self):
        cfg = load(self.base(SCANBRIDGE_DISPLAY_NAME='A' * 15))
        self.assertEqual(cfg.printers[0].targets[0].name, 'A' * 15)

    def test_display_name_with_quote(self):
        self.assertRejected(self.base(SCANBRIDGE_DISPLAY_NAME='my"pc'), 'display name')

    def test_display_name_with_semicolon(self):
        self.assertRejected(self.base(SCANBRIDGE_DISPLAY_NAME='my;pc'), 'display name')

    def test_display_name_non_ascii(self):
        self.assertRejected(self.base(SCANBRIDGE_DISPLAY_NAME='Scannér'), 'display name')

    def test_display_name_empty(self):
        self.assertRejected(self.base(SCANBRIDGE_DISPLAY_NAME=''), 'display name')

    def test_duplicate_display_names(self):
        toml = '[[printers]]\nhost = "h"\n[[printers.targets]]\nname = "A"\n[[printers.targets]]\nname = "A"\n'
        self.assertRejected({}, 'unique', self.write('c.toml', toml))

    def test_same_display_name_on_different_printers_is_fine(self):
        toml = ('[[printers]]\nhost = "h1"\n[[printers.targets]]\nname = "A"\n'
                '[[printers]]\nhost = "h2"\n[[printers.targets]]\nname = "A"\n')
        cfg = load({}, self.write('c.toml', toml))
        self.assertEqual(len(cfg.printers), 2)

    def test_unknown_destination_referenced(self):
        self.assertRejected(self.base(SCANBRIDGE_DESTINATIONS='local,nope'), 'unknown destination.*nope')

    def test_empty_destination_list(self):
        self.assertRejected(self.base(SCANBRIDGE_DESTINATIONS=''), 'no destinations')

    def test_unknown_destination_type(self):
        toml = '[[printers]]\nhost = "h"\n[destinations.x]\ntype = "sftp"\n'
        self.assertRejected({}, 'unknown type', self.write('c.toml', toml))

    def test_unknown_destination_without_type_uses_name_as_type(self):
        toml = '[[printers]]\nhost = "h"\n[destinations.sftp]\nhost = "x"\n'
        self.assertRejected({}, 'unknown type', self.write('c.toml', toml))

    def test_unknown_printer_setting(self):
        toml = '[[printers]]\nhost = "h"\ncolour = "x"\n'
        self.assertRejected({}, 'unknown setting.*colour', self.write('c.toml', toml))

    def test_unknown_global_setting(self):
        toml = 'colour = "x"\n[[printers]]\nhost = "h"\n'
        self.assertRejected({}, 'unknown setting.*colour', self.write('c.toml', toml))

    def test_unknown_target_setting(self):
        toml = '[[printers]]\nhost = "h"\n[[printers.targets]]\nname = "A"\ncolour = "x"\n'
        self.assertRejected({}, 'unknown setting.*colour', self.write('c.toml', toml))

    def test_unknown_destination_option(self):
        toml = '[[printers]]\nhost = "h"\n[destinations.local]\npath = "/x"\ncolour = "x"\n'
        self.assertRejected({}, 'unknown setting.*colour', self.write('c.toml', toml))

    def test_missing_required_destination_option(self):
        self.assertRejected(self.base(SCANBRIDGE_SEAFILE_URL='https://sf'), 'token is required')

    def test_missing_required_printer_host(self):
        self.assertRejected({'SCANBRIDGE_PRINTER_NAME': 'x'}, 'host is required')

    def test_bad_integer(self):
        self.assertRejected(self.base(SCANBRIDGE_PRINTER_REGISTER_DURATION='abc'), 'expected a number')

    def test_bad_integer_in_global_setting(self):
        self.assertRejected(self.base(SCANBRIDGE_RETRY_INTERVAL='soon'), 'expected a number')

    def test_retry_interval_below_one_second(self):
        self.assertRejected(self.base(SCANBRIDGE_RETRY_INTERVAL='0'), 'retry_interval must be at least 1')

    def test_register_duration_below_sixty_seconds(self):
        self.assertRejected(self.base(SCANBRIDGE_PRINTER_REGISTER_DURATION='59'), 'at least 60')

    def test_interval_minimums_are_accepted(self):
        cfg = load(self.base(SCANBRIDGE_RETRY_INTERVAL='1', SCANBRIDGE_PRINTER_REGISTER_DURATION='60'))
        self.assertEqual((cfg.retry_interval, cfg.printers[0].register_duration), (1, 60))

    def test_bad_log_level(self):
        self.assertRejected(self.base(SCANBRIDGE_LOG_LEVEL='LOUD'), 'log_level')

    def test_bad_filename_template_field(self):
        self.assertRejected(self.base(SCANBRIDGE_FILENAME='{nope}'), 'file name template')

    def test_bad_filename_template_positional(self):
        self.assertRejected(self.base(SCANBRIDGE_FILENAME='{0}'), 'file name template')

    def test_bad_filename_template_syntax(self):
        self.assertRejected(self.base(SCANBRIDGE_FILENAME='{date'), 'file name template')

    def test_all_documented_filename_fields_are_accepted(self):
        cfg = load(self.base(SCANBRIDGE_FILENAME='{date:%Y}{target}{function}{printer}'))
        self.assertEqual(cfg.printers[0].targets[0].filename, '{date:%Y}{target}{function}{printer}')

    def test_duplicate_printer_hosts(self):
        toml = '[[printers]]\nhost = "h"\n[[printers]]\nhost = "h"\n'
        self.assertRejected({}, 'same host', self.write('c.toml', toml))

    def test_reserved_destination_name_printer(self):
        toml = '[[printers]]\nhost = "h"\n[destinations.printer]\ntype = "local"\npath = "/x"\n'
        self.assertRejected({}, 'invalid destination name', self.write('c.toml', toml))

    def test_invalid_destination_name_characters(self):
        toml = '[[printers]]\nhost = "h"\n[destinations."my nas"]\ntype = "local"\npath = "/x"\n'
        self.assertRejected({}, 'invalid destination name', self.write('c.toml', toml))

    def test_invalid_toml(self):
        self.assertRejected({}, 'c.toml', self.write('c.toml', 'this is = = not toml'))

    def test_missing_toml_file(self):
        self.assertRejected({}, 'cannot read', os.path.join(self.tmp, 'missing.toml'))

    def test_unknown_driver(self):
        toml = '[[printers]]\nhost = "h"\ndriver = "hp"\n'
        self.assertRejected({}, 'unknown driver', self.write('c.toml', toml))

    def test_printers_must_be_a_list(self):
        toml = '[printers]\nhost = "h"\n'
        self.assertRejected({}, 'printers must be a list', self.write('c.toml', toml))

    def test_destinations_must_be_a_table(self):
        toml = 'destinations = ["local"]\n[[printers]]\nhost = "h"\n'
        self.assertRejected({}, 'destinations must be a table', self.write('c.toml', toml))

    # Wrongly typed values in the file must give a ConfigError, not a traceback.
    def test_non_string_display_name(self):
        toml = '[[printers]]\nhost = "h"\n[[printers.targets]]\nname = 5\n'
        self.assertRejected({}, path=self.write('c.toml', toml))

    def test_filename_template_with_bad_attribute(self):
        toml = '[[printers]]\nhost = "h"\n[[printers.targets]]\nfilename = "{date.x}"\n'
        self.assertRejected({}, path=self.write('c.toml', toml))

    def test_non_string_destination_name(self):
        toml = '[[printers]]\nhost = "h"\n[[printers.targets]]\ndestinations = [1]\n'
        self.assertRejected({}, path=self.write('c.toml', toml))

    def test_numeric_password_must_be_quoted(self):
        toml = '[[printers]]\nhost = "h"\npassword = 0\n'
        self.assertRejected({}, 'expected text', self.write('c.toml', toml))

    def test_error_is_an_exception_subclass(self):
        self.assertTrue(issubclass(ConfigError, Exception))


class DescribeTest(unittest.TestCase):
    def test_describe_masks_secrets(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'printer.lan', 'SCANBRIDGE_SEAFILE_URL': 'https://sf.example',
                    'SCANBRIDGE_SEAFILE_TOKEN': 'super-secret-token', 'SCANBRIDGE_PRINTER_PASSWORD': 'pw-secret'})
        text = '\n'.join(cfg.describe())
        self.assertNotIn('super-secret-token', text)
        self.assertIn("token='***'", text)
        self.assertIn('url=', text)
        self.assertIn('https://sf.example', text)
        self.assertIn('printer.lan', text)
        self.assertIn('SCANBRIDGE', text)

    def test_describe_lists_every_part(self):
        cfg = load({'SCANBRIDGE_PRINTER_HOST': 'p', 'SCANBRIDGE_ADVERTISE_IP': '10.0.0.2'})
        lines = cfg.describe()
        self.assertTrue(lines[0].startswith('Data directory:'))
        self.assertTrue(any('advertised as 10.0.0.2' in line for line in lines))
        self.assertTrue(any(line.startswith('Destination local (local)') for line in lines))


if __name__ == '__main__':
    unittest.main()

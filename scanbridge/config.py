"""Settings, from (lowest to highest precedence) built-in defaults, an optional TOML file and environment variables.

A single printer with one name needs nothing but environment variables (SCANBRIDGE_PRINTER_HOST,
SCANBRIDGE_DISPLAY_NAME, SCANBRIDGE_<DESTINATION>_<OPTION> ...). The TOML file, given with SCANBRIDGE_CONFIG, is for
several printers, names or destinations. Every variable can also be read from a file by appending _FILE to its name
(Docker secrets).
"""
import datetime
import logging
import os
import re
import tomllib
from dataclasses import dataclass, field

from . import destinations, drivers

PREFIX = 'SCANBRIDGE_'
DEFAULT_DISPLAY_NAME = 'SCANBRIDGE'
DEFAULT_FILENAME = 'Scan_{date:%Y-%m-%d_%H%M%S}'

# Setting -> default. None means optional, REQUIRED must be given.
REQUIRED = destinations.REQUIRED
GLOBAL_KEYS = {'data_dir': './data', 'log_level': 'INFO', 'retry_interval': 300, 'advertise_ip': None}
PRINTER_KEYS = {'host': REQUIRED, 'name': None, 'driver': 'brother', 'username': 'Public', 'password': '0000',
                'advertise_ip': None, 'register_duration': 360}
TARGET_KEYS = {'name': DEFAULT_DISPLAY_NAME, 'destinations': None, 'filename': DEFAULT_FILENAME}
# Target settings have shorter variable names, as most setups have a single target.
TARGET_ENV = {'name': 'DISPLAY_NAME', 'destinations': 'DESTINATIONS', 'filename': 'FILENAME'}
# SCANBRIDGE_PRINTER_* belongs to the printer, SCANBRIDGE_PROFILE_* to the scan settings.
RESERVED_DESTINATION_NAMES = {'printer', 'profile'}

# The panel functions, as the printer reports them in FUNC, and the settings of each: key -> allowed values.
# Only 300 dpi colour has been verified on hardware.
FUNCTIONS = ('FILE', 'IMAGE', 'OCR', 'EMAIL')
PROFILE_CHOICES = {'format': ('pdf', 'jpg'), 'dpi': (100, 200, 300, 600), 'color': ('color', 'gray')}

# Variables used before the SCANBRIDGE_ prefix existed. Still read, with a warning.
LEGACY_ENV = {
    'PRINTER_IP': 'PRINTER_HOST',
    'DISPLAY_NAME': 'DISPLAY_NAME',
    'ADVERTISE_IP': 'ADVERTISE_IP',
    'PHOENIX_USER': 'PRINTER_USERNAME',
    'PHOENIX_PASSWORD': 'PRINTER_PASSWORD',
    'REGISTER_DURATION': 'PRINTER_REGISTER_DURATION',
    'OUTPUT_DIR': 'DATA_DIR',
    'LOG_LEVEL': 'LOG_LEVEL',
}
LEGACY_SEAFILE_ENV = {'SEAFILE_URL': 'SEAFILE_URL', 'SEAFILE_REPO_TOKEN': 'SEAFILE_TOKEN',
                      'SEAFILE_DIR': 'SEAFILE_DIRECTORY'}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Profile:
    """How a panel function scans and what file it produces."""
    format: str = 'pdf'
    dpi: int = 300
    color: str = 'color'

    def __str__(self):
        return f'{self.format}/{self.dpi}/{self.color}'


DEFAULT_PROFILES = {'FILE': Profile(), 'IMAGE': Profile(format='jpg'), 'OCR': Profile(), 'EMAIL': Profile()}


@dataclass
class Target:
    """A name in the printer's "Scan to PC" list and what to do with scans sent to it."""
    name: str
    destinations: list
    filename: str = DEFAULT_FILENAME
    profiles: dict = field(default_factory=lambda: dict(DEFAULT_PROFILES))  # FUNC -> Profile


@dataclass
class Printer:
    host: str
    name: str
    driver: str = 'brother'
    username: str = 'Public'
    password: str = '0000'
    advertise_ip: str = None
    register_duration: int = 360
    targets: list = field(default_factory=list)


@dataclass
class DestinationConfig:
    name: str
    type: str
    options: dict


@dataclass
class Config:
    printers: list
    destinations: dict
    data_dir: str = './data'
    log_level: str = 'INFO'
    retry_interval: int = 300
    legacy: list = field(default_factory=list)  # old variable names that were used

    def describe(self):
        """Human-readable summary without secrets."""
        lines = [f'Data directory: {self.data_dir}']
        for p in self.printers:
            lines.append(f'Printer {p.name}: {p.host} ({p.driver}), advertised as {p.advertise_ip or "auto"}')
            for t in p.targets:
                lines.append(f'  "{t.name}" -> {", ".join(t.destinations)}, file name {t.filename!r}')
                changed = [f'{f.lower()}={pr}' for f, pr in t.profiles.items() if pr != DEFAULT_PROFILES.get(f)]
                if changed:
                    lines.append(f'    profiles (format/dpi/colour; others default): {", ".join(changed)}')
        for d in self.destinations.values():
            secrets = destinations.TYPES[d.type].SECRETS
            opts = ', '.join(f'{k}={"***" if k in secrets else v!r}' for k, v in d.options.items())
            lines.append(f'Destination {d.name} ({d.type}): {opts}')
        return lines


def load(environ=None, path=None):
    """Read the configuration. `path` overrides SCANBRIDGE_CONFIG. Raises ConfigError."""
    env = dict(os.environ if environ is None else environ)
    path = path or _env(env, 'CONFIG')
    legacy = _apply_legacy(env)
    raw = _read_toml(path) if path else {}
    _overlay_env(raw, env)
    cfg = _build(raw)
    cfg.legacy = legacy
    return cfg


def insecure_destinations(cfg):
    """Names of the destinations that would send scans or credentials unencrypted to another host."""
    return [d.name for d in cfg.destinations.values() if destinations.TYPES[d.type].insecure(d.options)]


# --- Environment ------------------------------------------------------------------------------

def _env(env, key):
    """SCANBRIDGE_<key>, or the contents of the file named by SCANBRIDGE_<key>_FILE. None if neither is set."""
    name = PREFIX + key
    if name in env:
        return env[name]
    if name + '_FILE' in env:
        try:
            with open(env[name + '_FILE'], encoding='utf-8') as f:
                return f.read().rstrip('\r\n')
        except OSError as e:
            raise ConfigError(f'{name}_FILE: {e}') from None
    return None


def env_name(name):
    """The part of a variable name that stands for `name`: upper case, other characters as _."""
    return re.sub(r'[^A-Z0-9]', '_', name.upper())


def _apply_legacy(env):
    """Translate the old variable names into SCANBRIDGE_ ones that are not already set. Returns the old names used."""
    used = [k for k in (*LEGACY_ENV, *LEGACY_SEAFILE_ENV) if k in env]
    if not used:
        return used
    mapping = dict(LEGACY_ENV)
    seafile = 'SEAFILE_URL' in env and 'SEAFILE_REPO_TOKEN' in env
    if seafile:
        mapping.update(LEGACY_SEAFILE_ENV)

    def default(key, value):  # unless SCANBRIDGE_<key> or SCANBRIDGE_<key>_FILE is set
        if PREFIX + key not in env and PREFIX + key + '_FILE' not in env:
            env[PREFIX + key] = value

    if not seafile and 'OUTPUT_DIR' in env:
        default('LOCAL_PATH', env['OUTPUT_DIR'])  # without Seafile, scans were saved there
    for old, new in mapping.items():
        if old in env:
            default(new, env[old])
    return used


def _overlay_env(raw, env):
    for key in GLOBAL_KEYS:
        if (v := _env(env, env_name(key))) is not None:
            raw[key] = v

    printer = {k: v for k in PRINTER_KEYS if (v := _env(env, 'PRINTER_' + env_name(k))) is not None}
    target = {k: v for k, var in TARGET_ENV.items() if (v := _env(env, var)) is not None}
    profiles = {}  # SCANBRIDGE_PROFILE_<FUNCTION>_<KEY>
    for func in FUNCTIONS:
        for key in PROFILE_CHOICES:
            if (v := _env(env, f'PROFILE_{func}_{env_name(key)}')) is not None:
                profiles.setdefault(func.lower(), {})[key] = v
    if printer or target or profiles:
        printers = raw.setdefault('printers', [])
        if not isinstance(printers, list):
            raise ConfigError('printers must be a list ([[printers]])')
        if not printers:
            printers.append({})
        if not isinstance(printers[0], dict):
            raise ConfigError('printers[0]: expected a table')
        printers[0].update(printer)
        if target or profiles:
            targets = printers[0].setdefault('targets', [])
            if not isinstance(targets, list):
                raise ConfigError('printers[0].targets must be a list ([[printers.targets]])')
            if not targets:
                targets.append({})
            if not isinstance(targets[0], dict):
                raise ConfigError('printers[0].targets[0]: expected a table')
            targets[0].update(target)
            if profiles:
                tables = targets[0].setdefault('profiles', {})
                if not isinstance(tables, dict) or not all(isinstance(t, dict) for t in tables.values()):
                    raise ConfigError('printers[0].targets[0].profiles: expected tables, one per function')
                for func, values in profiles.items():
                    tables.setdefault(func, {}).update(values)

    dests = raw.setdefault('destinations', {})
    if not isinstance(dests, dict):
        raise ConfigError('destinations must be a table ([destinations.<name>])')
    # Destinations from the file: SCANBRIDGE_<NAME>_<OPTION>.
    for name, d in dests.items():
        cls = destinations.TYPES.get(d.get('type', name)) if isinstance(d, dict) else None
        for key in cls.OPTIONS if cls else ():
            if (v := _env(env, f'{env_name(name)}_{env_name(key)}')) is not None:
                d[key] = v
    # Destinations only in the environment: SCANBRIDGE_<TYPE>_<OPTION> creates one named after its type.
    for type_, cls in destinations.TYPES.items():
        if type_ in dests:
            continue
        opts = {k: v for k in cls.OPTIONS if (v := _env(env, f'{env_name(type_)}_{env_name(k)}')) is not None}
        if opts:
            dests[type_] = {'type': type_, **opts}


def _read_toml(path):
    try:
        with open(path, 'rb') as f:
            return tomllib.load(f)
    except OSError as e:
        raise ConfigError(f'cannot read {path}: {e}') from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f'{path}: {e}') from None


# --- Validation -------------------------------------------------------------------------------

def _settings(raw, keys, where):
    """Defaults from `keys` overlaid with `raw`, converted to the defaults' types."""
    if not isinstance(raw, dict):
        raise ConfigError(f'{where}: expected a table')
    unknown = set(raw) - set(keys)
    if unknown:
        raise ConfigError(f'{where}: unknown setting(s) {", ".join(sorted(unknown))}')
    out = {}
    for key, default in keys.items():
        value = raw.get(key, default)
        if value is REQUIRED:
            raise ConfigError(f'{where}: {key} is required')
        out[key] = _convert(value, default, f'{where}.{key}')
    return out


def _convert(value, default, where):
    if value is None:
        return None
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if str(value).lower() in ('1', 'true', 'yes', 'on'):
            return True
        if str(value).lower() in ('0', 'false', 'no', 'off'):
            return False
        raise ConfigError(f'{where}: expected true or false, got {value!r}')
    if isinstance(default, int):
        try:
            if isinstance(value, bool):
                raise ValueError
            if isinstance(value, float) and not value.is_integer():
                raise ValueError
            return int(value)
        except (ValueError, TypeError, OverflowError):
            raise ConfigError(f'{where}: expected a number, got {value!r}') from None
    if isinstance(default, str) or default is REQUIRED:
        if not isinstance(value, str):
            raise ConfigError(f'{where}: expected text, got {value!r}')
    elif isinstance(value, dict):
        raise ConfigError(f'{where}: expected a single value')
    return value


def _profiles(raw, where):
    """The Profile of every panel function: the defaults overlaid with the given `raw` tables, one per function."""
    if not isinstance(raw, dict):
        raise ConfigError(f'{where}: expected a table per function ([{where}.file] ...)')
    known = [f.lower() for f in FUNCTIONS]
    unknown = set(raw) - set(known)
    if unknown:
        raise ConfigError(f'{where}: unknown function(s) {", ".join(sorted(map(str, unknown)))} '
                          f'(known: {", ".join(known)})')
    out = {}
    for func in FUNCTIONS:
        fwhere = f'{where}.{func.lower()}'
        keys = {k: getattr(DEFAULT_PROFILES[func], k) for k in PROFILE_CHOICES}
        values = _settings(raw.get(func.lower(), {}), keys, fwhere)
        for key, allowed in PROFILE_CHOICES.items():
            if isinstance(values[key], str):
                values[key] = values[key].lower()
            if values[key] not in allowed:
                raise ConfigError(f'{fwhere}.{key}: {values[key]!r} is not one of {", ".join(map(str, allowed))}')
        out[func] = Profile(**values)
    return out


def _check_display_name(name, where):
    # Shown on the panel (at most 15 characters) and quoted in the registration string.
    if not re.fullmatch(r'[ -~]{1,15}', name) or any(c in name for c in '";'):
        raise ConfigError(f'{where}: display name {name!r} must be 1-15 ASCII characters without " or ;')


def _check_filename(template, where):
    try:
        template.format(date=datetime.datetime.now(), target='T', function='file', printer='p')
    except (KeyError, IndexError, ValueError, AttributeError, TypeError) as e:
        raise ConfigError(f'{where}: invalid file name template {template!r} ({e}); '
                          'fields are {date}, {target}, {function} and {printer}') from None


def _build(raw):
    g = _settings({k: v for k, v in raw.items() if k not in ('printers', 'destinations')}, GLOBAL_KEYS, 'settings')
    level = str(g['log_level']).upper()
    if not isinstance(logging.getLevelName(level), int):
        raise ConfigError(f'log_level: unknown level {g["log_level"]!r}')
    if g['retry_interval'] < 1:
        raise ConfigError('retry_interval must be at least 1 second')

    dests = {}
    for name, d in raw.get('destinations', {}).items():
        where = f'destinations.{name}'
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name) or name.lower() in RESERVED_DESTINATION_NAMES:
            reserved = ' or '.join(f'"{n}"' for n in sorted(RESERVED_DESTINATION_NAMES))
            raise ConfigError(f'{where}: invalid destination name (letters, digits, - and _; not {reserved})')
        if not isinstance(d, dict):
            raise ConfigError(f'{where}: expected a table')
        type_ = d.get('type', name)
        if type_ not in destinations.TYPES:
            raise ConfigError(f'{where}: unknown type {type_!r} (known: {", ".join(destinations.TYPES)})')
        options = _settings({k: v for k, v in d.items() if k != 'type'}, destinations.TYPES[type_].OPTIONS, where)
        try:
            destinations.TYPES[type_].validate(options)
        except ValueError as e:
            raise ConfigError(f'{where}: {e}') from None
        dests[name] = DestinationConfig(name, type_, options)
    if not dests:
        dests['local'] = DestinationConfig('local', 'local', {'path': os.path.join(g['data_dir'], 'scans')})

    raw_printers = raw.get('printers', [])
    if not isinstance(raw_printers, list):
        raise ConfigError('printers must be a list ([[printers]])')
    if not raw_printers:
        raise ConfigError('no printer configured: set SCANBRIDGE_PRINTER_HOST or add a [[printers]] section')
    printers = []
    for i, rp in enumerate(raw_printers):
        where = f'printers[{i}]'
        if not isinstance(rp, dict):
            raise ConfigError(f'{where}: expected a table')
        raw_targets = rp.get('targets') or [{}]
        p = _settings({k: v for k, v in rp.items() if k != 'targets'}, PRINTER_KEYS, where)
        if p['driver'] not in drivers.DRIVERS:
            raise ConfigError(f'{where}.driver: unknown driver {p["driver"]!r} (known: {", ".join(drivers.DRIVERS)})')
        if p['register_duration'] < 60:
            raise ConfigError(f'{where}.register_duration must be at least 60 seconds')
        p['name'] = p['name'] or p['host']
        p['advertise_ip'] = p['advertise_ip'] or g['advertise_ip']
        if not isinstance(raw_targets, list):
            raise ConfigError(f'{where}.targets must be a list ([[printers.targets]])')
        targets = []
        for j, rt in enumerate(raw_targets):
            twhere = f'{where}.targets[{j}]'
            raw_profiles = {}
            if isinstance(rt, dict):
                rt = dict(rt)
                raw_profiles = rt.pop('profiles', {})
            t = _settings(rt, TARGET_KEYS, twhere)
            _check_display_name(t['name'], twhere)
            _check_filename(t['filename'], twhere)
            profiles = _profiles(raw_profiles, f'{twhere}.profiles')
            names = t['destinations']
            if names is None:
                names = list(dests)
            elif isinstance(names, str):
                names = [n.strip() for n in names.split(',') if n.strip()]
            if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                raise ConfigError(f'{twhere}: destinations must be a list of destination names')
            missing = [n for n in names if n not in dests]
            if missing:
                raise ConfigError(f'{twhere}: unknown destination(s) {", ".join(missing)} '
                                  f'(configured: {", ".join(dests)})')
            if not names:
                raise ConfigError(f'{twhere}: no destinations')
            targets.append(Target(t['name'], names, t['filename'], profiles))
        seen = [t.name for t in targets]
        if len(set(seen)) != len(seen):
            raise ConfigError(f'{where}: display names must be unique')
        printers.append(Printer(**p, targets=targets))
    hosts = [p.host for p in printers]
    if len(set(hosts)) != len(hosts):
        raise ConfigError('two printers have the same host')

    return Config(printers, dests, data_dir=g['data_dir'], log_level=level, retry_interval=g['retry_interval'])

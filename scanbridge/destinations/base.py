import ipaddress
import urllib.parse

REQUIRED = object()


def is_loopback(host):
    """True for localhost and loopback addresses; a host name that is not `localhost` is not."""
    if host is None:
        return False
    if host.lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # a host name


class Destination:
    """Somewhere a finished scan is stored.

    Subclasses set `type`, declare their settings in `OPTIONS` (name -> default, or REQUIRED) and implement
    store(). The defaults also decide how values from the environment are converted (int, bool or str).
    Options listed in `SECRETS` are never logged.
    """
    type = None
    OPTIONS = {}
    SECRETS = set()

    def __init__(self, name, **options):
        self.name = name
        self.options = options

    @classmethod
    def validate(cls, options):
        """Raise ValueError if the option values cannot work. Called when the configuration is loaded."""

    @classmethod
    def insecure(cls, options):
        """True if scans and credentials would travel unencrypted: a plain http:// URL to a host other than this one."""
        url = options.get('url')
        try:
            parts = urllib.parse.urlsplit(url) if isinstance(url, str) else None
            return bool(parts and parts.scheme == 'http' and not is_loopback(parts.hostname))
        except ValueError:
            return False  # not a URL; the destination reports that itself

    def store(self, filename, data):
        """Store `data` as `filename`. Returns where it ended up, for the log. Raises on failure."""
        raise NotImplementedError

    def __str__(self):
        return f'{self.name} ({self.type})'

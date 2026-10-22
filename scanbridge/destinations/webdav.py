"""Upload to a WebDAV folder: Nextcloud, ownCloud, Synology, Apache mod_dav, and others.

`url` is the address of an existing folder. In Nextcloud and ownCloud, it is shown under Files → Files settings →
WebDAV (https://<server>/remote.php/dav/files/<user>/<folder>). Instead of the account password, create an app
password: Settings → Security → Devices & sessions. The folder is not created.
"""
import base64
import itertools
import urllib.error
import urllib.parse
import urllib.request

from .base import REQUIRED, Destination
from .util import candidate_names

MAX_NAMES = 100  # file names to try before giving up


class WebdavDestination(Destination):
    type = 'webdav'
    OPTIONS = {'url': REQUIRED, 'username': '', 'password': ''}
    SECRETS = {'password'}

    def store(self, filename, data):
        o = self.options
        base = o['url'].rstrip('/')
        headers = {'Content-Type': 'application/octet-stream', 'If-None-Match': '*'}
        if o['username']:
            credentials = f'{o["username"]}:{o["password"]}'.encode()
            headers['Authorization'] = 'Basic ' + base64.b64encode(credentials).decode()
        for name in itertools.islice(candidate_names(filename), MAX_NAMES):
            url = f'{base}/{urllib.parse.quote(name, safe="")}'
            if self._exists(url, headers):
                continue
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data, headers, method='PUT'),
                                            timeout=120) as r:
                    if r.status in (200, 201, 204):
                        return url
                    raise OSError(f'PUT {url}: unexpected HTTP status {r.status}')
            except urllib.error.HTTPError as e:
                e.close()
                if e.code == 412:  # the name is taken
                    continue
                if e.code in (404, 409):
                    raise OSError(f'PUT {url}: HTTP {e.code}, the folder {base} probably does not exist') from None
                raise
        raise OSError(f'No free file name for {filename} in {base} after {MAX_NAMES} attempts')

    @staticmethod
    def _exists(url, headers):
        # If-None-Match already prevents overwriting on servers that honour it; HEAD covers the ones that do not.
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method='HEAD'), timeout=30):
                return True
        except urllib.error.HTTPError as e:
            e.close()
            if e.code == 404:
                return False
            raise

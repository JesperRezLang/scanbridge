"""Upload to a Seafile library with a library API token.

Create the token in Seafile's web UI: library → Advanced → API Token, permission read-write. It only grants
access to that library. Endpoints: /api/v2.1/via-repo-token/ (Seafile 9+).
"""
import json
import urllib.parse
import urllib.request
import uuid

from .base import REQUIRED, Destination


def _req(url, token, data=None, headers=None, method=None):
    h = {'Authorization': f'Token {token}', **(headers or {})}
    return urllib.request.Request(url, data, h, method=method)


def upload(base_url, token, name, data, parent_dir='/'):
    """Upload `data` as `parent_dir/name`. If the name is taken, Seafile appends (1), (2) ..."""
    base = base_url.rstrip('/')
    q = urllib.parse.urlencode({'path': parent_dir})
    with urllib.request.urlopen(_req(f'{base}/api/v2.1/via-repo-token/upload-link/?{q}', token), timeout=30) as r:
        link = json.load(r)
    # The link uses the server's public name (SERVICE_URL), which may not resolve from here: swap the base.
    p = urllib.parse.urlsplit(link)
    link = base + p.path + ('?' + p.query if p.query else '')

    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="parent_dir"\r\n\r\n{parent_dir}\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f'Content-Type: application/octet-stream\r\n\r\n'.encode() + data + b'\r\n',
        f'--{boundary}--\r\n'.encode(),
    ]
    body = b''.join(parts)
    sep = '&' if '?' in link else '?'
    req = _req(f'{link}{sep}ret-json=1', token, body,
               {'Content-Type': f'multipart/form-data; boundary={boundary}'}, 'POST')
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)  # [{"name": ..., "id": ..., "size": ...}]


class SeafileDestination(Destination):
    type = 'seafile'
    OPTIONS = {'url': REQUIRED, 'token': REQUIRED, 'directory': '/'}
    SECRETS = {'token'}

    def store(self, filename, data):
        o = self.options
        result = upload(o['url'], o['token'], filename, data, o['directory'])
        stored = result[0]['name'] if result else filename
        return f'{o["directory"].rstrip("/")}/{stored}'

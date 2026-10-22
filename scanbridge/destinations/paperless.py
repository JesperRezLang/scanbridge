"""Hand scans to Paperless-ngx for consumption.

`url` is the address of the Paperless web interface. Create the token in Paperless under your user's profile
(top right) → API auth token. `tags`, `correspondent` and `document_type` are numeric IDs, visible in the address
bar when you open the item under Manage. Paperless detects duplicates itself, so names are not changed.
"""
import json
import os
import re
import urllib.request
import uuid

from .base import REQUIRED, Destination


def multipart(fields, files):
    """Body and Content-Type of a multipart/form-data request from fields (name, text) and files (name, file, bytes)."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, filename, data in files:
        quoted = filename.replace('"', '%22').replace('\r', '%0D').replace('\n', '%0A')
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{quoted}"\r\n'
                     f'Content-Type: application/octet-stream\r\n\r\n'.encode() + data + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


def _ids(option, value):
    ids = [v.strip() for v in value.split(',') if v.strip()]
    for v in ids:
        if not re.fullmatch(r'[0-9]+', v):
            raise ValueError(f'{option}: {v!r} is not a numeric ID (use the number from Paperless)')
    return ids


class PaperlessDestination(Destination):
    type = 'paperless'
    OPTIONS = {'url': REQUIRED, 'token': REQUIRED, 'tags': '', 'correspondent': '', 'document_type': ''}
    SECRETS = {'token'}

    @classmethod
    def validate(cls, options):
        _ids('tags', options['tags'])
        for key in ('correspondent', 'document_type'):
            if len(_ids(key, options[key])) > 1:
                raise ValueError(f'{key}: only one ID is allowed')

    def store(self, filename, data):
        o = self.options
        fields = [('title', os.path.splitext(filename)[0])]
        fields += [('tags', t) for t in _ids('tags', o['tags'])]
        fields += [(k, v) for k in ('correspondent', 'document_type') if (v := o[k].strip())]
        body, content_type = multipart(fields, [('document', filename, data)])
        req = urllib.request.Request(o['url'].rstrip('/') + '/api/documents/post_document/', body,
                                     {'Authorization': f'Token {o["token"]}', 'Content-Type': content_type}, 'POST')
        with urllib.request.urlopen(req, timeout=120) as r:
            return f'task {json.load(r)}'  # the ID of the consumption task

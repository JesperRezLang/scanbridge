"""Write SNMP OIDs through the printer's web server ("MIB via Phoenix").

Plain SNMP SET is refused by the firmware (noSuchName / noAccess), but the same OIDs can be written with
POST https://<printer>/phoenix/mib, HTTP Digest auth and a JSON body. The printer uses a self-signed certificate.
"""
import json
import ssl
import urllib.request

_insecure = ssl.create_default_context()
_insecure.check_hostname = False
_insecure.verify_mode = ssl.CERT_NONE


def mib_set(host, username, password, items, timeout=15):
    """Write OctetString OIDs, `items` being (oid, value) pairs. Returns the status per OID (0 = ok)."""
    pw = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    pw.add_password(None, f'https://{host}/', username, password)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_insecure),
                                         urllib.request.HTTPDigestAuthHandler(pw))
    body = json.dumps({'request': [{'key': k, 'string_value': v} for k, v in items]}).encode()
    req = urllib.request.Request(f'https://{host}/phoenix/mib', body, {'Content-Type': 'application/json'})
    with opener.open(req, timeout=timeout) as r:
        return [it.get('status') for it in json.load(r)['response']]

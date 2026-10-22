"""Proof of concept: register this computer with the printer for the Scan button and log whatever arrives.

Usage: python -I skey_listen.py --printer IP [--name NAME] [--port PORT] [--minutes N]

Registration is done with SNMP SET on the same OIDs as Brother's driver (BrNetSti.dll):
  1.3.6.1.4.1.2435.2.3.9.2.11.1.1.0  register
  1.3.6.1.4.1.2435.2.3.9.2.11.1.2.0  unregister
The printer sends a UDP packet to HOST when someone selects the computer on the panel.

Newer firmware has SNMP v1/v2c read-only (SET gives noSuchName/noAccess). Brother's
software then writes the same OIDs via "MIB via Phoenix": HTTPS POST /phoenix/mib with
JSON and Digest login (default account Public/0000), see MIBviaPhoenixLib.Net45.dll.
"""
import argparse, datetime, json, os, socket, ssl, sys, time, urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'recon', 'scripts'))
from snmpget import tlv, enc_int, enc_oid, dec, dec_oid  # noqa: E402

OID_REGISTER = '1.3.6.1.4.1.2435.2.3.9.2.11.1.1.0'
OID_UNREGISTER = '1.3.6.1.4.1.2435.2.3.9.2.11.1.2.0'
FUNCS = ['IMAGE', 'OCR', 'EMAIL', 'FILE']


def snmp_set(ip, varbinds, community='internal'):
    """SNMPv1 SET with one or more OctetString varbinds. Returns error-status."""
    vbl = b''.join(tlv(0x30, enc_oid(o) + tlv(0x04, v)) for o, v in varbinds)
    pdu = tlv(0xa3, enc_int(2) + enc_int(0) + enc_int(0) + tlv(0x30, vbl))
    msg = tlv(0x30, enc_int(0) + tlv(0x04, community.encode()) + pdu)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    s.sendto(msg, (ip, 161))
    d = s.recv(65535)
    _, msgv, _ = dec(d)
    i = 0
    for _ in range(2):
        _, _, i = dec(msgv, i)
    _, pduv, _ = dec(msgv, i)
    _, _, j = dec(pduv, 0)
    _, err, j = dec(pduv, j)
    _, idx, _ = dec(pduv, j)
    return int.from_bytes(err, 'big'), int.from_bytes(idx, 'big')


def phoenix_set(ip, varbinds, user='Public', password='0000'):
    """Write OctetString OIDs via https://<ip>/phoenix/mib. Returns the status per OID."""
    body = json.dumps({'request': [{'key': o, 'string_value': v.decode()} for o, v in varbinds]}).encode()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # the printer's self-signed certificate
    pw = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    pw.add_password(None, f'https://{ip}/', user, password)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                         urllib.request.HTTPDigestAuthHandler(pw))
    req = urllib.request.Request(f'https://{ip}/phoenix/mib', body, {'Content-Type': 'application/json'})
    with opener.open(req, timeout=10) as r:
        return [it['status'] for it in json.load(r)['response']]


def local_ip_towards(ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((ip, 161))
    return s.getsockname()[0]


def build(name, func, host, port, appnum, duration, cc):
    return (f'TYPE=BR;BUTTON=SCAN;USER="{name}";FUNC={func};HOST={host}:{port};'
            f'APPNUM={appnum};DURATION={duration};BRID=;CC={cc};').encode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--printer', required=True)
    ap.add_argument('--name', default='SCANBRIDGE')
    ap.add_argument('--port', type=int, default=54935)
    ap.add_argument('--minutes', type=float, default=10)
    ap.add_argument('--community', default='internal')
    a = ap.parse_args()

    host = local_ip_towards(a.printer)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', a.port))
    sock.settimeout(1)

    duration = 360
    msgs = [(OID_REGISTER, build(a.name, f, host, a.port, n, duration, 1)) for n, f in enumerate(FUNCS, 1)]
    for o, m in msgs:
        print('SET', m.decode())

    def register():
        try:
            st = phoenix_set(a.printer, msgs)
            print('  phoenix: status', st)
            if not any(st):
                return True
        except Exception as e:
            print('  phoenix: error', e)
        for comm in (a.community, 'public'):
            try:
                err, idx = snmp_set(a.printer, msgs, comm)
            except socket.timeout:
                print(f'  community {comm!r}: timeout')
                continue
            print(f'  community {comm!r}: error-status={err} index={idx}')
            if err == 0:
                return True
        return False

    if not register():
        print('Registration failed.')
        return 1
    print(f'Listening on UDP {host}:{a.port} for {a.minutes} min. Select "{a.name}" in the printer\'s Scan menu.')

    log = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skey_events.log'), 'ab')
    end = time.time() + a.minutes * 60
    next_reg = time.time() + duration / 2
    try:
        while time.time() < end:
            if time.time() > next_reg:
                register()
                next_reg = time.time() + duration / 2
            try:
                data, src = sock.recvfrom(65535)
            except socket.timeout:
                continue
            ts = datetime.datetime.now().isoformat(timespec='seconds')
            print(f'\n[{ts}] {len(data)} bytes from {src}')
            print('  hex :', data.hex())
            print('  text:', data.decode('latin-1'))
            log.write(f'[{ts}] {src} {data.hex()}\n'.encode())
            log.flush()
    finally:
        try:
            print('Unregistered, status', phoenix_set(a.printer, [(OID_UNREGISTER, m) for _, m in msgs]))
        except Exception as e:
            print('Unregistration failed:', e)


if __name__ == '__main__':
    sys.exit(main())

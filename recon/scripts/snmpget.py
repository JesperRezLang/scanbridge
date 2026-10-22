"""Minimal dependency-free SNMPv1/v2c GET/GETNEXT. Usage: snmpget.py <ip> <oid> [next] [community]"""
import socket, sys

def enc_len(n):
    if n < 0x80: return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, 'big'); return bytes([0x80 | len(b)]) + b
def tlv(t, v): return bytes([t]) + enc_len(len(v)) + v
def enc_int(i): return tlv(0x02, i.to_bytes(max(1, (i.bit_length() + 8) // 8), 'big', signed=True))
def enc_oid(oid):
    p = [int(x) for x in oid.split('.')]; out = bytes([40 * p[0] + p[1]])
    for n in p[2:]:
        s = [n & 0x7f]; n >>= 7
        while n: s.insert(0, 0x80 | (n & 0x7f)); n >>= 7
        out += bytes(s)
    return tlv(0x06, out)
def dec(b, i=0):
    t = b[i]; l = b[i + 1]; i += 2
    if l & 0x80: k = l & 0x7f; l = int.from_bytes(b[i:i + k], 'big'); i += k
    return t, b[i:i + l], i + l
def dec_oid(v):
    p = [v[0] // 40, v[0] % 40]; n = 0
    for c in v[1:]:
        n = (n << 7) | (c & 0x7f)
        if not c & 0x80: p.append(n); n = 0
    return '.'.join(map(str, p))
def request(ip, oid, pdu=0xa0, community='public', version=1):
    vb = tlv(0x30, tlv(0x30, enc_oid(oid) + b'\x05\x00'))
    msg = tlv(0x30, enc_int(version) + tlv(0x04, community.encode()) + tlv(pdu, enc_int(1) + enc_int(0) + enc_int(0) + vb))
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3); s.sendto(msg, (ip, 161))
    d = s.recv(65535)
    _, msgv, _ = dec(d); i = 0
    for _ in range(2): _, _, i = dec(msgv, i)
    _, pduv, _ = dec(msgv, i); j = 0
    _, _, j = dec(pduv, j); _, err, j = dec(pduv, j); _, _, j = dec(pduv, j)
    _, vbl, _ = dec(pduv, j); _, vb1, _ = dec(vbl); _, o, k = dec(vb1); t, val, _ = dec(vb1, k)
    return int.from_bytes(err, 'big'), dec_oid(o), t, val

if __name__ == '__main__':
    ip, oid = sys.argv[1], sys.argv[2]
    nxt = len(sys.argv) > 3 and sys.argv[3] == 'next'
    comm = sys.argv[4] if len(sys.argv) > 4 else 'public'
    for _ in range(40 if nxt else 1):
        err, o, t, val = request(ip, oid, 0xa1 if nxt else 0xa0, comm)
        if err or (nxt and not o.startswith(sys.argv[2])): print('err' if err else 'end', err, o); break
        print(o, hex(t), val.decode('latin-1') if t == 4 else val.hex()); oid = o

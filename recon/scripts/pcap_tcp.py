"""Reassemble TCP streams from a pcapng (Ethernet/IPv4) and write them to <outdir>/<flow>.bin
plus a timeline with the start of each segment. pcap_tcp.py <file> <outdir> [port]"""
import os, struct, sys
path, out = sys.argv[1], sys.argv[2]
port = int(sys.argv[3]) if len(sys.argv) > 3 else None
os.makedirs(out, exist_ok=True)
d = open(path, 'rb').read(); i = 0
segs = {}; seen = set(); events = []
while i + 12 <= len(d):
    bt, bl = struct.unpack_from('<II', d, i)
    if bl < 12: break
    body = d[i + 8:i + bl - 4]; i += bl
    if bt != 6: continue
    _, th, tl, cap, _ = struct.unpack_from('<IIIII', body)
    pkt = body[20:20 + cap]; ts = ((th << 32) | tl) / 1e6
    if len(pkt) < 54 or pkt[12:14] != b'\x08\x00': continue
    ip = pkt[14:]; ihl = (ip[0] & 15) * 4; tot = struct.unpack_from('>H', ip, 2)[0]
    if ip[9] != 6: continue
    src = '.'.join(map(str, ip[12:16])); dst = '.'.join(map(str, ip[16:20]))
    sp, dp, seq = struct.unpack_from('>HHI', ip, ihl); off = (ip[ihl + 12] >> 4) * 4; flags = ip[ihl + 13]
    if port and port not in (sp, dp): continue
    data = ip[ihl + off:tot]
    key = (src, sp, dst, dp, seq, len(data), flags)
    if key in seen: continue
    seen.add(key)
    flow = f'{src}_{sp}-{dst}_{dp}'
    if flags & 2: events.append((ts, flow, 'SYN')); segs.setdefault(flow, {})['isn'] = seq + 1
    if data:
        segs.setdefault(flow, {}).setdefault('d', {})[seq] = data
        events.append((ts, flow, data[:80]))
    if flags & 1: events.append((ts, flow, 'FIN'))
for flow, s in segs.items():
    if 'd' not in s: continue
    base = s.get('isn', min(s['d'])); buf = bytearray()
    for seq in sorted(s['d']):
        o = (seq - base) & 0xffffffff
        if o > len(buf): buf += b'\0' * (o - len(buf))
        buf[o:o + len(s['d'][seq])] = s['d'][seq]
    open(os.path.join(out, flow + '.bin'), 'wb').write(buf)
    print(f'{flow}: {len(buf)} bytes')
t0 = events[0][0] if events else 0
with open(os.path.join(out, 'timeline.txt'), 'w', encoding='utf-8') as f:
    for ts, flow, x in sorted(events):
        f.write(f'{ts - t0:9.3f} {flow} {x!r}\n')

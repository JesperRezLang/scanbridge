"""List UDP packets in a pcapng (Ethernet/IPv4). pcap_udp.py <file> [port...]"""
import struct, sys
path, ports = sys.argv[1], {int(p) for p in sys.argv[2:]}
d = open(path, 'rb').read(); i = 0; seen = set(); tsres = 1e-6
while i + 12 <= len(d):
    bt, bl = struct.unpack_from('<II', d, i)
    if bl < 12: break
    body = d[i + 8:i + bl - 4]; i += bl
    if bt != 6: continue
    _, th, tl, cap, _ = struct.unpack_from('<IIIII', body)
    pkt = body[20:20 + cap]; ts = ((th << 32) | tl) * tsres
    if len(pkt) < 42 or pkt[12:14] != b'\x08\x00': continue
    ihl = (pkt[14] & 15) * 4; ip = pkt[14:]
    if ip[9] != 17: continue
    src = '.'.join(map(str, ip[12:16])); dst = '.'.join(map(str, ip[16:20]))
    sp, dp, ul = struct.unpack_from('>HHH', ip, ihl)
    payload = ip[ihl + 8:ihl + ul]
    if ports and sp not in ports and dp not in ports: continue
    key = (src, dst, sp, dp, payload)
    if key in seen: continue
    seen.add(key)
    print(f'{ts:.3f} {src}:{sp} -> {dst}:{dp} {len(payload)}B')
    print('   ', payload.decode('latin-1'))
    print('   ', payload.hex())

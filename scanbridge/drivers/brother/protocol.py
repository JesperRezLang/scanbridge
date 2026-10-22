"""Brother's scan protocol on TCP 54921, used by iPrint&Scan after a button press.

Mapped from a packet capture of iPrint&Scan talking to a DCP-L2620DW. Commands are text:
ESC <name> \\n [KEY=VALUE\\n ...] 0x80. Replies are framed as 00 <3 letters> <length u32 LE> <data>,
except the reply to ESC Q (see read_q). After ESC XSC the image arrives in blocks:
  14 byte header (byte 1 = 0x02, length u16 LE in bytes 6-7) + data, then an end record (byte 1 = 0x21).
"""
import socket
import struct

PORT = 54921
AREA_300 = (0, 0, 2409, 3437)  # the whole A4 flatbed in pixels at 300 dpi, as iPrint&Scan sends it


class BrotherScanError(Exception):
    pass


def _cmd(name, params=None):
    body = ''.join(f'{k}={v}\n' for k, v in (params or {}).items())
    return b'\x1b' + name.encode() + b'\n' + body.encode() + b'\x80'


class _Conn:
    def __init__(self, ip, timeout):
        self.s = socket.create_connection((ip, PORT), timeout=timeout)

    def recv_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise BrotherScanError(f'connection closed ({len(buf)}/{n} bytes)')
            buf += chunk
        return bytes(buf)

    def send(self, name, params=None):
        self.s.sendall(_cmd(name, params))

    def read_q(self):
        # c1 00 <n> ...: n bytes counted from the length byte on (75 bytes in total on the DCP-L2620DW)
        head = self.recv_exact(3)
        return head + self.recv_exact(head[2] - 1)

    def read_frame(self, expect):
        head = self.recv_exact(8)
        name = head[1:4].decode('ascii', 'replace')
        if head[0] != 0 or name != expect:
            raise BrotherScanError(f'expected reply {expect}, got {head.hex()}')
        return self.recv_exact(struct.unpack_from('<I', head, 4)[0])


def scan(ip, dpi=300, color='C24BIT', area=AREA_300, timeout=60):
    """Scan one page from the flatbed and return it as JPEG bytes.

    Must be called shortly after the button press has been acknowledged, while the panel shows "Connecting".
    `area` is in pixels at `dpi`; `color` is the CLR value (C24BIT = colour, GRAY64 = 8-bit grayscale).
    """
    c = _Conn(ip, timeout)
    try:
        greeting = c.recv_exact(9)
        if not greeting.startswith(b'+OK 200'):
            raise BrotherScanError(f'unexpected greeting {greeting!r}')
        c.send('Q')
        c.read_q()
        c.send('QDI')
        c.read_frame('QDI')
        c.send('GKP')
        c.read_frame('GKP')
        c.send('SSP', {
            'OS': 'WIN', 'PSRC': 'AUTO', 'RESO': f'{dpi},{dpi}', 'CLR': color, 'AREA': 'NORMAL',
            'MRGN': '0,0,0,0', 'DPLX': 'OFF', 'BRIT': 50, 'CONT': 50, 'COMP': 'JPEG', 'JSF': 420,
            'IPRC': 'NORMAL', 'PTYPE': 'NORMAL', 'PAGE': 0, 'LONG': 'OFF', 'CARR': 'OFF', 'RMGC': 'OFF',
            'DTDF': 'OFF', 'DT4V': 'OFF', 'DSKW': 'OFF', 'LSMD': 'OFF', 'RMBP': 'OFF', 'RMMR': 'OFF',
            'GMMA': 'OFF', 'TONE': 'OFF', 'QTFD': 'OFF', 'ATCN': 'OFF', 'ATCRP': 'OFF'})
        c.read_frame('SSP')
        c.send('GCP')
        c.read_frame('GCP')  # ICC profile, not used
        c.send('XSC', {'RESO': f'{dpi},{dpi}', 'AREA': ','.join(map(str, area)), 'MODE': 'NORMAL'})

        c.s.settimeout(timeout)
        jpeg = bytearray()
        while True:
            head = c.recv_exact(2)
            if head[1] != 0x02:
                break  # end record (0x21 = page done); the rest is ignored
            head += c.recv_exact(12)
            jpeg += c.recv_exact(struct.unpack_from('<H', head, 6)[0])
        if not jpeg.startswith(b'\xff\xd8'):
            raise BrotherScanError('no JPEG in the reply')
        return bytes(jpeg)
    finally:
        c.s.close()

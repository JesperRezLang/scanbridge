"""Wrap a JPEG in a single-page PDF without re-encoding it."""
import struct


def jpeg_size(jpeg):
    """Return (width, height, components) from the JPEG's SOF marker."""
    i = 2
    while i + 4 <= len(jpeg):
        marker, length = jpeg[i + 1], struct.unpack_from('>H', jpeg, i + 2)[0]
        if marker in (0xc0, 0xc1, 0xc2):
            h, w = struct.unpack_from('>HH', jpeg, i + 5)
            return w, h, jpeg[i + 9]
        i += 2 + length
    raise ValueError('no SOF marker in JPEG')


def jpeg_to_pdf(jpeg, dpi):
    """One page, the image embedded unchanged (DCTDecode)."""
    w, h, comps = jpeg_size(jpeg)
    pw, ph = w * 72 / dpi, h * 72 / dpi
    cs = '/DeviceRGB' if comps == 3 else '/DeviceGray'
    content = f'q {pw:.2f} 0 0 {ph:.2f} 0 0 cm /Im0 Do Q'.encode()
    objs = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.2f} {ph:.2f}] '
        f'/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>'.encode(),
        f'<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace {cs} '
        f'/BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n'.encode() + jpeg + b'\nendstream',
        f'<< /Length {len(content)} >>\nstream\n'.encode() + content + b'\nendstream',
    ]
    out = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f'{n} 0 obj\n'.encode() + body + b'\nendobj\n'
    xref = len(out)
    out += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode()
    out += b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    out += f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return bytes(out)

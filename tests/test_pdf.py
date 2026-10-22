import re
import struct
import unittest

from scanbridge import pdf


def make_jpeg(width, height, components=3, sof=0xC0):
    """A minimal JPEG skeleton: SOI, APP0, SOF, EOI (not decodable, only parseable)."""
    app0 = b'\xff\xe0' + struct.pack('>H', 16) + b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    comps = b''.join(bytes([i + 1, 0x11, 0]) for i in range(components))
    sof_seg = bytes([0xFF, sof]) + struct.pack('>H', 8 + 3 * components) + b'\x08' + struct.pack('>HH', height, width)
    sof_seg += bytes([components]) + comps
    return b'\xff\xd8' + app0 + sof_seg + b'\xff\xd9'


class JpegSizeTest(unittest.TestCase):
    def test_rgb(self):
        self.assertEqual(pdf.jpeg_size(make_jpeg(640, 480)), (640, 480, 3))

    def test_grayscale(self):
        self.assertEqual(pdf.jpeg_size(make_jpeg(100, 200, 1)), (100, 200, 1))

    def test_large_dimensions(self):
        self.assertEqual(pdf.jpeg_size(make_jpeg(2409, 3437)), (2409, 3437, 3))

    def test_sof1_and_sof2(self):
        self.assertEqual(pdf.jpeg_size(make_jpeg(10, 20, 3, 0xC1)), (10, 20, 3))
        self.assertEqual(pdf.jpeg_size(make_jpeg(10, 20, 3, 0xC2)), (10, 20, 3))

    def test_skips_several_segments(self):
        app1 = b'\xff\xe1' + struct.pack('>H', 10) + b'Exif\x00\x00\x00\x00'
        jpeg = make_jpeg(30, 40)
        jpeg = jpeg[:2] + app1 + jpeg[2:]
        self.assertEqual(pdf.jpeg_size(jpeg), (30, 40, 3))

    def test_no_sof_raises(self):
        app0 = b'\xff\xe0' + struct.pack('>H', 16) + b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
        with self.assertRaises(ValueError):
            pdf.jpeg_size(b'\xff\xd8' + app0 + b'\xff\xd9')

    def test_empty_and_garbage_raise(self):
        for data in (b'', b'\xff\xd8', b'not a jpeg at all'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                pdf.jpeg_size(data)


class JpegToPdfTest(unittest.TestCase):
    def setUp(self):
        self.jpeg = make_jpeg(600, 300)
        self.out = pdf.jpeg_to_pdf(self.jpeg, 300)

    def test_header_and_trailer(self):
        self.assertTrue(self.out.startswith(b'%PDF-1.4\n'))
        self.assertTrue(self.out.endswith(b'%%EOF\n'))

    def test_contains_jpeg_unchanged(self):
        self.assertEqual(self.out.count(self.jpeg), 1)
        self.assertIn(b'/Filter /DCTDecode', self.out)
        self.assertIn(f'/Length {len(self.jpeg)}'.encode(), self.out)
        self.assertIn(b'/Width 600 /Height 300', self.out)

    def test_startxref_points_at_xref(self):
        m = re.search(rb'startxref\n(\d+)\n%%EOF', self.out)
        self.assertIsNotNone(m)
        self.assertEqual(self.out[int(m.group(1)):int(m.group(1)) + 5], b'xref\n')

    def test_xref_entries_point_at_objects(self):
        xref = int(re.search(rb'startxref\n(\d+)\n', self.out).group(1))
        lines = self.out[xref:].split(b'\n')
        self.assertEqual(lines[1], b'0 6')
        self.assertEqual(lines[2], b'0000000000 65535 f ')
        for n in range(1, 6):
            entry = lines[2 + n]
            self.assertEqual(len(entry), 19)  # 20 bytes with the newline
            offset = int(entry[:10])
            self.assertEqual(self.out[offset:offset + len(f'{n} 0 obj')], f'{n} 0 obj'.encode())

    def test_trailer_size_and_root(self):
        self.assertIn(b'trailer\n<< /Size 6 /Root 1 0 R >>', self.out)

    def test_media_box_matches_dpi(self):
        m = re.search(rb'/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]', self.out)
        self.assertEqual((float(m.group(1)), float(m.group(2))), (600 * 72 / 300, 300 * 72 / 300))

    def test_media_box_other_dpi(self):
        out = pdf.jpeg_to_pdf(make_jpeg(1200, 600), 150)
        m = re.search(rb'/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]', out)
        self.assertEqual((float(m.group(1)), float(m.group(2))), (576.0, 288.0))
        self.assertIn(b'q 576.00 0 0 288.00 0 0 cm /Im0 Do Q', out)

    def test_rgb_color_space(self):
        self.assertIn(b'/ColorSpace /DeviceRGB', self.out)
        self.assertNotIn(b'/DeviceGray', self.out)

    def test_grayscale_color_space(self):
        out = pdf.jpeg_to_pdf(make_jpeg(100, 100, 1), 300)
        self.assertIn(b'/ColorSpace /DeviceGray', out)
        self.assertNotIn(b'/DeviceRGB', out)

    def test_content_stream_length_is_correct(self):
        m = re.search(rb'<< /Length (\d+) >>\nstream\n(q .*? Q)\nendstream', self.out)
        self.assertEqual(int(m.group(1)), len(m.group(2)))

    def test_invalid_jpeg_raises(self):
        with self.assertRaises(ValueError):
            pdf.jpeg_to_pdf(b'\xff\xd8\xff\xd9', 300)


if __name__ == '__main__':
    unittest.main()

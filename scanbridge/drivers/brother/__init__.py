"""Brother network scanners that support "Scan to PC" (tested: DCP-L2620DW)."""
from . import phoenix, protocol, push
from .push import FUNCTIONS, UDP_PORT, parse_event

COLORS = {'color': 'C24BIT', 'gray': 'GRAY64'}  # the CLR values; black & white returns a raw format, not JPEG


class BrotherDriver:
    udp_port = UDP_PORT
    parse_event = staticmethod(parse_event)

    def __init__(self, printer):
        self.printer = printer

    def register(self, names, advertise_ip):
        """Register every name for every panel function. Returns the status per registration (0 = ok)."""
        p = self.printer
        items = [(push.OID_REGISTER, push.registration_string(name, func, advertise_ip, appnum, p.register_duration))
                 for name in names for appnum, func in enumerate(FUNCTIONS, 1)]
        return phoenix.mib_set(p.host, p.username, p.password, items)

    def scan(self, ip, profile):
        """Scan one page with the dpi and color of `profile` and return it as JPEG bytes."""
        area = tuple(round(v * profile.dpi / 300) for v in protocol.AREA_300)
        return protocol.scan(ip, dpi=profile.dpi, color=COLORS[profile.color], area=area)


__all__ = ['BrotherDriver', 'FUNCTIONS', 'UDP_PORT', 'parse_event']

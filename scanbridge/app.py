"""The service: keep the names registered on the printers, answer button presses, scan and deliver the file.

1. Registers each target's name in the printer's "Scan to PC" list and renews it before it expires.
2. Receives the button press on UDP 54925 and acknowledges it by sending the same packet back.
3. Scans with the printer's own protocol (eSCL does not work here: while the panel shows "Connecting" the
   printer answers 500 to eSCL jobs).
4. Stores the file in the target's destinations; failures are queued and retried (delivery.py).
"""
import argparse
import datetime
import logging
import os
import re
import signal
import socket
import sys
import threading

from . import __version__, config, destinations, drivers, pdf
from .delivery import Delivery, private_dir
from .destinations.local import unique_path

log = logging.getLogger('scanbridge')


def local_ip_towards(ip):
    """The address of this host on the route to `ip`."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((ip, 161))
        return s.getsockname()[0]


def make_filename(target, printer, func, ext, now=None):
    name = target.filename.format(date=now or datetime.datetime.now(), target=target.name,
                                  function=func.lower(), printer=printer.name)
    name = re.sub(r'[\\/\x00-\x1f]', '_', name).strip() or 'Scan'
    return f'{name}.{ext}'


class PrinterRuntime:
    def __init__(self, printer):
        self.cfg = printer
        self.driver = drivers.DRIVERS[printer.driver](printer)
        self.targets = {t.name: t for t in printer.targets}
        self.ip = None
        self.busy = threading.Lock()
        self.last_event = None


class Service:
    def __init__(self, cfg):
        self.cfg = cfg
        self.delivery = Delivery({n: destinations.create(d) for n, d in cfg.destinations.items()},
                                 os.path.join(cfg.data_dir, 'queue'))
        self.printers = [PrinterRuntime(p) for p in cfg.printers]
        self.stop = threading.Event()

    def register_loop(self, rt):
        p, names, registered = rt.cfg, list(rt.targets), False
        while not self.stop.is_set():
            try:
                rt.ip = socket.gethostbyname(p.host)
                advertise = p.advertise_ip or local_ip_towards(rt.ip)
                status = rt.driver.register(names, advertise)
                if any(status):
                    log.warning('Registration on %s: status %s', p.name, status)
                elif not registered:
                    log.info('Registered %s on %s, button presses go to %s:%d',
                             ', '.join(f'"{n}"' for n in names), p.name, advertise, rt.driver.udp_port)
                registered = not any(status)
                wait = p.register_duration / 2
            except Exception as e:
                log.warning('Registration on %s failed: %s', p.name, e)
                registered, wait = False, 30
            self.stop.wait(wait)

    def on_packet(self, sock, data, src):
        rt = next((r for r in self.printers if r.ip == src[0]), None)
        if rt is None:
            log.debug('Ignoring packet from unknown host %s', src[0])
            return
        sock.sendto(data, src)  # acknowledge: the same packet back
        ev = rt.driver.parse_event(data)
        key = (ev.get('REGID'), ev.get('SEQ'))
        if key == rt.last_event:
            return  # retransmission
        rt.last_event = key
        target = rt.targets.get(ev.get('USER'))
        if target is None:
            log.info('Ignoring button press on %s for unknown name %r', rt.cfg.name, ev.get('USER'))
            return
        if not rt.busy.acquire(blocking=False):
            log.warning('A scan on %s is already running, ignoring button press', rt.cfg.name)
            return
        threading.Thread(target=self.scan, args=(rt, target, ev), daemon=True).start()

    def scan(self, rt, target, ev):
        try:
            func = ev.get('FUNC', 'FILE')
            profile = target.profiles.get(func) or target.profiles['FILE']
            log.info('Scan button on %s: "%s", %s, profile %s (P1-P4 = %s)', rt.cfg.name, target.name, func,
                     profile, [ev.get(f'P{i}') for i in range(1, 5)])
            jpeg = rt.driver.scan(rt.ip, profile)
            data = pdf.jpeg_to_pdf(jpeg, profile.dpi) if profile.format == 'pdf' else jpeg
            filename = make_filename(target, rt.cfg, func, profile.format)
            self.delivery.deliver(target.destinations, filename, data)
        except Exception:
            log.exception('Scan on %s failed', rt.cfg.name)
        finally:
            rt.busy.release()

    def migrate_legacy_queue(self):
        """Before 0.2 failed Seafile uploads were kept in <data_dir>/pending."""
        old = os.path.join(self.cfg.data_dir, 'pending')
        if not os.path.isdir(old) or 'seafile' not in self.cfg.destinations:
            return
        new = os.path.join(self.delivery.queue_dir, 'seafile')
        files = [f for f in os.listdir(old) if not f.endswith('.part')]
        if files:
            private_dir(self.delivery.queue_dir)
            private_dir(new)
            for f in files:
                os.replace(os.path.join(old, f), unique_path(new, f))
            log.info('Moved %d queued file(s) from %s to %s', len(files), old, new)

    def run(self):
        ports = {rt.driver.udp_port for rt in self.printers}
        socks = []
        for port in ports:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(('0.0.0.0', port))
            socks.append(s)
        self.migrate_legacy_queue()
        for rt in self.printers:
            threading.Thread(target=self.register_loop, args=(rt,), daemon=True).start()
        threading.Thread(target=self.delivery.retry_loop, args=(self.cfg.retry_interval, self.stop),
                         daemon=True).start()
        log.info('Listening on UDP %s', ', '.join(map(str, sorted(ports))))
        for s in socks[1:]:
            threading.Thread(target=self.listen, args=(s,), daemon=True).start()
        self.listen(socks[0])

    def listen(self, sock):
        while not self.stop.is_set():
            data, src = sock.recvfrom(4096)
            try:
                self.on_packet(sock, data, src)
            except Exception:
                log.exception('Handling a packet from %s failed', src[0])


def main(argv=None):
    ap = argparse.ArgumentParser(prog='scanbridge',
                                 description='Scan-to-PC button service for Brother printers.')
    ap.add_argument('--config', help='TOML configuration file (default: $SCANBRIDGE_CONFIG)')
    ap.add_argument('--check', action='store_true', help='validate the configuration, print it and exit')
    ap.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    args = ap.parse_args(argv)
    try:
        cfg = config.load(path=args.config)
    except config.ConfigError as e:
        print(f'Configuration error: {e}', file=sys.stderr)
        return 2

    logging.basicConfig(level=cfg.log_level, format='%(asctime)s %(levelname)s %(message)s')
    if cfg.legacy:
        log.warning('Deprecated environment variables %s, see the README for the SCANBRIDGE_ names',
                    ', '.join(cfg.legacy))
    for name in config.insecure_destinations(cfg):
        log.warning('Destination %s is not encrypted (http:// or FTP without TLS): credentials and scanned '
                    'documents are sent in clear text', name)
    if args.check:
        print('\n'.join(cfg.describe()))
        return 0
    log.info('scanbridge %s', __version__)
    for line in cfg.describe():
        log.info('%s', line)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # PID 1 in a container gets no default handler
    Service(cfg).run()
    return 0

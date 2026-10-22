"""Listen on UDP 54925 alongside the WIA service (SO_REUSEADDR) and log the packets."""
import datetime, os, socket, sys, time
port = int(sys.argv[1]) if len(sys.argv) > 1 else 54925
minutes = float(sys.argv[2]) if len(sys.argv) > 2 else 15
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('0.0.0.0', port))
s.settimeout(1)
print(f'Listening on UDP {port} for {minutes} min', flush=True)
log = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skey_events.log'), 'ab')
end = time.time() + minutes * 60
while time.time() < end:
    try:
        data, src = s.recvfrom(65535)
    except socket.timeout:
        continue
    ts = datetime.datetime.now().isoformat(timespec='seconds')
    print(f'\n[{ts}] {len(data)} bytes from {src}\n  hex : {data.hex()}\n  text: {data.decode("latin-1")}', flush=True)
    log.write(f'[{ts}] port{port} {src} {data.hex()}\n'.encode()); log.flush()

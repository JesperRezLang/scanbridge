"""The "Scan to PC" button: registration strings and the UDP packet the printer sends when it is pressed.

A host registers one string per panel function by writing it to OID_REGISTER. The registration expires after
DURATION seconds. When the user picks the name on the panel, the printer sends
  02 00 <n> 30 + 'TYPE=BR;BUTTON=SCAN;USER="<name>";FUNC=<func>;HOST=<ip>:<port>;APPNUM=<n>;...;REGID=<id>;SEQ=<n>;'
to UDP port 54925 on the registered host (the port in HOST= is ignored) and waits for the same packet back.
"""
import re

OID_REGISTER = '1.3.6.1.4.1.2435.2.3.9.2.11.1.1.0'
UDP_PORT = 54925

# Panel functions in registration order (APPNUM 1-4).
FUNCTIONS = ('FILE', 'IMAGE', 'OCR', 'EMAIL')


def registration_string(name, func, host_ip, appnum, duration):
    return (f'TYPE=BR;BUTTON=SCAN;USER="{name}";FUNC={func};HOST={host_ip}:{UDP_PORT};'
            f'APPNUM={appnum};DURATION={duration};BRID=;CC=1;')


def parse_event(data):
    """'02 00 <n> 30' + 'KEY=VAL;KEY="VAL";...' -> {'KEY': 'VAL', ...}"""
    text = data[4:].decode('utf-8', 'replace')
    return {k: v.strip('"') for k, v in re.findall(r'(\w+)=("[^"]*"|[^;]*);', text)}

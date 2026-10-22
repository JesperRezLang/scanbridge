"""Print ASCII and UTF-16LE strings matching a regex. strs.py <regex> <files...>"""
import re, sys
pat = re.compile(sys.argv[1], re.I)
for f in sys.argv[2:]:
    d = open(f, 'rb').read(); hits = set()
    for m in re.findall(rb'[\x20-\x7e]{4,}', d): hits.add(m.decode())
    for m in re.findall(rb'(?:[\x20-\x7e]\x00){4,}', d): hits.add(m.decode('utf-16le'))
    hits = sorted(h for h in hits if pat.search(h))
    if hits: print('==', f); [print('  ', h) for h in hits]

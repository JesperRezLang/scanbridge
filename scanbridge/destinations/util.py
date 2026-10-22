import itertools
import os


def candidate_names(filename):
    """`filename`, then `name (1).ext`, `name (2).ext` ... for as long as the caller asks."""
    stem, ext = os.path.splitext(filename)
    yield filename
    for n in itertools.count(1):
        yield f'{stem} ({n}){ext}'

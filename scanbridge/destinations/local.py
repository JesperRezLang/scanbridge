import os

from .base import REQUIRED, Destination
from .util import candidate_names


def write_atomic(path, data, mode=None):
    """Write via a .part file so that a folder watcher never sees half a file. `mode` sets the permissions."""
    part = path + '.part'
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with open(part, 'wb') if mode is None else os.fdopen(os.open(part, flags, mode), 'wb') as f:
        if mode is not None:
            os.fchmod(f.fileno(), mode)  # open() applies the umask and ignores the mode for an old .part file
        f.write(data)
    os.replace(part, path)


def unique_path(folder, filename):
    """`folder/filename`, or `name (1).ext`, `name (2).ext` ... if it is taken."""
    for name in candidate_names(filename):
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            return path


class LocalDestination(Destination):
    """A folder on disk. Also covers network shares (SMB, NFS) mounted into the container."""
    type = 'local'
    OPTIONS = {'path': REQUIRED}

    def store(self, filename, data):
        folder = self.options['path']
        os.makedirs(folder, exist_ok=True)
        path = unique_path(folder, filename)
        write_atomic(path, data)
        return path

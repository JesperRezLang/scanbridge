"""Hand finished scans to their destinations and keep the ones that failed for a later attempt.

A file that cannot be stored is written to <data_dir>/queue/<destination>/ and retried every `retry_interval`
seconds, oldest first, until it gets through.
"""
import logging
import os

from .destinations.local import unique_path, write_atomic

log = logging.getLogger(__name__)

QUEUE_DIR_MODE, QUEUE_FILE_MODE = 0o700, 0o600  # queued scans are private to the service's user


def private_dir(path):
    """Create `path` (and the missing parents) and make it accessible to the owner only."""
    os.makedirs(path, mode=QUEUE_DIR_MODE, exist_ok=True)
    try:
        os.chmod(path, QUEUE_DIR_MODE)  # makedirs applies the umask and leaves existing folders alone
    except OSError as e:
        log.debug('Cannot set permissions of %s: %s', path, e)


class Delivery:
    def __init__(self, destinations, queue_dir):
        self.destinations = destinations  # name -> Destination
        self.queue_dir = queue_dir

    def deliver(self, names, filename, data):
        for name in names:
            dest = self.destinations[name]
            try:
                where = dest.store(filename, data)
                log.info('Stored %s in %s: %s (%d bytes)', filename, dest, where, len(data))
            except Exception as e:
                log.error('Storing %s in %s failed (%s), queued for retry', filename, dest, e)
                self.enqueue(name, filename, data)

    def enqueue(self, name, filename, data):
        folder = os.path.join(self.queue_dir, name)
        private_dir(self.queue_dir)
        private_dir(folder)
        write_atomic(unique_path(folder, filename), data, QUEUE_FILE_MODE)

    def pending(self, name):
        folder = os.path.join(self.queue_dir, name)
        if not os.path.isdir(folder):
            return []
        files = [os.path.join(folder, f) for f in os.listdir(folder) if not f.endswith('.part')]
        return sorted(files, key=os.path.getmtime)

    def retry(self):
        for name, dest in self.destinations.items():
            for path in self.pending(name):
                filename = os.path.basename(path)
                try:
                    with open(path, 'rb') as f:
                        where = dest.store(filename, f.read())
                    os.remove(path)
                    log.info('Stored queued %s in %s: %s', filename, dest, where)
                except Exception as e:
                    log.warning('Retry of %s in %s failed: %s', filename, dest, e)
                    break  # the destination is still down, try again next round

    def retry_loop(self, interval, stop):
        while not stop.wait(interval):
            self.retry()

"""Upload to an FTP server, with explicit FTPS (AUTH TLS) by default.

`username` and `password` are the server's account. FTPS verifies the server certificate against the system's CA
certificates, so a self-signed certificate is refused: use a certificate from a CA, or set `tls = false` on a
trusted network only. The folder `directory` must exist; it is not created.
"""
import ftplib
import io
import itertools
import posixpath
import ssl

from .base import REQUIRED, Destination, is_loopback
from .util import candidate_names

MAX_NAMES = 100  # file names to try before giving up


class FtpDestination(Destination):
    type = 'ftp'
    OPTIONS = {'host': REQUIRED, 'port': 21, 'username': 'anonymous', 'password': '', 'directory': '/', 'tls': True,
               'passive': True}
    SECRETS = {'password'}

    @classmethod
    def insecure(cls, options):
        return not options.get('tls', True) and not is_loopback(options.get('host'))

    def _connect(self):
        o = self.options
        if o['tls']:
            ftp = ftplib.FTP_TLS(context=ssl.create_default_context(), timeout=30, encoding='utf-8')
        else:
            ftp = ftplib.FTP(timeout=30, encoding='utf-8')
        try:
            ftp.connect(o['host'], o['port'])
            ftp.login(o['username'], o['password'])
            if o['tls']:
                ftp.prot_p()  # encrypt the data channel too
            ftp.set_pasv(o['passive'])
            ftp.cwd(o['directory'])
        except BaseException:
            ftp.close()
            raise
        return ftp

    def store(self, filename, data):
        o = self.options
        ftp = self._connect()
        try:
            try:
                existing = {posixpath.basename(n) for n in ftp.nlst()}
            except ftplib.error_perm as e:
                if not str(e).startswith('550'):  # "550 No files found" is how some servers report an empty folder
                    raise
                existing = set()
            for name in itertools.islice(candidate_names(filename), MAX_NAMES):
                if name not in existing:
                    break
            else:
                raise OSError(f'No free file name for {filename} in {o["directory"]} after {MAX_NAMES} attempts')
            part = name + '.part'
            ftp.timeout = 120  # applies to the data connection; the control connection is set directly
            ftp.sock.settimeout(120)
            try:
                ftp.storbinary(f'STOR {part}', io.BytesIO(data))
                ftp.rename(part, name)  # a folder watcher never sees half a file
            except BaseException:
                try:
                    ftp.delete(part)
                except (OSError, ftplib.Error):
                    pass
                raise
            return f'ftp://{o["host"]}{posixpath.join("/", o["directory"].strip("/"), name)}'
        finally:
            try:
                ftp.quit()
            except (OSError, EOFError, ftplib.Error):
                pass
            finally:
                ftp.close()

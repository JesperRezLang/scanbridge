"""Destination types, looked up by the `type` setting of a destination."""
from .base import REQUIRED, Destination
from .ftp import FtpDestination
from .local import LocalDestination
from .paperless import PaperlessDestination
from .seafile import SeafileDestination
from .webdav import WebdavDestination

TYPES = {cls.type: cls for cls in (LocalDestination, SeafileDestination, WebdavDestination, PaperlessDestination,
                                   FtpDestination)}


def create(cfg):
    """Build a destination from a config.DestinationConfig."""
    return TYPES[cfg.type](cfg.name, **cfg.options)


__all__ = ['REQUIRED', 'TYPES', 'Destination', 'create']

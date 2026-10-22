"""Printer drivers, looked up by the `driver` setting of a printer."""
from .brother import BrotherDriver

DRIVERS = {'brother': BrotherDriver}

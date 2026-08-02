"""
Minimal base class for INDI wrapper/device classes.

Distant heritage: this package (hardware_indi/) originates from the
PANOPTES/POCS observatory control software (github.com/panoptes/POCS).
The original Base class loaded a POCS-specific config file and opened a
MongoDB connection on every instantiation - both removed here, since
Rosace has its own config (config.py) and databases (database.py,
targets.py) that have nothing to do with POCS's.
"""

import logging


class Base:
    """Common base for INDI wrapper/device classes: just a logger."""

    def __init__(self, *args, **kwargs):
        self.logger = kwargs.get("logger") or logging.getLogger(self.__class__.__name__)

"""
Exceptions used by the INDI hardware wrapper.

Distant heritage: trimmed down from the PANOPTES/POCS error hierarchy
(github.com/panoptes/POCS) - the AstropyWarning base class and the
custom logging setup it used to trigger on import (a call to
initLogger() that reconfigured Python's whole logging system) have been
removed, since they conflicted with Rosace's own centralized logging
(see log_config.py).
"""


class BLOBError(Exception):
    """Error for INDI BLOB (e.g. image) communication problems."""


class IndiClientPredicateTimeoutError(Exception):
    """Error while waiting for an INDI predicate to become true."""


class ImageAcquisitionError(Exception):
    """Error while trying to acquire an image."""

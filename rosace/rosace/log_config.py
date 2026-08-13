"""
Configuration centralisée du logging applicatif (console + fichier tournant).
Point d'entrée unique : configure_logging(), appelé au tout début de main.py.
"""

from __future__ import annotations

import logging.config
import time
from pathlib import Path

from .config import LOG_DIR

_CFG_PATH = Path(__file__).parent / "conf" / "logging.cfg"


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.config.fileConfig(
        _CFG_PATH,
        disable_existing_loggers=False,
        defaults={"logfilename": str(LOG_DIR / "rosace.log")},
    )
    # logging.Formatter defaults %(asctime)s to local time; the rest of the app
    # (DB timestamps, FITS filenames, the astronomical-day boundary) is UTC, so
    # logs must be too for cross-referencing to work.
    logging.Formatter.converter = time.gmtime

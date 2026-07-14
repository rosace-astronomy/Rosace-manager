"""
Configuration centralisée du logging applicatif (console + fichier tournant).
Point d'entrée unique : configure_logging(), appelé au tout début de main.py.
"""

from __future__ import annotations

import logging.config
from pathlib import Path

from .config import BASE_DATA_DIR

_CFG_PATH = Path(__file__).parent / "conf" / "logging.cfg"


def configure_logging() -> None:
    log_dir = BASE_DATA_DIR / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.config.fileConfig(
        _CFG_PATH,
        disable_existing_loggers=False,
        defaults={"logfilename": str(log_dir / "rosace.log")},
    )

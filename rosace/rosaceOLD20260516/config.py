"""
Configuration globale : chemins et constantes.
"""

import logging
from pathlib import Path

# ── Répertoires ───────────────────────────────────────────────
BASE_DATA_DIR = Path("/tmp/rosace")
DB_PATH       = BASE_DATA_DIR / "rosace.db"

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

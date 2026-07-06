"""
Configuration globale : chemins et constantes.
"""

import logging
from pathlib import Path

# ── Répertoires ───────────────────────────────────────────────
BASE_DATA_DIR = Path("/tmp/rosace")
DB_PATH       = BASE_DATA_DIR / "rosace.db"

# ── Programme scientifique actif ──────────────────────────────
# Nom du fichier YAML dans le répertoire programmes/ (sans extension).
# Ce programme définit les pipelines séquenceur et réduction de la session.
# Valeurs disponibles : etoiles_be | etoiles_brillantes | nova_monitoring
ACTIVE_PROGRAMME = "etoiles_be"

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

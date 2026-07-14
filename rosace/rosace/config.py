"""
Configuration globale : chemins et constantes.
"""

from pathlib import Path

# ── Répertoires ───────────────────────────────────────────────
BASE_DATA_DIR = Path("/home/observatoire/.rosace")
DB_PATH       = BASE_DATA_DIR / "databases" / "rosace.db"

# ── Site d'observation ─────────────────────────────────────────
# Nom générique utilisé pour nommer les répertoires de session.
SITE_NAME = "St-Pancrasse"

# ── Programme scientifique actif ──────────────────────────────
# Nom du fichier YAML dans le répertoire programmes/ (sans extension).
# Ce programme définit les pipelines séquenceur et réduction de la session.
# Valeurs disponibles : etoiles_be | etoiles_brillantes | nova_monitoring
ACTIVE_PROGRAMME = "etoiles_be"

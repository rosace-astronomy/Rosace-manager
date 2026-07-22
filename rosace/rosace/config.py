"""
Configuration globale : charge conf/config.yaml et expose les constantes de l'application.
"""

from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).parent / "conf" / "config.yaml"


def _load_config(path: Path = _CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


_cfg = _load_config()

# ── Répertoires ───────────────────────────────────────────────
BASE_DATA_DIR = Path(_cfg["base_data_dir"]).expanduser()
DATABASES_DIR = BASE_DATA_DIR / _cfg["databases_dir"]
DB_PATH       = DATABASES_DIR / "rosace.db"

# ── Site d'observation ─────────────────────────────────────────
# Nom générique utilisé pour nommer les répertoires de session.
SITE_NAME = _cfg["site_name"]

# ── Programme scientifique actif ──────────────────────────────
# Nom du fichier YAML dans le répertoire programmes/ (sans extension).
# Ce programme définit les pipelines séquenceur et réduction de la session.
# Valeurs disponibles : etoiles_be | etoiles_brillantes | nova_monitoring
ACTIVE_PROGRAMME = _cfg["active_programme"]

# ── Configuration matérielle active ────────────────────────────
# Nom du fichier YAML dans conf/ (sans extension) décrivant le hardware à utiliser.
# Permet de basculer entre plusieurs configurations (simulateur, observatoire réel…)
# en changeant uniquement cette valeur.
ACTIVE_HARDWARE = _cfg["active_hardware"]
HARDWARE_CONFIG_PATH = Path(__file__).parent / "conf" / f"{ACTIVE_HARDWARE}.yaml"

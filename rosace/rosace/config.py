"""
Configuration globale : charge conf/config.yaml et expose les constantes de l'application.
"""

import logging
import logging.config
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

# ── Configuration matérielle active ────────────────────────────
# Nom du fichier YAML dans conf/ (sans extension) décrivant le hardware à utiliser.
# Permet de basculer entre plusieurs configurations (simulateur, observatoire réel…)
# en changeant uniquement cette valeur.
ACTIVE_HARDWARE = _cfg["active_hardware"]
HARDWARE_CONFIG_PATH = Path(__file__).parent / "conf" / f"{ACTIVE_HARDWARE}.yaml"

# ── Ordonnancement ──────────────────────────────────────────────
# Délai avant de redemander une cible à SCHEDULER quand il n'y a rien à
# observer dans l'immédiat (SCHEDULER répond toujours vite, il ne bloque
# jamais en interne — c'est l'orchestrateur qui gère l'attente).
SCHEDULER_RETRY_SECONDS = _cfg["scheduler_retry_seconds"]

# ── Backend d'exécution SEQUENCER ────────────────────────────────
# "internal" : simulation interne, sans dépendance externe (démonstrateur niveau 1).
# "indi"     : protocole INDI/PHD2 réel, piloté par ACTIVE_HARDWARE/HARDWARE_CONFIG_PATH
#              (démonstrateur niveaux 2/3). Ne concerne que les sous-plugins SEQUENCER
#              (slew/focus/expose) — REDUCTION n'a pas de dépendance matérielle.
_VALID_SEQUENCER_BACKENDS = ("internal", "indi")

_LOGGING_CFG_PATH = Path(__file__).parent / "conf" / "logging.cfg"


def _validate_sequencer_backend(value: str) -> str:
    if value not in _VALID_SEQUENCER_BACKENDS:
        raise ValueError(
            f"conf/config.yaml: sequencer_backend={value!r} invalide "
            f"(valeurs acceptées : {_VALID_SEQUENCER_BACKENDS})"
        )
    return value


def _log_fatal_startup_error(message: str) -> None:
    """
    Filet de secours : configure le vrai système de logging (même conf/logging.cfg
    qu'en fonctionnement normal) pour qu'une erreur de config levée ici — avant que
    configure_logging() (log_config.py) n'ait pu tourner, puisqu'elle a elle-même
    besoin de BASE_DATA_DIR — atterrisse quand même dans rosace.log, pas seulement
    sur stderr. Ne fait pas appel à log_config.py directement pour éviter un import
    circulaire (log_config.py fait `from .config import BASE_DATA_DIR`).
    """
    try:
        log_dir = BASE_DATA_DIR / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.config.fileConfig(
            _LOGGING_CFG_PATH,
            disable_existing_loggers=False,
            defaults={"logfilename": str(log_dir / "rosace.log")},
        )
        logging.getLogger("spectro.config").error(message)
    except Exception:
        pass  # best effort — ne jamais masquer l'erreur d'origine


try:
    SEQUENCER_BACKEND = _validate_sequencer_backend(_cfg["sequencer_backend"])
except ValueError as e:
    _log_fatal_startup_error(str(e))
    raise

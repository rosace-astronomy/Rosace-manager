"""
Global configuration: loads conf/config.yaml and exposes the application's constants.
"""

import logging
import logging.config
import os
from pathlib import Path

import yaml

_PACKAGE_CONF_DIR = Path(__file__).parent / "conf"
_DEFAULT_CONFIG_PATH = _PACKAGE_CONF_DIR / "config.yaml"
_BASE_DATA_DIR_FILE = _PACKAGE_CONF_DIR / "base_data_dir.yaml"


def _load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_config_path() -> Path:
    """
    Which config.yaml to load, in priority order:
    1. ROSACE_CONFIG env var, if set — an explicit path (any location), e.g. set by
       `python -m rosace.main <path>` or by the caller's own environment. Highest
       priority, meant for one-off overrides (a second instrument, a test config).
    2. <base_data_dir>/conf/config.yaml, where base_data_dir comes from
       base_data_dir.yaml (see that file) — this installation's own config, so a
       real deployment doesn't need to pass ROSACE_CONFIG on every launch.
    3. The packaged conf/config.yaml — demo defaults, works out of the box on a
       fresh install with nothing configured yet.
    """
    explicit = os.environ.get("ROSACE_CONFIG")
    if explicit:
        return Path(explicit).expanduser()

    boot_cfg = _load_config(_BASE_DATA_DIR_FILE)
    candidate = Path(boot_cfg["base_data_dir"]).expanduser() / "conf" / "config.yaml"
    if candidate.exists():
        return candidate

    return _DEFAULT_CONFIG_PATH


_CONFIG_PATH = _resolve_config_path()
_cfg = _load_config(_CONFIG_PATH)

# ── Directories ───────────────────────────────────────────────
BASE_DATA_DIR = Path(_cfg["base_data_dir"]).expanduser()
DATABASES_DIR = BASE_DATA_DIR / _cfg["databases_dir"]
DB_PATH       = DATABASES_DIR / "rosace.db"
LOG_DIR          = BASE_DATA_DIR / _cfg["log_dir"]
OBSERVATIONS_DIR = BASE_DATA_DIR / _cfg["observations_dir"]
# One fixed-name FITS file per GUI panel (e.g. "guidage.fits"), overwritten by each new
# capture -- a technical GUI preview copy, not observation data, so deliberately kept
# outside OBSERVATIONS_DIR. See image_broadcast.py / acquisition_helpers.py.
GUI_PREVIEW_DIR = BASE_DATA_DIR / "gui_preview"

# ── Observing site ─────────────────────────────────────────
# Generic name used to name session directories.
SITE_NAME = _cfg["site_name"]

# The site's geographic position (longitude signed, East positive) and the
# minimum altitude (degrees) for a target to be considered observable — used
# by scheduler_rosace_2026 for its visibility calculation (AltAz, AstroPy).
SITE_LATITUDE_DEG = _cfg["site_latitude_deg"]
SITE_LONGITUDE_DEG = _cfg["site_longitude_deg"]
SITE_ELEVATION_M = _cfg["site_elevation_m"]
MIN_TARGET_ALTITUDE_DEG = _cfg["min_target_altitude_deg"]

# ── Active hardware configuration ────────────────────────────
# Name of the YAML file in conf/ (without extension) describing the hardware
# to use. Lets you switch between several configurations (simulator, real
# observatory…) by changing only this value.
ACTIVE_HARDWARE = _cfg["active_hardware"]
HARDWARE_CONFIG_PATH = Path(__file__).parent / "conf" / f"{ACTIVE_HARDWARE}.yaml"

# ── Scheduling ──────────────────────────────────────────────
# Delay before asking SCHEDULER again when there's nothing to observe right
# now (SCHEDULER always answers quickly, it never blocks internally — it's
# the orchestrator that manages the wait).
SCHEDULER_RETRY_SECONDS = _cfg["scheduler_retry_seconds"]

# ── Hardware connectivity (INIT_SESSION, sequencer_backend=indi) ──────
# Maximum time given to each INDI device / to PHD2 to respond, during the
# connectivity check at session start (see hardware_connectivity.py).
HARDWARE_CONNECT_TIMEOUT_SECONDS = _cfg["hardware_connect_timeout_seconds"]

# ── Science exposure ─────────────────────────────────────
# Exposure time (mag 0) and max duration of a sub-exposure — see
# acquisition_helpers.py::compute_exposure_time_and_count().
EXPOSURE_TIME_MAG0_SECONDS = _cfg["exposure_time_mag0_seconds"]
MAX_EXPOSURE_TIME_SECONDS = _cfg["max_exposure_time_seconds"]

# ── Darks / bias ──────────────────────────────────────────────
# Number of images per series, fixed for the whole session. See
# plugins/sequencer_plugins/dark_acquisition/.
DARK_COUNT = _cfg["dark_count"]
BIAS_COUNT = _cfg["bias_count"]
BIAS_EXPOSURE_TIME_SECONDS = _cfg["bias_exposure_time_seconds"]
DARK_LIBRARY_DIR = BASE_DATA_DIR / _cfg["dark_library_dir"]

# ── Camera cooldown (INIT_SESSION, sequencer_backend=indi) ──────
# Maximum time given to the science camera to reach its computed target
# temperature. See plugins/camera_cooldown.py.
CAMERA_COOLDOWN_TIMEOUT_SECONDS = _cfg["camera_cooldown_timeout_seconds"]

# ── SEQUENCER execution backend ────────────────────────────────
# "internal": internal simulation, no external dependency (level-1 demo).
# "indi"    : real INDI/PHD2 protocol, driven by ACTIVE_HARDWARE/HARDWARE_CONFIG_PATH
#             (level-2/3 demo). Only concerns SEQUENCER sub-plugins — REDUCTION
#             has no hardware dependency.
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
    Safety net: configures the real logging system (the same conf/logging.cfg
    as in normal operation) so that a config error raised here — before
    configure_logging() (log_config.py) has had a chance to run, since it
    itself needs BASE_DATA_DIR — still lands in rosace.log, not just on
    stderr. Doesn't call log_config.py directly, to avoid a circular import
    (log_config.py does `from .config import BASE_DATA_DIR`).
    """
    try:
        # Recomputed from BASE_DATA_DIR (not the frozen LOG_DIR constant above) so that
        # monkeypatching BASE_DATA_DIR alone (as tests do) is enough to redirect this too.
        log_dir = BASE_DATA_DIR / _cfg["log_dir"]
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.config.fileConfig(
            _LOGGING_CFG_PATH,
            disable_existing_loggers=False,
            defaults={"logfilename": str(log_dir / "rosace.log")},
        )
        logging.getLogger("spectro.config").error(f"[CONFIG] {message}")
    except Exception:
        pass  # best effort — never mask the original error


try:
    SEQUENCER_BACKEND = _validate_sequencer_backend(_cfg["sequencer_backend"])
except ValueError as e:
    _log_fatal_startup_error(str(e))
    raise

# ── Active SCHEDULER plugin ────────────────────────────────────────
# Name (registry in plugins/schedulers/__init__.py) of the SCHEDULER plugin
# to use — each is an interchangeable target-selection algorithm, see
# plugins/schedulers/blank/scheduler.py for the contract to honor.
_VALID_SCHEDULERS = ("scheduler_rosace_2026", "blank")


def _validate_active_scheduler(value: str) -> str:
    if value not in _VALID_SCHEDULERS:
        raise ValueError(
            f"conf/config.yaml: active_scheduler={value!r} invalide "
            f"(valeurs acceptées : {_VALID_SCHEDULERS})"
        )
    return value


try:
    ACTIVE_SCHEDULER = _validate_active_scheduler(_cfg["active_scheduler"])
except ValueError as e:
    _log_fatal_startup_error(str(e))
    raise

# ── Observation plan (SCHEDULER) ───────────────────────────────
# Text file (anywhere on disk, not necessarily under BASE_DATA_DIR) that
# SCHEDULER reads to pick the next target.
OBSERVATION_LIST_PATH = Path(_cfg["observation_list_path"]).expanduser()

# ── Authentication ──────────────────────────────────────────────
# How long a session (cookie) stays valid before requiring a fresh login.
SESSION_TTL_HOURS = _cfg["session_ttl_hours"]
# The session cookie's 'Secure' attribute — see the comment in config.yaml.
SESSION_COOKIE_SECURE = _cfg["session_cookie_secure"]

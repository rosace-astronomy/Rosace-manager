# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

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

# Optional — absent (False) in every normal config.yaml. Only conf/config_demo.yaml
# sets this, to trigger demo_seed.py's one-time catalogue seeding at startup.
IS_DEMO_MODE: bool = bool(_cfg.get("is_demo", False))

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

# The site's geographic position (longitude signed, East positive) — a
# physical fact about the installation, not a plugin setting, even though
# only scheduler_rosace_2026 reads it today (AltAz visibility calculation).
SITE_LATITUDE_DEG = _cfg["site_latitude_deg"]
SITE_LONGITUDE_DEG = _cfg["site_longitude_deg"]
SITE_ELEVATION_M = _cfg["site_elevation_m"]

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

# ── Darks / bias ──────────────────────────────────────────────
# DARK_LIBRARY_DIR stays a core constant (a directory, same shape/lifecycle
# as BASE_DATA_DIR/LOG_DIR/... above) even though only dark_acquisition
# consumes it — see plugins/sequencer_plugins/dark_acquisition/.
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
            f"conf/config.yaml: sequencer_backend={value!r} invalid "
            f"(accepted values: {_VALID_SEQUENCER_BACKENDS})"
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
            f"conf/config.yaml: active_scheduler={value!r} invalid "
            f"(accepted values: {_VALID_SCHEDULERS})"
        )
    return value


try:
    ACTIVE_SCHEDULER = _validate_active_scheduler(_cfg["active_scheduler"])
except ValueError as e:
    _log_fatal_startup_error(str(e))
    raise

# ── Plugin parameters ──────────────────────────────────────────
# A single generic passthrough for every sub-plugin-specific setting (e.g.
# DARK/BIAS frame counts, scheduler_rosace_2026's observation list path...).
# Deliberately the ONLY thing config.py ever does with these: unlike the
# core constants above, no key here gets its own named constant in this
# module, since sub-plugins are meant to vary freely per installation and
# config.py must never need editing again just because a new plugin
# parameter was introduced. Each consuming sub-plugin reads its own slice
# (e.g. SUBPLUGIN_PARAMS.get("dark_acquisition", {}).get("count", 5)) and
# owns its own default — config.py has no opinion on what's inside, or
# even on whether a given key exists at all. See conf/config.yaml's
# subplugin_params section and CLAUDE.md's config table.
SUBPLUGIN_PARAMS: dict = _cfg.get("subplugin_params", {})

# ── Authentication ──────────────────────────────────────────────
# How long a session (cookie) stays valid before requiring a fresh login.
SESSION_TTL_HOURS = _cfg["session_ttl_hours"]
# The session cookie's 'Secure' attribute — see the comment in config.yaml.
SESSION_COOKIE_SECURE = _cfg["session_cookie_secure"]
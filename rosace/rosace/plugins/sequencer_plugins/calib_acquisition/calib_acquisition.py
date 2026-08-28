"""
Sub-plugin SEQUENCER -- Calibration frame acquisition, with current-session
reuse. Backend "indi" -- named "calib_acquisition" rather than "calib" to
stay consistent with dark_acquisition/bias_acquisition (see CLAUDE.md's
cross-family uniqueness rule).

A CALIB image is a science-camera exposure taken with the spectroscope
switched to the CALIB light source (a calibration lamp). Unlike
dark_acquisition, there is no reusable library, and no temperature
constraint at all -- a calibration lamp's flux dominates any dark-current
concern, so this step never reads (or requires) obs.camera_target_temperature_deg_c.
Only two origins, same shape as bias_acquisition:

- current_session: a usable series already sits in this session's own
  directory (e.g. made for an earlier observation tonight) -- *and* its
  capture timestamp is younger than max_age_hours
  (subplugin_params.calib_acquisition in conf/config.yaml): a calibration
  lamp's spectral response can drift over hours, so an old-enough series is
  treated as stale and reshot even though it's still technically present.
- camera: otherwise, switches the spectroscope to CALIB and shoots a fresh
  CALIB_COUNT-frame series at CALIB_EXPOSURE_TIME_SECONDS.

Casier: {"origin": "current_session"|"camera", "calib_files": [...]}.
"""

from __future__ import annotations

DISPLAY_NAME = "Calib"

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from astropy.io import fits

from ....config import SUBPLUGIN_PARAMS
from ....hardware.spectroscope import LightSource
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import build_fits_root_name, capture_science_image
from ..dark_library_helpers import EXPOSURE_TIME_TOLERANCE_SECONDS, summarize_series_root_name

log = logging.getLogger("spectro.subplugin.calib_acquisition")

_params = SUBPLUGIN_PARAMS.get("calib_acquisition", {})
CALIB_COUNT = _params.get("count", 5)
CALIB_EXPOSURE_TIME_SECONDS = _params.get("exposure_time_seconds", 3.0)
CALIB_MAX_AGE_HOURS = _params.get("max_age_hours", 24.0)

# Matches the trailing "_YYYYMMDD_HHMMSS" timestamp build_fits_root_name()
# always writes, with an optional per-file series index ("-1", "-2", ...)
# right before ".fits" -- same idea as dark_library_helpers.py's own
# _TEMP_TOKEN_RE, parsing the filename rather than trusting a FITS header
# card (no capture-time card is stamped by capture_science_image()).
_TIMESTAMP_TOKEN_RE = re.compile(r"_(\d{8}_\d{6})(?:-\d+)?\.fits$")


def _find_usable_calib_series(
    directory: Path,
    *,
    count_required: int,
    exposure_time_required: float,
    max_age_hours: float,
    sensor_size: tuple[int, int],
    now: datetime,
) -> list[str] | None:
    """Looks for a usable CALIB series in `directory` (the session
    directory): the most recent batch of CALIB_*.fits files sharing the
    same embedded capture timestamp -- at least count_required files,
    younger than max_age_hours, whose first file's FITS header (read
    alone, not the pixel data) reports the same sensor size as sensor_size
    and an EXPTIME at least exposure_time_required minus
    EXPOSURE_TIME_TOLERANCE_SECONDS.

    Unlike find_usable_frame_series() (DARK/BIAS, dark_library_helpers.py),
    age -- not temperature -- is what makes an existing series stale here,
    and only the single most recent batch is ever considered: an older,
    already-stale batch left behind by an earlier reshoot this session must
    never be picked over a fresher one sitting right next to it. Returns
    the matching filenames (sorted), or None if no usable series was found."""
    candidates = sorted(directory.glob("CALIB_*.fits")) if directory.is_dir() else []
    if not candidates:
        return None

    latest_match = _TIMESTAMP_TOKEN_RE.search(candidates[-1].name)
    if latest_match is None:
        return None
    latest_token = latest_match.group(1)

    batch = [
        p for p in candidates
        if (m := _TIMESTAMP_TOKEN_RE.search(p.name)) and m.group(1) == latest_token
    ]
    if len(batch) < count_required:
        return None

    batch_time = datetime.strptime(latest_token, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    age_hours = (now - batch_time).total_seconds() / 3600
    if age_hours > max_age_hours:
        return None

    selected = batch[:count_required]
    header = fits.getheader(selected[0])
    if (header.get("NAXIS1"), header.get("NAXIS2")) != tuple(sensor_size):
        return None
    exptime = header.get("EXPTIME")
    if exptime is None or exptime < exposure_time_required - EXPOSURE_TIME_TOLERANCE_SECONDS:
        return None

    return [p.name for p in selected]


class CalibAcquisitionPlugin(SubPlugin):
    """Resolves a usable CALIB series for this observation -- reused from
    the session directory when a young-enough series already exists,
    freshly captured with the science camera otherwise."""

    async def execute(self, obs: Observation) -> Observation:
        if not obs.obs_dir:
            raise RuntimeError("CALIB_ACQUISITION: observation directory (obs_dir) not set")

        camera = obs.hardware["science_camera"]
        sensor_size = await camera.get_sensor_size()

        files = _find_usable_calib_series(
            Path(obs.obs_dir), count_required=CALIB_COUNT,
            exposure_time_required=CALIB_EXPOSURE_TIME_SECONDS,
            max_age_hours=CALIB_MAX_AGE_HOURS, sensor_size=sensor_size,
            now=datetime.now(timezone.utc),
        )
        origin = "current_session"

        # camera: otherwise, shoot a fresh series with the science camera (no library for CALIB).
        if files is None:
            spectroscope = obs.hardware["spectroscope"]
            await spectroscope.set_light_source(LightSource.CALIB)
            root_name = build_fits_root_name(
                "CALIB", exposure_time=CALIB_EXPOSURE_TIME_SECONDS, num_exposures=CALIB_COUNT,
            )
            captured: list[str] = []
            for i in range(1, CALIB_COUNT + 1):
                suffix = f"-{i}" if CALIB_COUNT > 1 else ""
                path = await capture_science_image(
                    camera, CALIB_EXPOSURE_TIME_SECONDS, obs.obs_dir, f"{root_name}{suffix}.fits", image_to_gui=True
                )
                captured.append(path.name)

                if i < CALIB_COUNT and obs.stop_check and obs.stop_check():
                    log.info(
                        f"[CALIB_ACQUISITION] Stop requested -- {i}/{CALIB_COUNT} calib(s) captured, interrupting"
                    )
                    break
            files = captured
            origin = "camera"

        obs.sequencer_data["calib_acquisition"] = {"origin": origin, "calib_files": files}
        log.info(f"[CALIB_ACQUISITION] Origin={origin}, series of {len(files)}x{CALIB_EXPOSURE_TIME_SECONDS:g}s")
        log.info(f"[CALIB_ACQUISITION] Done, root name = '{summarize_series_root_name(files)}'")
        return obs

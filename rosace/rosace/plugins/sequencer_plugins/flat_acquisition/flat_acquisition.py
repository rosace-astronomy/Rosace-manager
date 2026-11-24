"""
Sub-plugin SEQUENCER -- Flat frame acquisition, with current-session reuse.
Backend "indi" -- named "flat_acquisition" rather than "flat" to avoid
colliding with reduction_plugins/flat (the FLAT-field correction
processing step -- see CLAUDE.md's cross-family uniqueness rule), same
reasoning as dark_acquisition/bias_acquisition/calib_acquisition.

Simplest of the four acquisition sub-plugins: a FLAT image is a
science-camera exposure taken with the spectroscope switched to the FLAT
light source, shot once per session and reused for the rest of it, with no
staleness check at all -- unlike calib_acquisition (age-based) or
dark_acquisition (temperature-based, via the reusable library): flat-field
correction is about optical vignetting/uniformity, not sensor temperature
or lamp drift, so an existing session-directory series is simply always
reused once it exists. No library either, same simplification as BIAS.
Only two origins:

- current_session: a usable series already sits in this session's own
  directory (e.g. made for an earlier observation tonight).
- camera: otherwise, switches the spectroscope to FLAT and shoots a fresh
  FLAT_COUNT-frame series at FLAT_EXPOSURE_TIME_SECONDS.

Casier: {"origin": "current_session"|"camera"}. The series' filenames go to
the shared sequencer_data["flat_images"] key, which REDUCTION reads -- never
this casier (see CLAUDE.md, "Shared, purpose-named keys").
"""

from __future__ import annotations

DISPLAY_NAME = "Flat"
TIME_CATEGORY = "calibrations"

import logging
from pathlib import Path

from ....config import SUBPLUGIN_PARAMS
from ....hardware.spectroscope import LightSource
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import build_fits_root_name, capture_science_image
from ..dark_library_helpers import pick_most_recent_usable_series, summarize_series_root_name

log = logging.getLogger("spectro.subplugin.flat_acquisition")

_params = SUBPLUGIN_PARAMS.get("flat_acquisition", {})
FLAT_COUNT = _params.get("count", 5)
FLAT_EXPOSURE_TIME_SECONDS = _params.get("exposure_time_seconds", 3.0)


def _find_usable_flat_series(
    directory: Path,
    *,
    count_required: int,
    exposure_time_required: float,
    frame_size: tuple[int, int],
) -> list[str] | None:
    """Looks for a usable FLAT series in `directory` (the session
    directory): the most recent series of FLAT_*.fits files (same root name,
    minus the trailing "-<N>" index) with at least count_required files whose
    first file's FITS header (read alone, not the pixel data) reports the
    same frame size as frame_size (the camera's currently configured
    CCD_FRAME, see hardware/camera.py::Camera.get_frame_size()) and an
    EXPTIME at least exposure_time_required minus
    EXPOSURE_TIME_TOLERANCE_SECONDS -- see
    dark_library_helpers.py::pick_most_recent_usable_series(), shared with
    DARK/BIAS. No staleness check at all, unlike DARK/BIAS
    (temperature-tolerant, see find_usable_frame_series()) or CALIB
    (age-based) -- flat-field correction depends on neither, so once a
    matching series exists in the session directory it's reused for the
    rest of it. Returns its first count_required filenames, or None if no
    usable series was found."""
    if not directory.is_dir():
        return None
    return pick_most_recent_usable_series(
        list(directory.glob("FLAT_*.fits")), count_required=count_required,
        exposure_time_required=exposure_time_required, frame_size=frame_size,
    )


class FlatAcquisitionPlugin(SubPlugin):
    """Resolves a usable FLAT series for this observation -- reused from
    the session directory when one already exists, freshly captured with
    the science camera otherwise."""

    async def execute(self, obs: Observation) -> Observation:
        if not obs.obs_dir:
            raise RuntimeError("FLAT_ACQUISITION: observation directory (obs_dir) not set")

        camera = obs.hardware["science_camera"]
        frame_size = await camera.get_frame_size()

        files = _find_usable_flat_series(
            Path(obs.obs_dir), count_required=FLAT_COUNT,
            exposure_time_required=FLAT_EXPOSURE_TIME_SECONDS, frame_size=frame_size,
        )
        origin = "current_session"

        # camera: otherwise, shoot a fresh series with the science camera (no library for FLAT).
        if files is None:
            spectroscope = obs.hardware["spectroscope"]
            await spectroscope.set_light_source(LightSource.FLAT)
            root_name = build_fits_root_name(
                "FLAT", exposure_time=FLAT_EXPOSURE_TIME_SECONDS, num_exposures=FLAT_COUNT,
            )
            captured: list[str] = []
            for i in range(1, FLAT_COUNT + 1):
                suffix = f"-{i}" if FLAT_COUNT > 1 else ""
                path = await capture_science_image(
                    camera, FLAT_EXPOSURE_TIME_SECONDS, obs.obs_dir, f"{root_name}{suffix}.fits", image_to_gui=True, obs=obs
                )
                captured.append(path.name)

                if i < FLAT_COUNT and obs.stop_check and obs.stop_check():
                    log.info(
                        f"[FLAT_ACQUISITION] Stop requested -- {i}/{FLAT_COUNT} flat(s) captured, interrupting"
                    )
                    break
            files = captured
            origin = "camera"

        obs.sequencer_data["flat_acquisition"] = {"origin": origin}
        obs.sequencer_data["flat_images"] = files
        log.info(f"[FLAT_ACQUISITION] Origin={origin}, series of {len(files)}x{FLAT_EXPOSURE_TIME_SECONDS:g}s")
        log.info(f"[FLAT_ACQUISITION] Done, root name = '{summarize_series_root_name(files)}'")
        return obs

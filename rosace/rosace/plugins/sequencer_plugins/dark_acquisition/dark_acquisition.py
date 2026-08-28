"""
Sub-plugin SEQUENCER -- Dark frame acquisition, with session/library reuse.
Backend "indi" -- named "dark_acquisition" rather than "dark" to avoid
colliding with reduction_plugins/dark (the DARK-subtraction processing
step -- see CLAUDE.md's cross-family uniqueness rule).

Every DARK frame must come from the science camera actually used for this
observation, at the same sensor temperature as the science images -- the
camera is already cooled to a fixed session-wide target before any
observation starts (see plugins/camera_cooldown.py, called once from
INIT_SESSION), read here from obs.camera_target_temperature_deg_c. This
sub-plugin never re-sets or re-checks the camera's temperature itself: it
trusts INIT_SESSION already got it there.

Producing a full dark series is slow, so three origins are tried in order,
each cheaper than the next -- the sensor size used to validate a candidate
series always comes straight from the camera itself (get_sensor_size()),
never from an already-acquired science image, so this step's position in
the program YAML relative to star_spectra_acquisition doesn't matter:

- current_session: a usable series already sits in this session's own
  directory (e.g. made for an earlier observation tonight).
- library: a usable series exists in the reusable dark library
  (DARK_LIBRARY_DIR, one subdirectory per temperature bucket -- see
  dark_library_helpers.py) -- copied into the session directory (every
  session image must live there) before being used. Never the other way
  around: nothing here ever writes into the library itself, that stays a
  manual transfer by the observer.
- camera: neither is found -- switches the spectroscope to DARK and shoots
  a fresh series with the science camera.

"Usable" (dark_library_helpers.py::find_usable_dark_series()) means: at
least DARK_COUNT files at the right temperature, whose first file reports
the camera's own sensor size and an exposure time >= MAX_EXPOSURE_TIME_SECONDS
-- a longer library exposure is accepted, a shorter one is not.

Casier: {"origin": "current_session"|"library"|"camera", "dark_files": [...]}
-- both are genuinely produced by this step (which files were retained, and
how), so a casier is legitimate here (contrast with a sub-plugin that has
nothing descriptive to add, see CLAUDE.md).
"""

from __future__ import annotations

DISPLAY_NAME = "Dark"

import logging
from pathlib import Path

from ....config import DARK_LIBRARY_DIR, SUBPLUGIN_PARAMS
from ....hardware.spectroscope import LightSource
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import build_fits_root_name, capture_science_image
from ..dark_library_helpers import (
    copy_darks_from_library,
    find_usable_frame_series,
    summarize_series_root_name,
    temperature_bucket_dirname,
)

log = logging.getLogger("spectro.subplugin.dark_acquisition")

_params = SUBPLUGIN_PARAMS.get("dark_acquisition", {})
DARK_COUNT = _params.get("count", 5)
# Shared with star_spectra_acquisition.py -- see conf/config.yaml's
# subplugin_params section for why this one key sits at the root.
MAX_EXPOSURE_TIME_SECONDS = SUBPLUGIN_PARAMS.get("max_exposure_time_seconds", 300)


class DarkAcquisitionPlugin(SubPlugin):
    """Resolves a usable DARK series for this observation -- reused from the
    session directory or the dark library when possible, freshly captured
    with the science camera otherwise."""

    async def execute(self, obs: Observation) -> Observation:
        if not obs.obs_dir:
            raise RuntimeError("DARK_ACQUISITION: observation directory (obs_dir) not set")
        if obs.camera_target_temperature_deg_c is None:
            raise RuntimeError(
                "DARK_ACQUISITION: camera_target_temperature_deg_c not set "
                "-- INIT_SESSION's cooldown step did not run"
            )

        camera = obs.hardware["science_camera"]
        target_temp = obs.camera_target_temperature_deg_c
        sensor_size = await camera.get_sensor_size()

        # current_session: reuse a usable series already sitting in this session's own directory.
        files = find_usable_frame_series(
            Path(obs.obs_dir), image_type="DARK", temp_c=target_temp, count_required=DARK_COUNT,
            exposure_time_required=MAX_EXPOSURE_TIME_SECONDS, sensor_size=sensor_size,
        )
        origin = "current_session"

        # library: otherwise, reuse a usable series from the dark library, copied into the session dir.
        if files is None:
            library_dir = DARK_LIBRARY_DIR / temperature_bucket_dirname(target_temp)
            library_files = find_usable_frame_series(
                library_dir, image_type="DARK", temp_c=target_temp, count_required=DARK_COUNT,
                exposure_time_required=MAX_EXPOSURE_TIME_SECONDS, sensor_size=sensor_size,
            )
            if library_files is not None:
                files = copy_darks_from_library(library_dir, library_files, Path(obs.obs_dir))
                origin = "library"

        # camera: otherwise, shoot a fresh series with the science camera.
        if files is None:
            spectroscope = obs.hardware["spectroscope"]
            await spectroscope.set_light_source(LightSource.DARK)
            root_name = build_fits_root_name(
                "DARK", exposure_time=MAX_EXPOSURE_TIME_SECONDS, num_exposures=DARK_COUNT,
                sensor_temp_c=target_temp,
            )
            captured: list[str] = []
            for i in range(1, DARK_COUNT + 1):
                suffix = f"-{i}" if DARK_COUNT > 1 else ""
                path = await capture_science_image(
                    camera, MAX_EXPOSURE_TIME_SECONDS, obs.obs_dir, f"{root_name}{suffix}.fits", image_to_gui=True
                )
                captured.append(path.name)

                if i < DARK_COUNT and obs.stop_check and obs.stop_check():
                    log.info(
                        f"[DARK_ACQUISITION] Stop requested -- {i}/{DARK_COUNT} dark(s) captured, interrupting"
                    )
                    break
            files = captured
            origin = "camera"

        obs.sequencer_data["dark_acquisition"] = {"origin": origin, "dark_files": files}
        log.info(f"[DARK_ACQUISITION] Origin={origin}, series of {len(files)}x{MAX_EXPOSURE_TIME_SECONDS:g}s at {round(target_temp)}deg")
        log.info(f"[DARK_ACQUISITION] Done, root name = '{summarize_series_root_name(files)}'")
        return obs

"""
Sub-plugin SEQUENCER -- Bias frame acquisition, with current-session reuse.
Backend "indi" -- named "bias_acquisition" rather than "bias" to avoid
colliding with reduction_plugins/bias (the BIAS-subtraction processing
step -- see CLAUDE.md's cross-family uniqueness rule), same reasoning as
dark_acquisition/.

Same origin logic as dark_acquisition, minus the library step (no
reusable bias library is kept -- see CLAUDE.md, an explicit user choice: a
BIAS series is fast enough that in practice it only ever needs remaking
once, for the session's first observation):

- current_session: a usable series already sits in this session's own
  directory (e.g. made for an earlier observation tonight).
- camera: otherwise, switches the spectroscope to DARK (there is no
  dedicated BIAS light source on hardware/spectroscope.py::LightSource --
  a bias needs the same "no light reaches the sensor" state as a dark, it's
  simply a zero-length exposure) and shoots a fresh BIAS_COUNT-frame series
  at BIAS_EXPOSURE_TIME_SECONDS (0s by definition, overridable in
  conf/config.yaml for a camera that refuses a literal 0s exposure).

Never re-sets or re-checks the camera's temperature itself, same as
dark_acquisition -- trusts the once-per-session cooldown (see
plugins/camera_cooldown.py) and reads obs.camera_target_temperature_deg_c.

Casier: {"origin": "current_session"|"camera", "bias_files": [...]}.
"""

from __future__ import annotations

DISPLAY_NAME = "Bias"

import logging
from pathlib import Path

from ....config import SUBPLUGIN_PARAMS
from ....hardware.spectroscope import LightSource
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import build_fits_root_name, capture_science_image
from ..dark_library_helpers import find_usable_frame_series, summarize_series_root_name

log = logging.getLogger("spectro.subplugin.bias_acquisition")

_params = SUBPLUGIN_PARAMS.get("bias_acquisition", {})
BIAS_COUNT = _params.get("count", 5)
BIAS_EXPOSURE_TIME_SECONDS = _params.get("exposure_time_seconds", 0.0)


class BiasAcquisitionPlugin(SubPlugin):
    """Resolves a usable BIAS series for this observation -- reused from the
    session directory when possible, freshly captured with the science
    camera otherwise."""

    async def execute(self, obs: Observation) -> Observation:
        if not obs.obs_dir:
            raise RuntimeError("BIAS_ACQUISITION: observation directory (obs_dir) not set")
        if obs.camera_target_temperature_deg_c is None:
            raise RuntimeError(
                "BIAS_ACQUISITION: camera_target_temperature_deg_c not set "
                "-- INIT_SESSION's cooldown step did not run"
            )

        camera = obs.hardware["science_camera"]
        target_temp = obs.camera_target_temperature_deg_c
        sensor_size = await camera.get_sensor_size()

        # current_session: reuse a usable series already sitting in this session's own directory.
        files = find_usable_frame_series(
            Path(obs.obs_dir), image_type="BIAS", temp_c=target_temp, count_required=BIAS_COUNT,
            exposure_time_required=BIAS_EXPOSURE_TIME_SECONDS, sensor_size=sensor_size,
        )
        origin = "current_session"

        # camera: otherwise, shoot a fresh series with the science camera (no library for BIAS).
        if files is None:
            spectroscope = obs.hardware["spectroscope"]
            await spectroscope.set_light_source(LightSource.DARK)
            root_name = build_fits_root_name(
                "BIAS", exposure_time=BIAS_EXPOSURE_TIME_SECONDS, num_exposures=BIAS_COUNT,
                sensor_temp_c=target_temp,
            )
            captured: list[str] = []
            for i in range(1, BIAS_COUNT + 1):
                suffix = f"-{i}" if BIAS_COUNT > 1 else ""
                path = await capture_science_image(
                    camera, BIAS_EXPOSURE_TIME_SECONDS, obs.obs_dir, f"{root_name}{suffix}.fits", image_to_gui=True
                )
                captured.append(path.name)

                if i < BIAS_COUNT and obs.stop_check and obs.stop_check():
                    log.info(
                        f"[BIAS_ACQUISITION] Stop requested -- {i}/{BIAS_COUNT} bias(es) captured, interrupting"
                    )
                    break
            files = captured
            origin = "camera"

        obs.sequencer_data["bias_acquisition"] = {"origin": origin, "bias_files": files}
        log.info(f"[BIAS_ACQUISITION] Origin={origin}, series of {len(files)}x{BIAS_EXPOSURE_TIME_SECONDS:g}s at {round(target_temp)}deg")
        log.info(f"[BIAS_ACQUISITION] Done, root name = '{summarize_series_root_name(files)}'")
        return obs

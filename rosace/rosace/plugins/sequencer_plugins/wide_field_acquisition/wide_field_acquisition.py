"""
SEQUENCER sub-plugin -- wide-field image of the target with the pointing camera, "indi" backend only.

One exposure of obs.hardware["pointing_camera"] (the ZWO ASI224MC on the
72ED, a colour camera), taken right after the science exposures while the
telescope is still on the target -- so one sees whether clouds crossed the
target's field. The REDUCTION step wide_field_image plate-solves it and
marks the target and the bright stars on it.

The exposure time is subplugin_params.wide_field.exposure_seconds (5 s by
default); gain/offset come from the hardware YAML, pushed at connection like
every camera. The file, WIDEFIELD_<queried name>_<exp>s_<timestamp>.fits in
the session directory, carries the target's catalogue position (RA/DEC/
EQUINOX, J2000) and its queried name (OBJECT), stamped by Rosace like the
guide images; its name goes under the shared key
sequencer_data["wide_field_images"].

Never blocking: without a pointing camera in the active hardware profile
(the replay demo, the simulators) it does nothing (one INFO line); a failed
exposure is a WARNING and the observation goes on. Not wrapped by the
guiding-chain recovery: another camera, which the guiding never needs.
"""

from __future__ import annotations

DISPLAY_NAME = "Grand champ"
TIME_CATEGORY = "target_measurements"

import logging
from pathlib import Path

from ....config import SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import build_fits_root_name

log = logging.getLogger("spectro.subplugin.wide_field_acquisition")

CAMERA = "pointing_camera"
DEFAULT_EXPOSURE_SECONDS = 5.0


class WideFieldAcquisitionPlugin(SubPlugin):
    """Takes one wide-field image of the target with the pointing camera, never blocking."""

    async def execute(self, obs: Observation) -> Observation:
        camera = obs.hardware.get(CAMERA)
        if camera is None:
            log.info(f"[WIDE_FIELD] Not used: no '{CAMERA}' in the active hardware profile")
            return obs
        exposure = float((SUBPLUGIN_PARAMS.get("wide_field") or {}).get("exposure_seconds", DEFAULT_EXPOSURE_SECONDS))
        name = obs.scheduler_data.get("queried_name")
        target = obs.scheduler_data.get("target")
        try:
            image = await camera.expose(exposure)
            header = image[0].header
            if target is not None:
                header["RA"] = (target.ra, "J2000, stamped by Rosace")
                header["DEC"] = (target.dec, "J2000, stamped by Rosace")
                header["EQUINOX"] = (2000, "Equinox of RA and DEC above, stamped by Rosace")
            if name:
                header["OBJECT"] = (name, "Queried name, stamped by Rosace")
            filename = build_fits_root_name("WIDEFIELD", target_name=name, exposure_time=exposure) + ".fits"
            image.writeto(Path(obs.obs_dir) / filename, overwrite=True)
        except Exception as error:
            log.warning(f"[WIDE_FIELD] No wide-field image for this observation: {type(error).__name__}: {error}")
            return obs
        obs.sequencer_data["wide_field_images"] = [filename]
        log.info(f"[WIDE_FIELD] {exposure:g} s exposure -> {filename}")
        return obs

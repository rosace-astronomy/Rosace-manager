"""
REDUCTION sub-plugin — Calibration-lamp preprocessing
Corrects every calibration-lamp frame with the master bias and the master flat: (frame - bias) / flat.

Same computation as preproc_target, on the lamp series. Inputs: the shared
keys calib_images, master_bias_image and master_flat_image. Each frame is
corrected on its own; the dark is not subtracted for now. The corrected
frames go to the observation's intermediate-file directory ("<queried
name>_<observation id>" in the session directory) as
"<raw name>_preproc.fits", and their paths, relative to the session
directory, under the shared reduction_data["preproc_calib_images"] key -- no
casier of its own. See reduction_helpers.py::preprocess_frames().
"""

from __future__ import annotations

DISPLAY_NAME = "Preproc C"

import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import preprocess_frames

log = logging.getLogger("spectro.subplugin.preproc_calib")


class PreprocCalibPlugin(SubPlugin):
    """Corrects every calibration-lamp frame with the master bias and the master flat."""

    async def execute(self, obs: Observation) -> Observation:
        written, out_dir = preprocess_frames(obs, "calib_images")
        obs.reduction_data["preproc_calib_images"] = written
        log.info(f"[PREPROC_CALIB] {len(written)} frames preprocessed into '{out_dir.name}'")
        return obs

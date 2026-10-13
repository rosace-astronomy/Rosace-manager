"""
REDUCTION sub-plugin — Target preprocessing
Corrects every science frame with the master bias and the master flat: (frame - bias) / flat.

Inputs: the shared keys star_spectra_images, master_bias_image,
master_dark_image and master_flat_image. Each frame is corrected on its own
(stacking comes later); the dark is not subtracted, but the hot pixels the
master dark shows are replaced by their row neighbours. The corrected frames go to the
observation's intermediate-file directory ("<queried name>_<observation id>"
in the session directory) as "<raw name>_preproc.fits", and their paths,
relative to the session directory, under the shared
reduction_data["preproc_target_images"] key -- no casier of its own. See
reduction_helpers.py::preprocess_frames().
"""

from __future__ import annotations

DISPLAY_NAME = "Preproc T"

import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import preprocess_frames

log = logging.getLogger("spectro.subplugin.preproc_target")


class PreprocTargetPlugin(SubPlugin):
    """Corrects every science frame with the master bias and the master flat."""

    async def execute(self, obs: Observation) -> Observation:
        written, out_dir, hot_count = preprocess_frames(obs, "star_spectra_images")
        obs.reduction_data["preproc_target_images"] = written
        log.info(f"[PREPROC_TARGET] {len(written)} frames preprocessed into '{out_dir.name}', "
                 f"{hot_count} hot pixels replaced in each")
        return obs

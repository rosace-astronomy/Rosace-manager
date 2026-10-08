"""
REDUCTION sub-plugin — Master flat
Combines the flat series into a master flat (min/max-rejected mean), removes the master bias and normalizes it by its mean.

Inputs: the shared sequencer_data["flat_images"] key and the master bias
(shared reduction_data["master_bias_image"] key, so master_bias must run
first). Output: the master's file name under the shared
reduction_data["master_flat_image"] key -- no casier of its own.

Unlike the dark, the flat is used in a division, where nothing would cancel
its offset: the master bias is subtracted first. The dark current over a
short flat exposure is neglected. The result is then divided by its own mean,
so it averages 1; the lamp's spectral shape stays in it, to be absorbed by
the instrumental response (see CLAUDE.md, "Native reduction chain"). Written
to the session directory as "<series root>_master.fits" and reused when that
file already exists (see reduction_helpers.py).
"""

from __future__ import annotations

DISPLAY_NAME = "M. flat"

import logging
from pathlib import Path

import numpy as np
from astropy.io import fits

from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import find_shared_input, resolve_master

log = logging.getLogger("spectro.subplugin.master_flat")


class MasterFlatPlugin(SubPlugin):
    """Combines the flat series into a master flat, removes the master bias and normalizes it by its mean."""

    async def execute(self, obs: Observation) -> Observation:
        def subtract_bias_and_normalize(data: np.ndarray, comments: list[str]) -> np.ndarray:
            bias_name = find_shared_input(obs, "master_bias_image")
            bias = fits.getdata(Path(obs.obs_dir) / bias_name).astype(np.float64)
            if bias.shape != data.shape:
                raise RuntimeError(f"MASTER_FLAT: master bias '{bias_name}' is {bias.shape}, flats are {data.shape}")
            data = data - bias
            mean = float(data.mean())
            if mean <= 0:
                raise RuntimeError(f"MASTER_FLAT: mean of the bias-subtracted flat is {mean:.1f} ADU, cannot normalize")
            comments += [
                f"Master bias subtracted: {bias_name}",
                "Dark current neglected",
                f"Normalized by its mean: {mean:.1f} ADU",
            ]
            return data / mean

        name, reused = resolve_master(obs, "flat_images", "Master flat", subtract_bias_and_normalize)
        obs.reduction_data["master_flat_image"] = name
        log.info(f"[MASTER_FLAT] {'Reused' if reused else 'Computed'} '{name}'")
        return obs

"""
REDUCTION sub-plugin — Master dark
Combines the dark series into a master dark (min/max-rejected mean; the dark keeps its bias).

Input: the shared sequencer_data["dark_images"] key (a series shot tonight or
copied from the dark library -- same file names either way). Output: the
master's file name under the shared reduction_data["master_dark_image"] key
-- no casier of its own. The bias is deliberately not subtracted: the dark is
subtracted from an image that carries the same bias, which cancels out (see
CLAUDE.md, "Native reduction chain"). Written to the session directory as
"<series root>_master.fits" and reused when that file already exists (see
master_helpers.py).
"""

from __future__ import annotations

DISPLAY_NAME = "M. dark"

import logging

import numpy as np

from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..master_helpers import resolve_master

log = logging.getLogger("spectro.subplugin.master_dark")


def _keep_bias(data: np.ndarray, comments: list[str]) -> np.ndarray:
    comments.append("Bias not subtracted")
    return data


class MasterDarkPlugin(SubPlugin):
    """Combines the dark series into a master dark (min/max-rejected mean; the dark keeps its bias)."""

    async def execute(self, obs: Observation) -> Observation:
        name, reused = resolve_master(obs, "dark_images", "Master dark", _keep_bias)
        obs.reduction_data["master_dark_image"] = name
        log.info(f"[MASTER_DARK] {'Reused' if reused else 'Computed'} '{name}'")
        return obs

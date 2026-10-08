"""
REDUCTION sub-plugin — Master bias
Combines the session's bias series into a master bias (min/max-rejected mean).

Input: the shared sequencer_data["bias_images"] key. Output: the master's
file name under the shared reduction_data["master_bias_image"] key -- no
casier of its own, nothing else descriptive to add. The master is written to
the session directory as "<series root>_master.fits" and reused when that
file already exists (see reduction_helpers.py), so it is computed once per
session.
"""

from __future__ import annotations

DISPLAY_NAME = "M. bias"

import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..reduction_helpers import resolve_master

log = logging.getLogger("spectro.subplugin.master_bias")


class MasterBiasPlugin(SubPlugin):
    """Combines the session's bias series into a master bias (min/max-rejected mean)."""

    async def execute(self, obs: Observation) -> Observation:
        name, reused = resolve_master(obs, "bias_images", "Master bias")
        obs.reduction_data["master_bias_image"] = name
        log.info(f"[MASTER_BIAS] {'Reused' if reused else 'Computed'} '{name}'")
        return obs

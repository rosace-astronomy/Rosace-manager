"""
SEQUENCER sub-plugin -- resets the spectroscope's light source to SKY, "indi" backend only.

Meant to run as one of the last SEQUENCER steps in a program, alongside
stop_phd2_guiding: nothing else resets the spectroscope at the end of an
observation, so once a future DARK/FLAT/CALIB acquisition sub-plugin
switches it away from SKY, it would otherwise stay on a calibration lamp
into the next observation.
"""

from __future__ import annotations

import logging

from ....hardware.spectroscope import LightSource
from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.spectro_light_source_to_sky")


class SpectroLightSourceToSkyPlugin(SubPlugin):
    """Resets the spectroscope's light source to SKY at the end of an observation."""

    async def execute(self, obs: Observation) -> Observation:
        spectroscope = obs.hardware["spectroscope"]

        await spectroscope.set_light_source(LightSource.SKY)

        obs.sequencer_data["spectro_light_source_to_sky"] = {"light_source": "sky"}
        log.info("[SPECTRO_LIGHT_SOURCE_TO_SKY] Spectroscope reset to SKY")
        return obs

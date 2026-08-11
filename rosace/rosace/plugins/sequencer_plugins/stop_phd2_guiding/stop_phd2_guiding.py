"""
SEQUENCER sub-plugin -- stops PHD2 guiding, "indi" backend only.

Meant to always be the last SEQUENCER step in a program: nothing else in
the sequencer stops PHD2 once pointing_72ed starts it -- without this,
guiding keeps running past the whole observation and into the next one,
where it can fight that next observation's own mount slews.
"""

from __future__ import annotations

import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.stop_phd2_guiding")


class StopPhd2GuidingPlugin(SubPlugin):
    """Stops PHD2 guiding/looping at the end of an observation's SEQUENCER steps."""

    async def execute(self, obs: Observation) -> Observation:
        phd2 = obs.hardware["phd2"]

        log.info("[STOP_PHD2_GUIDING] Stopping PHD2 guiding")
        await phd2.stop_capture()

        obs.sequencer_data["stop_phd2_guiding"] = {"guiding_stopped": True}
        log.info("[STOP_PHD2_GUIDING] PHD2 guiding stopped")
        return obs

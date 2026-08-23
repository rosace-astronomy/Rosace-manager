"""
SHARING plugin — Sharing orchestrator.
The sharing sub-plugin (a single one, not a list) is loaded dynamically
from the observation's scientific program YAML
(obs.scheduler_data["program"]), set by SCHEDULER — so it's reloaded for
every observation, not once and for all at startup.

Structure:
    programs/<program>.yaml  →  pipeline_loader  →  <SingleSubPlugin>
"""

import logging

from ..models import Observation
from .pipeline_loader import load_sharing_plugin

log = logging.getLogger("spectro.plugin.sharing")


class SharingPlugin:
    """
    Orchestrates result sharing by delegating to the sub-plugin declared in
    the current observation's program YAML.
    """

    async def run(self, obs: Observation) -> Observation:
        program = obs.scheduler_data.get("program")
        if not program:
            raise RuntimeError("SHARING : program manquant dans scheduler_data")

        sub = load_sharing_plugin(program)
        log.info(f"[SHARING] Diffusion via '{sub.__class__.__name__}'")
        obs = await sub.execute(obs)
        return obs

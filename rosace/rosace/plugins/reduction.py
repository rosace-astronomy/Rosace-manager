"""
REDUCTION plugin — Reduction chain orchestrator.
The pipeline (list of sub-plugins) is loaded dynamically from the
observation's scientific program YAML (obs.scheduler_data["program"]), set
by SCHEDULER — so it's reloaded for every observation, not once and for
all at startup.

Structure:
    programs/<program>.yaml  →  pipeline_loader  →  [Bias, Dark, Flat, Extract, …]
"""

import logging

from ..models import Observation
from .pipeline_loader import load_pipelines

log = logging.getLogger("spectro.plugin.reduction")


class ReductionPlugin:
    """
    Orchestrates the reduction chain by running, in order, the sub-plugins
    declared in the current observation's program YAML.
    """

    async def run(self, obs: Observation) -> Observation:
        raw_files = obs.sequencer_data.get("star_spectra_images")
        if not raw_files:
            raise RuntimeError("REDUCTION: no raw file (sequencer_data['star_spectra_images'] empty)")

        program = obs.scheduler_data.get("program")
        if not program:
            raise RuntimeError("REDUCTION: program missing from scheduler_data")

        _, red_pipeline = load_pipelines(program)
        log.info(
            f"[REDUCTION] Starting reduction '{program}' — "
            f"{len(raw_files)} raw file(s)"
        )

        # Each sub-plugin reads/writes obs.reduction_data directly
        # (and reads obs.sequencer_data["star_spectra_images"] set by SEQUENCER as input --
        # a shared key, not the name of the sub-plugin that produced it)
        for sub in red_pipeline:
            step = sub.__class__.__name__
            log.info(f"[REDUCTION] → {step}")
            try:
                obs = await sub.execute(obs)
            except Exception as e:
                log.error(f"[REDUCTION] Step {step} failed: {e}")
                raise

        log.info(
            f"[REDUCTION] Reduction complete — "
            f"{len(obs.reduction_data.get('extract', {}).get('reduced_files', []))} spectrum(s) produced"
        )
        return obs

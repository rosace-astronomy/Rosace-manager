"""
SEQUENCER plugin — Acquisition orchestrator.
The pipeline (list of sub-plugins) is loaded dynamically from the
observation's scientific program YAML (obs.scheduler_data["program"]), set
by SCHEDULER — so it's reloaded for every observation, not once and for
all at startup. The active hardware profile's own equipment-specific steps
(pipeline_loader.py::hardware_sequencer_prefix(), e.g. an AM3 mount's
home/sync step) are prepended before this list, so a program YAML never
needs to know or declare anything about the equipment it happens to run on.

Structure:
    programs/<program>.yaml  →  pipeline_loader  →  [sub-plugin, sub-plugin, …]
"""

import logging

from .. import step_progress
from ..models import Observation
from .pipeline_loader import hardware_sequencer_prefix, load_pipelines, load_program_yaml

log = logging.getLogger("spectro.plugin.sequencer")


class SequencerPlugin:
    """
    Orchestrates the acquisition sequence by running, in order, the
    sub-plugins declared in the current observation's program YAML.
    """

    async def run(self, obs: Observation) -> Observation:
        program = obs.scheduler_data.get("program")
        if not program or "target" not in obs.scheduler_data:
            raise RuntimeError("SEQUENCER: scheduler_data incomplete (target/program)")
        if not obs.obs_dir:
            raise RuntimeError("SEQUENCER: observation directory (obs_dir) not set")

        # Manifest set as the very first action, before any sub-plugin -- survives a
        # crash anywhere in SEQUENCER, REDUCTION or SHARING (see models.py).
        program_data = load_program_yaml(program)
        obs.program_file = f"{program}.yaml"
        obs.program_steps = {
            # Same prefix load_pipelines() below prepends when it actually builds
            # seq_pipeline -- both read it from the same function, so the step
            # names here can never drift out of sync with the instances they're
            # zipped against below.
            "sequencer": hardware_sequencer_prefix() + list(program_data["sequencer"]),
            "reduction": list(program_data["reduction"]),
            "sharing": program_data["sharing"],
        }

        seq_pipeline, _ = load_pipelines(program)
        log.info(
            f"[SEQUENCER] Starting sequence '{program}' for {obs.scheduler_data.get('queried_name')}"
        )

        # Each sub-plugin reads/writes obs.sequencer_data directly
        for step_name, sub in zip(obs.program_steps["sequencer"], seq_pipeline):
            step = sub.__class__.__name__
            log.info(f"[SEQUENCER] → {step}")
            step_progress.set_step(obs.id, "sequencer", step_name, obs.program_steps["sequencer"])
            try:
                obs = await sub.execute(obs)
            except Exception as e:
                log.error(f"[SEQUENCER] Step {step} failed: {e}")
                raise

        log.info("[SEQUENCER] Sequence complete")
        return obs

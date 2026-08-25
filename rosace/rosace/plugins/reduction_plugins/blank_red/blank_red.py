"""
Minimal REDUCTION sub-plugin — a template for writing a new one.

THE SUB-PLUGIN CONTRACT
------------------------
Loaded dynamically by pipeline_loader.py from the program YAML that
declares it (an entry in that program's `reduction:` list) — reloaded
fresh for every observation, never cached. execute(self, obs) -> Observation
is the only method to implement (see sub_plugin_base.py::SubPlugin). It
receives the whole Observation object — every casier collected so far, not
just REDUCTION's own — and reads whichever specific key(s) it needs directly
from it: this is an ordinary implementation choice, not a dependency the
framework tracks or enforces. If a key you need isn't there, raise an
explicit error naming it — never guess or fall back silently.

This example reads obs.sequencer_data["star_spectra_images"] (SEQUENCER's own
output, always present by the time REDUCTION runs) — a purpose-named,
shared key, not the name of whichever SEQUENCER sub-plugin actually
produced the raw files: this REDUCTION sub-plugin must never need to know
that name, only the fixed key every acquisition sub-plugin writes to. A
real REDUCTION sub-plugin further down a pipeline would typically read
whatever an earlier REDUCTION step wrote instead (e.g.
obs.reduction_data["bias"]) — same pattern, just a different key, entirely
up to what that sub-plugin needs.

You *may* write a result into obs.reduction_data under your own declared
name:

    obs.reduction_data["blank_red"] = {...}

...but only if you have something genuinely descriptive to add to the
observation record — not by default (a cleanup step with nothing to
report writes no casier at all). And whether a previous step ran/succeeded
is not your concern to verify: the orchestrator's own try/except already
guarantees a failed step stops the whole sequence before you'd ever run.
Reading a previous casier (like this example does with
sequencer_data["star_spectra_images"]) is for its actual *data*, not to
check that the step that wrote it "succeeded" — if that data genuinely
isn't there, raise an explicit error naming what's missing, same as any
other required input.

No config of your own — all configuration lives in conf/, like the rest of
the app. No backend axis either: REDUCTION sub-plugins process
already-acquired files, no hardware dependency — unlike SEQUENCER, there's
never a "<name>_internal.py" variant.

LOGGING
--------
The orchestrator already announces each step before running it
("[REDUCTION] → YourPlugin") and logs a failure itself if execute() raises
— don't repeat that with your own "starting X" line, it's redundant. Log
once you have something informative to report: parameters used, a count,
or the outcome. Exception: if your sub-plugin has several genuinely
distinct internal phases (not just one action), a log line per phase is
legitimate.

TO WRITE YOUR OWN REDUCTION SUB-PLUGIN
-----------------------------------------
1. Copy this whole directory (plugins/reduction_plugins/blank_red/) to
   plugins/reduction_plugins/<your_name>/, rename this file and its class.
   <your_name> must be all-lowercase (letters, digits, underscores only) —
   enforced at server startup, see pipeline_loader.py::validate_subplugin_catalogue().
2. Implement your own execute(obs).
3. Write plugins/reduction_plugins/<your_name>/test_<your_name>.py.
4. No registry to edit — add "<your_name>" to a program YAML's
   reduction: list, wherever in the order you want it to run.
"""

from __future__ import annotations

import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.blank_red")


class BlankRedPlugin(SubPlugin):
    """Minimal example REDUCTION sub-plugin — see module docstring for the contract."""

    async def execute(self, obs: Observation) -> Observation:
        if "star_spectra_images" not in obs.sequencer_data:
            raise RuntimeError(
                "BLANK_RED: sequencer_data['star_spectra_images'] missing — "
                "no acquisition sub-plugin ran before BLANK_RED"
            )
        raw_files = obs.sequencer_data["star_spectra_images"]

        log.info(f"[BLANK_RED] Example REDUCTION sub-plugin on {len(raw_files)} file(s)")

        obs.reduction_data["blank_red"] = {"processed_files": raw_files}
        return obs

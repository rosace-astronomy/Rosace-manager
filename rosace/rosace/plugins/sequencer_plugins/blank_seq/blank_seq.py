"""
Minimal SEQUENCER sub-plugin — a template for writing a new one.

THE SUB-PLUGIN CONTRACT
------------------------
Loaded dynamically by pipeline_loader.py from the program YAML that
declares it (an entry in that program's `sequencer:` list) — reloaded
fresh for every observation, never cached. execute(self, obs) -> Observation
is the only method to implement (see sub_plugin_base.py::SubPlugin). It
receives the whole Observation object — every casier collected so far, not
just SEQUENCER's own — and reads whichever specific key(s) it needs directly
from it: this is an ordinary implementation choice, not a dependency the
framework tracks or enforces. If a key you need isn't there, raise an
explicit error naming it — never guess or fall back silently.

You *may* write a result into obs.sequencer_data under your own declared
name:

    obs.sequencer_data["blank_seq"] = {...}

...but only if you have something genuinely descriptive to add to the
observation record (data about what the observation contains -- images,
positions, computed values) -- not by default. Don't repeat data that's
already elsewhere in obs (e.g. the target's name, already in
scheduler_data) just to "complete" your casier. And whether a previous
step ran/succeeded is not your concern to verify: the orchestrator's own
try/except already guarantees a failed step stops the whole sequence
before you'd ever run -- checking a previous casier's presence "just in
case" is dead code that can never actually catch anything. If you
genuinely need a previous step's *data* to do your own job, read it
directly and raise an explicit error naming what's missing if it isn't
there -- that's a real input dependency, not a completion check. This
example below has nothing of its own to report, so it writes no casier at
all.

No config of your own — all configuration lives in conf/, like the rest of
the app.

LOGGING
--------
The orchestrator already announces each step before running it
("[SEQUENCER] → YourPlugin") and logs a failure itself if execute() raises
— don't repeat that with your own "starting X" line, it's redundant. Log
once you have something informative to report: parameters used, a count,
or the outcome. Exception: if your sub-plugin has several genuinely
distinct internal phases (not just one action), a log line per phase is
legitimate.

BACKEND AXIS (SEQUENCER only)
-------------------------------
SEQUENCER sub-plugins are the only family with a backend axis
(SEQUENCER_BACKEND, config.py): the plain filename (<name>/<name>.py, this
file) is canonical, used whenever SEQUENCER_BACKEND="indi". An optional
<name>/<name>_internal.py variant is loaded instead when
SEQUENCER_BACKEND="internal" — but only where a demo-mode counterpart is
actually needed; its absence (like here) is normal, not a gap.

TO WRITE YOUR OWN SEQUENCER SUB-PLUGIN
-----------------------------------------
1. Copy this whole directory (plugins/sequencer_plugins/blank_seq/) to
   plugins/sequencer_plugins/<your_name>/, rename this file and its class.
   <your_name> must be all-lowercase (letters, digits, underscores only) —
   enforced at server startup, see pipeline_loader.py::validate_subplugin_catalogue().
2. Implement your own execute(obs).
3. Write plugins/sequencer_plugins/<your_name>/test_<your_name>.py.
4. No registry to edit — add "<your_name>" to a program YAML's
   sequencer: list, wherever in the order you want it to run.
"""

from __future__ import annotations

import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.blank_seq")


class BlankSeqPlugin(SubPlugin):
    """Minimal example SEQUENCER sub-plugin — see module docstring for the contract."""

    async def execute(self, obs: Observation) -> Observation:
        queried_name = obs.scheduler_data.get("queried_name") or "inconnue"

        log.info(f"[BLANK_SEQ] Example SEQUENCER sub-plugin for {queried_name}")

        # No casier: this example has nothing descriptive of its own to report.
        return obs

"""
Minimal SHARING sub-plugin — a template for writing a new one, and the only
one that exists today (every program currently points to it via
`sharing: blank_share` — real per-program distribution logic is a future chantier).

THE SHARING SUB-PLUGIN CONTRACT
--------------------------------
`SharingPlugin.run()` (`plugins/sharing.py`) resolves and calls exactly one
sub-plugin per observation — the one declared under the singular `sharing:`
key in the active program YAML (not a list, unlike `sequencer:`/`reduction:`).
Its `execute(self, obs) -> Observation` reads whatever it needs from the
observation — preferably its own files (the YAML dossier, `obs.obs_dir`),
never assuming that a given REDUCTION sub-plugin ran, since every program
declares its own list — and does whatever this program needs to distribute
results (email, FTP, publish to an API...). Nothing to share is not an
error: the observation itself succeeded. It *may* write its own nested key into
`obs.sharing_data`, keyed by its own declared name — but only if it has
something genuinely descriptive to add to the observation record, not by
default; whether it "succeeded" is the log's concern, not the YAML's (see
CLAUDE.md's Plugin system section).

If a program needs to notify several destinations, that happens *inside*
this one sub-plugin's own logic (e.g. loop over a list of recipients) — the
mechanism deliberately only supports one sub-plugin per program, not a list.

PLACEHOLDER: the execute() below only logs the observation dossier — real
email/FTP/API integration is a separate future chantier, expected to work
from the observation's files, like ARCHIVE.

TIME BUDGET
-----------
No TIME_CATEGORY here (SEQUENCER sub-plugins only): SHARING runs in the
post-observation thread, in parallel with the next observations, so it
takes no telescope time and is not on the night's time line.

DISPLAY_NAME (GUI label)
--------------------------
Right after this docstring, every sub-plugin declares its own short
DISPLAY_NAME constant — a human-readable label used to name this step in
the GUI. Not currently displayed for SHARING (a program declares exactly
one, so there's no "current step" chip row to build) — kept here purely
for catalogue consistency with SEQUENCER/REDUCTION, same reasoning as the
module-docstring convention above applying to all three families.

LOGGING
--------
The orchestrator already announces which sub-plugin it's calling
("[SHARING] Diffusion via 'YourPlugin'") and would log a failure itself if
execute() raised — don't repeat that with your own "starting X" line, it's
redundant. Log once you have something informative to report: parameters
used, a count, or the outcome. Exception: if your sub-plugin has several
genuinely distinct internal phases (not just one action), a log line per
phase is legitimate.

TO WRITE YOUR OWN SHARING SUB-PLUGIN
--------------------------------------
1. Copy this whole directory (plugins/sharing_plugins/blank_share/) to
   plugins/sharing_plugins/<your_name>/, rename this file and its class.
   <your_name> must be all-lowercase (letters, digits, underscores only) —
   enforced at server startup, see pipeline_loader.py::validate_subplugin_catalogue().
2. Implement your own execute(obs) — read whatever you need from obs (see
   the contract above), do your real distribution, and write
   obs.sharing_data["<your_name>"] = {...} only if you have something
   descriptive to record.
3. Write plugins/sharing_plugins/<your_name>/test_<your_name>.py.
4. No registry to edit — set `sharing: <your_name>` in the program YAML.
"""

from __future__ import annotations

DISPLAY_NAME = "Blank"

import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.sharing.blank_share")


class BlankSharePlugin(SubPlugin):
    """Placeholder distribution — see module docstring for the contract."""

    async def execute(self, obs: Observation) -> Observation:
        log.info(f"[SHARING] Placeholder: nothing shared yet (observation dossier {obs.yaml_file})")
        return obs

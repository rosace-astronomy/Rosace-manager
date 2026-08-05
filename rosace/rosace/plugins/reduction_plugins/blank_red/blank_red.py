"""
Minimal REDUCTION sub-plugin — a template for writing a new one.

THE SUB-PLUGIN CONTRACT
------------------------
Loaded dynamically by pipeline_loader.py from the programme YAML that
declares it (an entry in that programme's `reduction:` list) — reloaded
fresh for every observation, never cached. execute(self, obs) -> Observation
is the only method to implement (see sub_plugin_base.py::SubPlugin). It
receives the whole Observation object — every casier collected so far, not
just REDUCTION's own — and reads whichever specific key(s) it needs directly
from it: this is an ordinary implementation choice, not a dependency the
framework tracks or enforces. If a key you need isn't there, raise an
explicit error naming it — never guess or fall back silently.

This example reads obs.sequencer_data["expose"]["raw_files"] (SEQUENCER's
own output, always present by the time REDUCTION runs). A real REDUCTION
sub-plugin further down a pipeline would typically read whatever an earlier
REDUCTION step wrote instead (e.g. obs.reduction_data["bias"]) — same
pattern, just a different key, entirely up to what that sub-plugin needs.

Write your own result into obs.reduction_data under your own declared name
— presence of the key means you completed, no separate "done" flag needed:

    obs.reduction_data["blank_red"] = {...}

No config of your own — all configuration lives in conf/, like the rest of
the app. No backend axis either: REDUCTION sub-plugins process
already-acquired files, no hardware dependency — unlike SEQUENCER, there's
never a "<name>_internal.py" variant.

TO WRITE YOUR OWN REDUCTION SUB-PLUGIN
-----------------------------------------
1. Copy this whole directory (plugins/reduction_plugins/blank_red/) to
   plugins/reduction_plugins/<your_name>/, rename this file and its class.
   <your_name> must be all-lowercase (letters, digits, underscores only) —
   enforced at server startup, see pipeline_loader.py::validate_subplugin_catalogue().
2. Implement your own execute(obs).
3. Write plugins/reduction_plugins/<your_name>/test_<your_name>.py.
4. No registry to edit — add "<your_name>" to a programme YAML's
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
        if "expose" not in obs.sequencer_data:
            raise RuntimeError("BLANK_RED : sequencer_data['expose'] manquant — EXPOSE doit avoir tourné avant BLANK_RED")
        raw_files = obs.sequencer_data["expose"]["raw_files"]

        log.info(f"[BLANK_RED] Exemple de sous-plugin REDUCTION sur {len(raw_files)} fichier(s)")

        obs.reduction_data["blank_red"] = {"processed_files": raw_files}
        return obs

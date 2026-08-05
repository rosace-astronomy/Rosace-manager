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

Write your own result into obs.sequencer_data under your own declared name
— presence of the key means you completed, no separate "done" flag needed:

    obs.sequencer_data["blank_seq"] = {...}

No config of your own — all configuration lives in conf/, like the rest of
the app.

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
        target = obs.scheduler_data.get("target")
        target_name = target.name if target else "inconnue"

        log.info(f"[BLANK_SEQ] Exemple de sous-plugin SEQUENCER pour {target_name}")

        obs.sequencer_data["blank_seq"] = {"target": target_name}
        return obs

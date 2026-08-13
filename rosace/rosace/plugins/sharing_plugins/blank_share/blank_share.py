"""
Minimal SHARING sub-plugin — a template for writing a new one, and the only
one that exists today (every program currently points to it via
`sharing: blank_share` — real per-program distribution logic is a future chantier).

THE SHARING SUB-PLUGIN CONTRACT
--------------------------------
`SharingPlugin.run()` (`plugins/sharing.py`) resolves and calls exactly one
sub-plugin per observation — the one declared under the singular `sharing:`
key in the active program YAML (not a list, unlike `sequencer:`/`reduction:`).
Its `execute(self, obs) -> Observation` reads `obs.reduction_data["extract"]
["reduced_files"]` (REDUCTION's own output — always present by the time
SHARING runs) and does whatever this program needs to distribute results
(email, FTP, publish to an API...). It writes its own nested key into
`obs.sharing_data`, keyed by its own declared name — presence of the key
means it completed, no separate `"done"` flag needed, same convention as
every other sub-plugin family (see CLAUDE.md).

If a program needs to notify several destinations, that happens *inside*
this one sub-plugin's own logic (e.g. loop over a list of recipients) — the
mechanism deliberately only supports one sub-plugin per program, not a list.

PLACEHOLDER: the actual distribution logic below is a stand-in (it only logs
each file) — real email/FTP/API integration is a separate future chantier.

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
2. Implement your own execute(obs) — read whatever you need from obs
   (reduction_data["extract"]["reduced_files"] at minimum), do your real
   distribution, and write obs.sharing_data["<your_name>"] = {...}.
3. Write plugins/sharing_plugins/<your_name>/test_<your_name>.py.
4. No registry to edit — set `sharing: <your_name>` in the program YAML.
"""

from __future__ import annotations

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.sharing.blank_share")


class BlankSharePlugin(SubPlugin):
    """Minimal/placeholder distribution of the reduced files — see module docstring for the contract."""

    async def execute(self, obs: Observation) -> Observation:
        reduced_files = obs.reduction_data.get("extract", {}).get("reduced_files", [])
        if not reduced_files:
            raise RuntimeError("SHARING : aucun fichier réduit (reduction_data['extract']['reduced_files'] vide)")

        log.info(f"[SHARING] Diffusion de {len(reduced_files)} fichier(s)")
        for f in reduced_files:
            await asyncio.sleep(0.3)  # TODO : envoi email / API réel
            log.info(f"[SHARING] Diffusé : {f}")

        obs.sharing_data["blank_share"] = {"shared_files": reduced_files}
        return obs

"""
Minimal SCHEDULER plugin — a template for writing a new one.

THE SCHEDULER CONTRACT
-----------------------
main.py's session_runner() calls the active SCHEDULER plugin's run(obs) once
per observation attempt. It must answer within a few seconds (never sleep
inside run() waiting for a condition — see the "no blocking wait" rule in
CLAUDE.md) and populate obs.scheduler_data with at least these keys:

    target       : models.Target         — the star to observe (ra/dec in
                    degrees, within Target's own field constraints).
    queried_name : str                   — the name this scheduler used to
                    pick the target (may differ from target.name — an alias,
                    a hardcoded label, whatever is meaningful to this
                    scheduler's own logic/UI display).
    program    : str                   — the scientific program this
                    observation is attached to, for bookkeeping in
                    rosace.db. Does not have to be a real targets.db
                    program (see below).
    priority     : int                   — this observation's priority,
                    only meaningful relative to other targets from the same
                    scheduling pass; purely informational downstream.
    exposure     : models.ExposureConfig — exposure_time may be None if
                    unknown (a later stage may compute it from magnitude).

Optional keys — set them when the scheduler knows them, leave them out
otherwise (recorded in rosace.db when present, NULL there when absent):

    proposed_by          : str          — who proposed this target inside
                    the scheduler (e.g. the selector's planner/program).
    estimated_duration_s : int | float  — expected duration of the whole
                    observation, in seconds.

Any other key a scheduler writes (e.g. the selector's "reason") is its own
information: kept in the observation dossier, read by nobody else.

If nothing is observable right now, set obs.scheduler_data["target"] = None
and return — this is not an error, main.py waits SCHEDULER_RETRY_SECONDS and
calls SCHEDULER again.

The orchestrator owns the Observation (creates it, writes it to disk, hands
it to each stage) and reads these keys itself: main.py checks target (None →
wait and retry), database.py records them in the observation's row, and the
dossier's filename uses queried_name. Downstream stages (SEQUENCER,
REDUCTION, the GUI) read them too — none of them know or care which
SCHEDULER plugin produced them. This is the only thing that has to stay compatible
across schedulers; nothing else is shared, and this contract is deliberately
NOT checked at runtime here (see conftest.py's assert_scheduler_contract(),
used by this plugin's own tests instead — keep it that way for your own
scheduler too: check the contract in tests, not in the plugin itself).

This plugin never touches targets.db — the three targets below are
hardcoded, valid Target instances. "Be_stars" is used as the program label
purely as a placeholder (these three stars are not actually Be stars) — a
real scheduler would normally set whatever program it is actually
scheduling for.

TO WRITE YOUR OWN SCHEDULER
----------------------------
1. Copy this whole directory (plugins/schedulers/blank/) to
   plugins/schedulers/<your_name>/, rename the class below.
2. Implement your own run(obs) — read whatever data source you like
   (a file, a database, a fixed list...), and only ever write into
   obs.scheduler_data, following the contract above.
3. Write plugins/schedulers/<your_name>/test_scheduler.py — see this
   plugin's own test_scheduler.py for the pattern, and call
   conftest.py's assert_scheduler_contract() on any obs where you selected
   a target.
4. Register your class in plugins/schedulers/__init__.py's _build_registry()
   (the only list of schedulers).
5. Set active_scheduler: <your_name> in conf/config.yaml.
"""

import logging

from ....database import db_count_observations_for_target
from ....models import Observation, Target, ExposureConfig

log = logging.getLogger("spectro.plugin.scheduler.blank")

# Placeholder program label — see module docstring.
_PROGRAM = "Be_stars"

_TARGETS = [
    Target(name="Vega", ra=279.234735, dec=38.783611, magnitude_v=0.03),
    Target(name="Arcturus", ra=213.915300, dec=19.182410, magnitude_v=-0.05),
    Target(name="Castor", ra=113.649413, dec=31.888276, magnitude_v=1.58),
]


class BlankPlugin:
    """Hands out Vega, then Arcturus, then Castor — one per call, skipping
    any of the three already observed tonight. See the module docstring for
    the full SCHEDULER contract this (and any other) scheduler must honor."""

    async def run(self, obs: Observation) -> Observation:
        for priority, target in enumerate(_TARGETS, start=1):
            already_observed = await db_count_observations_for_target(_PROGRAM, target.name)
            if already_observed >= 1:
                continue

            obs.scheduler_data["target"] = target
            obs.scheduler_data["queried_name"] = target.name
            obs.scheduler_data["program"] = _PROGRAM
            obs.scheduler_data["priority"] = priority
            obs.scheduler_data["exposure"] = ExposureConfig(exposure_time=None)
            log.info(f"[SCHEDULER:blank] Target selected: {obs.scheduler_data['queried_name']}")
            return obs

        log.info("[SCHEDULER:blank] No target left (Vega/Arcturus/Castor already observed tonight)")
        obs.scheduler_data["target"] = None
        return obs

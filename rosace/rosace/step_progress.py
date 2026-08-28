"""
In-memory, ephemeral tracking of which sub-plugin step is currently running
within SEQUENCER/REDUCTION, per observation. Deliberately never persisted to
rosace.db (a sub-plugin name is a plugin identity -- the DB must stay
agnostic to which plugins exist, same principle as SUBPLUGIN_PARAMS in
config.py) and deliberately not pushed live over /ws either -- the GUI's
existing ~3s poll (GET /observations/steps) is enough, since a completed
step simply shows as done at the next poll. Lost on server restart, same as
main.py's own `state` singleton -- nothing here needs to survive one.

Tracked per (obs_id, stage) rather than one "current stage" per obs_id --
SEQUENCER and REDUCTION never run concurrently for the *same* observation,
but the GUI shows one step-progress line per stage, and keeps both visible
for a few seconds after the whole observation finishes (see main.ts): once
SEQUENCER completes and REDUCTION starts, SEQUENCER's own last-known step
list must still be there to render its line as "all done", not be
overwritten by REDUCTION's entry.
"""

from __future__ import annotations

_current_steps: dict[str, dict[str, dict]] = {}  # {obs_id: {stage: {"step": ..., "steps": [...]}}}


def set_step(obs_id: str, stage: str, step: str, steps: list[str]) -> None:
    _current_steps.setdefault(obs_id, {})[stage] = {"step": step, "steps": steps}


def get_all() -> dict[str, dict[str, dict]]:
    return {
        obs_id: {stage: dict(entry) for stage, entry in stages.items()}
        for obs_id, stages in _current_steps.items()
    }

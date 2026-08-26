"""
SEQUENCER sub-plugin -- ZWO AM3 mount home/park recovery, "indi" backend only.

Re-parks then unparks the mount to recover a reliable position reference:
the driver derives Home's sky coordinate itself from the current time and
site once unparked, so no explicit SYNC is needed -- and none is issued,
since a SYNC would also feed the mount's own pointing-correction model,
distorting subsequent GOTOs. AM3/driver-specific, so it lives in its own
SEQUENCER sub-plugin rather than INIT_SESSION, kept mount-agnostic.

Runs once per session, before the first real pointing (placed before
pointing_72ed in the program YAML) -- not before every observation, since
re-homing is comparatively slow and the mount is expected to hold its
position for the rest of a session that stays connected throughout.
Tracked via a module-level "last session_id already homed" value: cheap,
in-process only (lost on a server restart -- same as every other in-memory
session state, e.g. main.py's own `state` singleton -- but *not* lost
across a plain stop/restart of the session within the same server
process, since sub-plugin modules are imported once via
importlib.import_module() and then reused from sys.modules, never
re-executed). Reset implicitly whenever session_id changes, rather than
through any explicit call from INIT_SESSION, so INIT_SESSION never needs
to know this sub-plugin -- or the AM3 -- exists.

No casier: this step has nothing descriptive to add to the observation
record (see CLAUDE.md's casier-vs-flow-tracking principle) -- only "it ran
once for session X", which the module-level tracking already captures,
not observation data.
"""

from __future__ import annotations

import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.home_sync_am3")

_last_homed_session_id: str | None = None


class HomeSyncAm3Plugin(SubPlugin):
    """Once per session, before the first real pointing: park the AM3 mount
    to its Home reference then unpark it -- recovers a reliable position
    reference after a mid-session reconnect."""

    async def execute(self, obs: Observation) -> Observation:
        global _last_homed_session_id
        if obs.session_id == _last_homed_session_id:
            return obs

        mount = obs.hardware["mount"]
        await mount.park()
        await mount.unpark()

        _last_homed_session_id = obs.session_id
        log.info(f"[HOME_SYNC_AM3] Mount re-homed for session {obs.session_id}")
        return obs

"""
SEQUENCER sub-plugin -- ZWO AM3 mount home+sync recovery, "indi" backend only.

The AM3 has absolute encoders that are supposed to survive a disconnect/
reconnect on their own -- but in practice (found 2026-08-18, during a real
observing session) the mount's reported position was lost after several
manual reconnects. Re-parking (which slews to the mount's fixed mechanical
Home reference and locks it there), then unparking (which unlocks motion
again without moving), then syncing the driver's internal position to
Home's own known sky coordinate, recovers a reliable position reference.
This recovery procedure is AM3/driver-specific -- a mount whose absolute
encoders actually survive a reconnect would never need it -- which is why
it lives in its own SEQUENCER sub-plugin instead of INIT_SESSION, kept
mount-agnostic.

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

from ....config import SITE_LONGITUDE_DEG
from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.home_sync_am3")

_last_homed_session_id: str | None = None


class HomeSyncAm3Plugin(SubPlugin):
    """Once per session, before the first real pointing: park the AM3 mount
    to its Home reference, unpark it, and sync the driver's position to
    Home's own known sky coordinate -- recovers a reliable position
    reference after a mid-session reconnect."""

    async def execute(self, obs: Observation) -> Observation:
        global _last_homed_session_id
        if obs.session_id == _last_homed_session_id:
            return obs

        mount = obs.hardware["mount"]
        await mount.park()
        await mount.unpark()
        ra, dec = await mount.get_home_position(SITE_LONGITUDE_DEG)
        await mount.sync_to_coordinates(ra, dec)

        _last_homed_session_id = obs.session_id
        log.info(f"[HOME_SYNC_AM3] Mount re-homed and synced for session {obs.session_id}")
        return obs

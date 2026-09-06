"""
SEQUENCER sub-plugin -- ZWO AM3 mount home/park recovery, "indi" backend only.

Re-parks then unparks the mount to recover a reliable position reference,
then syncs it to its own theoretically-derived Home position (RA=local
sidereal time, DEC=+90, JNow) -- telling the mount "you are here" so its
internal position reference matches reality even when polar alignment or
the configured site coordinates aren't perfect (park/unpark alone only
gives a *mechanical* zero-position reference; it doesn't by itself close
any residual gap between that and the real sky).

PROVISIONAL, reinstated 2026-09-06 for live field-testing (see HISTORY.md):
a sync here was removed 2026-08-26 after a distorting side effect --
found on the INDI Telescope Simulator, which activates a multi-point
pointing-correction model after any SYNC -- but the real AM3 has no
`ALIGNMENT_SUBSYSTEM_ACTIVE` property at all, so that specific mechanism
may simply not exist on this driver. Restored to test that directly on
real hardware before deciding whether/how to keep it (e.g. syncing to a
*plate-solved, measured* position instead of this computed one -- the
other, still-valid half of the 2026-08-26 finding: never sync to a value
that hasn't been verified against the real sky). `on_coord_set('TRACK')`
is re-armed immediately after the sync (inside `sync_to_coord()` itself)
so every following ordinary slew behaves normally regardless of this step.
AM3/driver-specific, so it lives in its own SEQUENCER sub-plugin rather
than INIT_SESSION, kept mount-agnostic.

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

Field-test safety net (2026-09-06, see HISTORY.md): the sync itself is
wrapped so it can never turn into a session-blocking failure -- if
get_home_position()/sync_to_coordinates() raises (e.g. an INDI property
mismatch on this driver, already seen once with TIME_UTC), it's logged as
a WARNING and this step still completes with the pre-existing park/unpark
behavior, exactly as if SYNC_TO_COMPUTED_HOME were False. Without this,
the module-level "already done" flag -- only ever set after a fully
successful run -- would never get set, and every next SCHEDULER retry
would hit the exact same failure again, forever, blocking the whole
session. SYNC_TO_COMPUTED_HOME below is a one-line kill switch for the
sync itself (park/unpark always still happen): flip to False and restart
the server if the sync turns out to make pointing worse rather than
better -- a "successful but harmful" outcome (e.g. a distorting alignment
model after all) that the WARNING-and-continue safety net above cannot
catch by itself, since no exception is raised in that case.
"""

from __future__ import annotations

DISPLAY_NAME = "Home"

import logging

from ....config import SITE_LONGITUDE_DEG
from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.home_sync_am3")

_last_homed_session_id: str | None = None

# One-line kill switch for tonight's field test -- see module docstring.
# Flip to False (and restart the server) to fall back to the pre-2026-09-06
# behavior (park/unpark only, no sync) without touching anything else.
SYNC_TO_COMPUTED_HOME = True


class HomeSyncAm3Plugin(SubPlugin):
    """Once per session, before the first real pointing: park the AM3 mount
    to its Home reference, unpark it, then (unless disabled, see
    SYNC_TO_COMPUTED_HOME) sync it to that same theoretically-derived Home
    position -- recovers a reliable position reference after a mid-session
    reconnect, and closes the gap between the mount's mechanical zero and
    the real sky (see module docstring)."""

    async def execute(self, obs: Observation) -> Observation:
        global _last_homed_session_id
        if obs.session_id == _last_homed_session_id:
            return obs

        mount = obs.hardware["mount"]
        await mount.park()
        await mount.unpark()

        if SYNC_TO_COMPUTED_HOME:
            try:
                home_ra, home_dec = await mount.get_home_position(SITE_LONGITUDE_DEG)
                await mount.sync_to_coordinates(home_ra, home_dec)
                log.info(
                    f"[HOME_SYNC_AM3] Mount re-homed and synced to Home "
                    f"(RA={home_ra:.4f}, DEC={home_dec:.4f}) for session {obs.session_id}"
                )
            except Exception as e:
                log.warning(
                    f"[HOME_SYNC_AM3] Sync to Home failed, continuing with park/unpark "
                    f"only for session {obs.session_id}: {e}"
                )
        else:
            log.info(f"[HOME_SYNC_AM3] Mount re-homed (sync disabled) for session {obs.session_id}")

        _last_homed_session_id = obs.session_id
        return obs

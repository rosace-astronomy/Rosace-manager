# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The session's time line: what the telescope is doing, as consecutive,
exclusive intervals recorded in rosace.db (table time_segments).

The orchestrator (main.py, plugins/sequencer.py) calls switch() at every
change of activity: switch() closes the current interval and opens the next
one, so the intervals always cover the whole session without gap or overlap
-- no time can leak out of the budget. Sub-plugins never call it: the
SEQUENCER records each step under the TIME_CATEGORY its sub-plugin declares
(pipeline_loader.time_category()). time_budget.py turns the intervals into
the night's budget at CLOSE_SESSION.

Generic: knows no sub-plugin, only the category names below. Never
blocking: a database error is a WARNING, the session goes on.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .database import db_insert_time_segment, db_mark_segments_failed

log = logging.getLogger("spectro.time_ledger")

# Categories a SEQUENCER sub-plugin may declare (TIME_CATEGORY). "pointing"
# and "science" are mixed: time_budget.py splits them with the observation's
# measurements (slew_s, guiding_settle_s / science_exposure_s).
STEP_CATEGORIES = (
    "slew",                 # moving the mount to the target, nothing else
    "pointing",             # slew + fine pointing + PHD2 settling (split by the budget)
    "homing",               # mount homing, once per session
    "science",              # science exposures + readout (split by the budget)
    "target_measurements",  # measurements on the target (focus, wide field, end guide image)
    "calibrations",         # dark, bias, lamp, flat, guiding dark, light source changes
    "other",                # anything else (and the blank_seq template)
)

# Categories recorded by the orchestrator itself.
ORCHESTRATOR_CATEGORIES = (
    "init", "scheduler", "waiting_for_target", "orchestrator", "waiting_post_obs", "close",
)


@dataclass
class _OpenSegment:
    started_at: datetime
    category: str
    step: str
    observation_id: str
    failed: bool = False


class TimeLedger:
    """Records the current session's time line (one session at a time)."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._current: _OpenSegment | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def start(self, session_id: str, category: str, at: datetime | None = None) -> None:
        """Starts a new session's time line with its first interval (nothing
        written yet: an interval is written when it closes)."""
        self._session_id = session_id
        self._current = _OpenSegment(at or datetime.now(timezone.utc), category, "", "")

    async def switch(self, category: str, step: str = "", observation_id: str = "") -> None:
        """Closes the current interval now and opens the next one."""
        if self._session_id is None:
            return
        now = datetime.now(timezone.utc)
        await self._close(now)
        self._current = _OpenSegment(now, category, step, observation_id)

    async def mark_failed(self, observation_id: str) -> None:
        """Marks an observation's intervals (recorded and current) as a failed attempt."""
        if self._session_id is None or not observation_id:
            return
        if self._current and self._current.observation_id == observation_id:
            self._current.failed = True
        try:
            await db_mark_segments_failed(observation_id)
        except Exception as e:
            log.warning(f"[TIME_LEDGER] Could not mark {observation_id} as failed: {e}")

    async def stop(self) -> None:
        """Closes the current interval and ends the session's time line."""
        if self._session_id is None:
            return
        await self._close(datetime.now(timezone.utc))
        self._current = None
        self._session_id = None

    async def _close(self, now: datetime) -> None:
        current = self._current
        if current is None or now <= current.started_at:
            return
        try:
            await db_insert_time_segment(
                self._session_id, current.started_at, now, current.category, current.step,
                current.observation_id, "failed" if current.failed else None,
            )
        except Exception as e:
            log.warning(f"[TIME_LEDGER] Could not record the '{current.category}' interval: {e}")


ledger = TimeLedger()

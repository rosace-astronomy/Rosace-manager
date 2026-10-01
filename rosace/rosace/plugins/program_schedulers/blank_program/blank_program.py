"""
Blank program planner — the template to copy, and the developer guide.

WHAT A PLANNER IS
-----------------
Rosace observes for several scientific programs. Each program has its own
planner, which knows the program's science (which targets, how often, when).
At every SCHEDULER step, the selector (plugins/schedulers/selector/) asks each
active planner for at most ONE candidate, checks the candidates, and picks the
next observation. Only the next observation is decided, never the whole night:
a planner answers "what would you observe now?", nothing more.

THE CALL
--------
    async def propose(self, ctx: SchedulingContext) -> Candidate | None

ctx (program_schedulers/__init__.py):
    now      UTC instant of the decision, the same for every planner. Always
             use it instead of reading the clock (tests simulate any time).
    site     latitude_deg, longitude_deg (east positive), elevation_m.
    program  the program whose YAML declares this planner (None for manual_list).
    params   the `params` block of that YAML, free-form: read your own keys
             with your own defaults (ctx.params.get("cadence_days", 15)).

Return None when nothing is to be done now (not an error).

THE ANSWER: Candidate
---------------------
    queried_name  star name, canonical or alias; the selector looks it up in
                  targets.db (unknown star -> candidate rejected).
    program       program whose YAML will run the observation (usually
                  ctx.program; manual_list uses the program of each line).
    priority      level 0 to 4, see below.
    exposure      ExposureConfig, as today: exposure_time is the duration of
                  ONE sub-exposure if known (program_stars.exposure_time),
                  otherwise None (computed from the magnitude). The split into
                  sub-exposures is decided at acquisition time by
                  star_spectra_acquisition, not here.
    window        time window (start_after / end_before, UTC) — not supported
                  yet: leave it None.
    reason        one readable sentence: why this target now. It goes to the
                  log and to the observation dossier ("last success 19 d ago").

PRIORITY LEVELS (the lower, the more urgent) — "what do we lose if we wait?"
-----------------------------------------------------------------------------
    0  PRIORITY_INTERRUPT  the event itself, irreplaceable (nova, outburst).
                           Will interrupt the running observation; not
                           implemented yet: today it only goes first.
    1  PRIORITY_SERIES     next element of a series this planner has started.
    2  PRIORITY_HIGH       the information is lost if not observed tonight.
    3  PRIORITY_ORDINARY   routine; can be caught up tomorrow.
    4  PRIORITY_FILLER     not needed, but better than an idle telescope.
A time constraint does not set the level: the planner chooses both.

HOW THE SELECTOR CHOOSES
------------------------
1. collect one candidate (or none) per active program;
2. keep the feasible ones (same checks as checks.py, see below);
3. lowest level first; at equal level: at level 1 the program of the previous
   observation keeps the hand, then manual_list before the other programs,
   then the program name (time shares will replace it later).

RULES FOR A PLANNER
-------------------
- Answer within planner_timeout_seconds (10 s by default). The planner runs in
  its own thread, with its own event loop: a slow or blocked planner never
  freezes Rosace, it only loses its turn (and is skipped while still running).
  Use the async helpers of targets.py and database.py as usual.
- At most one candidate, and make sure it is feasible: call
  checks.check_feasible(star, exposure, ctx.now, min_altitude_deg) and only
  propose a candidate for which it returns None. The selector re-runs the same
  checks; a rejection there means a planner bug.
- Keep no state between calls: everything you need (what was observed, when,
  with which result) is in rosace.db. A server restart then loses nothing.
  database.db_get_successes_for_target(program, target, ctx.now) gives the
  number of successful observations tonight and the time of the last one.
- Read-only: never write to targets.db, rosace.db or any file of the
  installation, never touch the hardware.
- Your own scientific criteria (minimum altitude, Moon, preferred hour angle,
  cadence...) are yours: apply them before choosing.

ERRORS
------
- One unusable target: log it and try the next one.
- Any exception: this program is skipped for this round only.
- SchedulerFatalError (plugins/schedulers): a configuration or data problem
  that retrying cannot fix; this program is disabled until the session ends.

RUNNING A SERIES
----------------
A series = observing one target continuously, with nothing else in between.
The selector knows nothing about series: the planner does everything.
- Before the first element, check the whole series: pass the series' total
  duration to check_feasible(..., duration_s=...).
- Propose the first element at its own level (2, 3 or 4), then every next
  element at level 1 (PRIORITY_SERIES), reading in rosace.db that the series
  has started, until its end time. A mount flip between two elements is
  accepted (each element is an ordinary observation).

DECLARING YOUR PROGRAM
----------------------
1. Copy this directory to plugins/program_schedulers/<your_name>/, rename the
   file to <your_name>.py and the class to <YourName>Scheduler
   (snake_case -> PascalCase + "Scheduler").
2. Add a `scheduler` block to your program's YAML (programs/<program>.yaml):
       scheduler:
         name: <your_name>
         params: { ... }        # whatever your planner reads from ctx.params
   A program is active as soon as its YAML has a `scheduler` key.
3. Write <your_name>/test_<your_name>.py: no candidate when nothing is to do,
   a valid Candidate otherwise (priority 0-4, program set, reason set), and
   your own rules — see this planner's test file for the pattern.

THIS EXAMPLE
------------
Proposes, at level 3, the first star linked to its program in targets.db
that has no successful observation tonight and is feasible now. Param:
min_altitude_deg (default: the installation floor only).
"""

import logging

from ....database import db_get_successes_for_target
from ....models import ExposureConfig
from ....targets import program_get_by_name, program_get_stars
from .. import PRIORITY_ORDINARY, Candidate, SchedulingContext
from ..checks import check_feasible

log = logging.getLogger("spectro.plugin.planner.blank_program")


class BlankProgramScheduler:

    async def propose(self, ctx: SchedulingContext) -> Candidate | None:
        program = await program_get_by_name(ctx.program) if ctx.program else None
        if program is None:
            log.warning(f"[SCHEDULER] Program '{ctx.program}' not found in targets.db")
            return None

        min_altitude_deg = ctx.params.get("min_altitude_deg")
        for star in await program_get_stars(program.id):
            successes, _ = await db_get_successes_for_target(program.name, star.name, ctx.now)
            if successes > 0:
                continue
            exposure = ExposureConfig(exposure_time=star.exposure_time)
            if check_feasible(star, exposure, ctx.now, min_altitude_deg) is not None:
                continue
            return Candidate(
                queried_name=star.name,
                program=program.name,
                priority=PRIORITY_ORDINARY,
                exposure=exposure,
                reason="not yet observed successfully tonight",
            )
        return None

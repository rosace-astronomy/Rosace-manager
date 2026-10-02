# Multi-program scheduling — specification v0.8

**Version 0.8 — 2 October 2026.** Implemented on branch `multi-program`: phase A (selector,
shared checks, `manual_list` and `blank_program` planners), phase B1 (failures, observation
columns, measurements) and phase B2 (windows and booked observations, series in the manual
list, time shares).
Earlier versions (0.1–0.55, in French) were working drafts.

## 1. Principles

1. **One selector** (the SCHEDULER stage plugin, `active_scheduler: selector`) and **one planner
   per scientific program**. The core of the design is what they exchange.
2. **A program exists through its YAML** (`programs/<name>.yaml`), which declares its planner in
   a `scheduler:` key. **`manual_list`** (observations listed by hand) is the minimal program,
   always present.
3. **Only the next observation is decided**, never the whole night. The past is read from
   `rosace.db`; planners keep no state between calls.
4. **Each planner proposes at most one candidate** per request and makes sure it is feasible,
   using the **shared checks** (section 4). The selector runs the same checks again, as a mere
   verification, then chooses.
5. **Priority and time constraint are separate.** Five priority levels (section 5).
6. **The selector knows nothing about the programs' science, nor about series**; `main.py`
   knows no plugin. The selector fills `scheduler_data` with the existing contract; switching
   back is one line: `active_scheduler: scheduler_rosace_2026`.
7. **Fixed objects only** (J2000 RA/Dec from `targets.db`); moving objects (comets, asteroids)
   will later add optional coordinates to the candidate.

## 2. Programs and planners

```yaml
program: Be_stars
scheduler:
  name: be_cadence        # plugins/program_schedulers/be_cadence/be_cadence.py
  share_percent: 70       # optional: target share of telescope time
  params: { ... }         # free-form, read by the planner
sequencer: [...]
reduction: [...]
sharing: blank_share
```

- **Active program**: every `programs/*.yaml` with a `scheduler` key (remove or comment the key
  to put a program to sleep). Without `manual_list.yaml`, the selector raises
  `SchedulerFatalError`.
- `manual_list.yaml` has no sequencer: each line of the list names the program whose YAML runs
  the observation (`manual_list` proposes, `Be_stars` observes).
- Code: `plugins/schedulers/selector/`; `plugins/program_schedulers/` (contract and loader in
  `__init__.py`, shared checks in `checks.py`, one directory per planner,
  `<name>/<name>.py`, class `<PascalName>Scheduler`; `blank_program/` is the template and
  its docstring the developer guide).

## 3. Selector ↔ planner exchange

```python
class ProgramScheduler(Protocol):
    async def propose(self, ctx: SchedulingContext) -> Candidate | None: ...
```

**Request** (`SchedulingContext`): `now` (UTC instant of the decision, the same for every
planner), `site`, `program` (`None` for manual_list), `params` (the YAML's `params` block).

**Candidate**:

| Field | Meaning |
|---|---|
| `queried_name` | Star name (canonical or alias). The selector resolves it in `targets.db` (coordinates, magnitude); unknown star → rejected. |
| `program` | Program whose YAML runs the observation. |
| `priority` | 0 to 4 (section 5). |
| `exposure` | As today: `exposure_time` = one sub-exposure if set in `targets.db`, else `None` (from magnitude). The split into sub-exposures stays in `star_spectra_acquisition`, unchanged. |
| `window` | `start_after` = start of the science exposures (the observation is launched `overhead_minutes` earlier); `end_before` = end of the whole observation (UTC). Launch still ahead, within `lookahead_minutes` (60) → **booked**. |
| `reason` | One readable sentence, logged and written to the observation dossier. |

**Threads and timeout.** Each planner runs in its own thread with its own event loop, bounded
by `planner_timeout_seconds` (10 s): a slow or blocked planner never freezes Rosace, it only
loses its turn. A thread cannot be killed: a planner that timed out is skipped until its thread
ends, so threads never pile up.

**Errors.** One unusable target: the planner logs it and moves on. Any exception: program
skipped for this round. `SchedulerFatalError`: program disabled until the session ends (for
`manual_list`, re-raised: the session stops).

## 4. Shared checks (`program_schedulers/checks.py`)

Used by planners to choose and by the selector to verify, so both apply the same rules.
Installation settings in `subplugin_params.selector` (`min_altitude_deg` 10,
`min_meridian_distance_deg` 0 — 3 on the 72ED, `overhead_minutes` 5,
`planner_timeout_seconds` 10), plus `horizon_file` and the site position (Core).

- `estimate_duration_s(star, exposure)` = total exposure, with the same function and settings
  as `star_spectra_acquisition` (`compute_exposure_time_and_count()`), + the fixed overhead.
- `check_feasible(star, exposure, now, min_altitude_deg=None, duration_s=None)` → `None` if
  feasible, else the reason. Altitude above the installation floor (and the planner's own
  minimum) and above the local horizon, sampled every 5 minutes over the whole duration; the
  pointing must not straddle the meridian (current rule, at start). A series planner passes the
  whole series' duration.
- `check_window(window, now, duration)` → `open`, `booked` or `refused` (closed, too short,
  launch beyond `lookahead_minutes`); `check_feasible(..., start=launch)` for a booked one.
- `check_not_recently_failed(program, target, now)` → refuses a target whose last observation
  for this program failed less than `failure_backoff_hours` (1 h) ago. Not applied at level 1:
  a series retries at once, its planner bounds the retries
  (`db_count_trailing_failures()`).
- `database.db_get_successes_for_target(program, target, now)` → number of successful
  observations (SEQUENCER completed) tonight and the time of the last one.

## 5. Priority levels — "what do we lose if we wait?"

| Level | Meaning |
|---|---|
| 0 | Interrupts the running observation (nova, outburst). Not implemented: today it only goes first. |
| 1 | Next element of a series already started — chosen by the planner running the series. |
| 2 | The information is lost if not observed tonight. |
| 3 | Ordinary. |
| 4 | Filler: not needed, but better than an idle telescope. |

A time constraint does not set the level; the planner chooses both.

## 6. Selection algorithm

```
1. COLLECT    one candidate (or none) per active program
2. FILTER     keep the feasible candidates (section 4); a candidate whose launch time
              is still ahead is set aside as a booked observation
3. PROTECT    drop any immediate candidate, not more urgent than a booked one, that
              would end after its launch time (start_after − overhead)
4. SORT       by level: 0, 1, 2, 3, 4
5. TIE-BREAK  at equal level, in this order:
                a. level 1: the program of the previous observation keeps the hand
                b. manual_list before the other programs
                c. a candidate with a window before one without
                   (between two windows, the one closing first)
                d. the program furthest behind its time share
                e. program name
6. DECIDE     first immediate one -> scheduler_data; only booked ones (or nobody)
              -> target = None, main.py asks again a minute later
```

Filling the time before a booked observation uses only the candidates already received. One
candidate per planner: while `manual_list` waits for a booked line, it proposes only a line
that ends before its launch (or a more urgent one), else the booked line itself. **Known
limit**: a more urgent candidate of another program can still delay a booked manual line;
filling with the candidates of every planner comes later.
`scheduler_data` keeps `target`, `queried_name`, `program`, `priority`, `exposure` and adds
`proposed_by`, `estimated_duration_s`, `reason`. One INFO line per round.

## 7. Time shares

- Time used by an observation = `ended_at − created_at` (end of SEQUENCER), overhead and
  failures included. Share obtained = program time / total time, rolling 60 days. Deficit =
  target − obtained, no cap. Only breaks ties at equal level.
- `share_percent` in the program YAML (optional); checked at the first selector call of each
  session: total below 100 % → warning, above 100 % → the session stops with an explicit
  message, none declared → shares off (rule d changes nothing).
- Manual observations escape the shares (they go first at equal level) but their time counts
  for the program they execute.

## 8. Failures, series, manual list

**Failures.** Success = SEQUENCER completed; failure = it raised; interrupted = failure. The
database is the memory: planners count successes only (a failed observation never consumes a
manual list line), and the shared checks refuse a target that failed less than
`failure_backoff_hours` (1 h) ago.

**Series.** The planner's business: it knows the series has started (from `rosace.db`: the
night's last observation is its own) and proposes the next element at level 1, without
failure backoff; `manual_list` stops a series after `max_series_failures` (3) consecutive
failures — a passing cloud gets 3 tries, each time. A mount flip between two elements is accepted. Not
re-pointing between elements and when to calibrate: handled by the program concerned.

**Manual list** (`ObservationList.txt`, re-read on every call, may be edited during a session):

```
Priority, Program, Star, Start_after, End_before, Series, comment
3, Be_stars, gam Cas, none, none, none, follow-up
```

- `Priority` 0, 2, 3 or 4 (1 reserved); ties keep the file order. `none` or empty = not given.
- `Start_after`/`End_before` (UT, `HH:MM` = tonight's astronomical night, or
  `YYYY-MM-DD HH:MM`) and `Series` (true/false). `Series` with `End_before` = bounded series,
  feasible until its end before the first element; without = runs until the next element is
  no longer feasible, a manual stop, or a level 0. A series line is done once the series is
  over with ≥ 1 success; with none it stays available like any failed line.
- The same star on several lines: a line with a window is done by a success started inside
  its window; the k-th line without a window of a (program, star) pair is done once that pair
  has k other successes tonight, whichever planner proposed them.
- The same star several times in another program: the planner decides from the history.
- File problems never stop the session: ERROR in the log (repeated at DEBUG while unchanged),
  the line or the whole file ignored until corrected.

## 9. Interruption (level 0) — later

Generic and "brutal": the SCHEDULER plugin may offer `should_interrupt()`; during SEQUENCER,
`main.py` calls it regularly and, on yes, cancels SEQUENCER without involving any sub-plugin,
then goes back to SCHEDULER. The selector says yes only for a feasible level-0 candidate. To
study: the hardware state left behind (an exposure already started runs to its end, PHD2
guiding, a lamp on, a half-written file).

## 10. Data recorded per observation (B1, implemented)

- New `observations` columns, all nullable (`CREATE TABLE` for new databases, manual `ALTER`
  on the existing one): `ended_at` and `outcome` (`success`/`failed`; `interrupted` later), set
  by `database.py` when SEQUENCER ends or fails; `proposed_by` and `estimated_duration_s`, from
  `scheduler_data`; measurements `ambient_temp_c`, `camera_temp_c`, `focus_fwhm_px`,
  `slit_y_px`, `pier_side`. No `level` column (`priority` holds it), no `exposure_total_s`.
- Measurements go through a generic `obs.measurements` dict (agreed name → value) written by
  sub-plugins: temperatures by `star_spectra_acquisition` right before the science exposures,
  `pier_side` by `slew_72ed`, `slit_y_px` by `slit_xy`, `focus_fwhm_px` by `focus`. The
  `observations` table is written by `main.py` only: after every stage, where it writes the
  YAML dossier, it copies the names declared in `database.py::MEASUREMENT_COLUMNS`, without
  knowing who produced them (same pattern as `scheduler_data` → `program_name`, `priority`).

**Meridian, to study on the AM3**: how far it tracks past the meridian
(`meridian_tolerance_deg`), what it does at that limit, which pier side it takes for a target
just past the meridian, how it reports it (`TELESCOPE_PIER_SIDE`). Until then only the current
pointing rule applies.

## 11. Development phases

- **Phase A (done)**: contract, shared checks, history helper, selector (steps 1, 2, 4, 5a, 5b,
  6), `manual_list` (new file format, windows/series refused, same choices as
  `scheduler_rosace_2026` — verified by a test), `blank_program`. Next: one real night with
  `active_scheduler: selector`.
- **Phase B1 (done)**: success-only counting and 1-hour backoff, new columns and
  `obs.measurements` (temperatures, pier side, slit, focus). Next: one simulator run.
- **Phase B2 (done)**: time shares, windows and booked observations, series in the manual
  list. Next: one simulator run, then the first program planner (Be).
- **Later**: interruption, meridian tolerance, Moon, moving objects, filling before a booked
  observation with the candidates of every planner.

"""
Manual list planner: proposes the observations an observer listed by hand in
the file named by observation_list_path, at the root of the configuration's
subplugin_params (an installation setting, shared with scheduler_rosace_2026,
not a program parameter). manual_list is the minimal program, always present
(programs/manual_list.yaml).

File format (CSV with a header; `none` or an empty field = not given):

    Priority, Program, Star, Start_after, End_before, Series, comment
    3, Be_stars, gam Cas, none, none, none, weekly follow-up
    2, Be_stars, V923 Aql, 21:30, 23:00, true, bounded series

- Priority: selector level 0, 2, 3 or 4 (1 is reserved for a series in
  progress); lines of the same priority keep the file order.
- Program: the program whose YAML will run the observation; the star must be
  linked to it in targets.db.
- Star: canonical name or alias in targets.db.
- Start_after / End_before (UT): `HH:MM` (that time during the current
  astronomical night, local noon to local noon) or `YYYY-MM-DD HH:MM`.
  Start_after is the start of the science exposures — the observation is
  launched the fixed overhead earlier (checks.launch_time()); End_before is
  the end of the whole observation. The three last columns may be absent.
- Series: `true` = the star is observed again and again, nothing else in
  between, until End_before (or, without it, until it is no longer feasible).

Each line is a separate request, so the same star may appear on several
lines. A line with a time window is done once the pair (program, star) has a
SUCCESSFUL observation started inside that window (from its launch time to
End_before). The lines without a window share the other successes: the k-th
such line naming a (program, star) pair counts as done once that pair has at
least k of them during the current astronomical night (rosace.db; aliases of
the same star count as that star), whichever planner proposed them. A failed observation does not consume its
line: the line is proposed again once the failure backoff (shared checks,
failure_backoff_hours) has elapsed.

Series: the first element is proposed at the line's priority. While the
night's last observation is that (program, star) and the window is open,
the next element is proposed at level 1 (series in progress), without failure
backoff — until max_series_failures (params, 3) consecutive failures: a
passing cloud gets that many tries, each time. A series line counts as one
request: done once the series is over with at least one success; with none,
it stays available like any failed line. Do not mix a series line with other
lines naming the same star.

Booked lines: a line whose launch time is still ahead (within the selector's
lookahead_minutes) is "booked". While it waits, this planner only proposes a
line that ends before its launch (or a more urgent one); if no line fits, it
proposes the booked line itself and the selector waits for it. One candidate
in every case.

The file may be edited during a session: it is re-read on every call and
never modified. A problem (missing file, missing column, invalid line, bad
data in targets.db) never stops the session: it is logged as an ERROR, the
affected line (or the whole file) is ignored, and the planner waits for the
correction. To keep the log readable, an error already reported on the
previous call is only repeated at DEBUG level, until it changes or disappears.
"""

import csv
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from ....config import SUBPLUGIN_PARAMS
from ....database import db_count_trailing_failures, db_get_recent_observations, db_get_successes_for_target
from ....models import ExposureConfig
from ....targets import program_get_by_name, program_get_stars, star_get_by_name
from ....time_helpers import astronomical_day_start_utc
from .. import PRIORITY_SERIES, Candidate, SchedulingContext, TimeWindow
from ..checks import (
    check_feasible, check_not_recently_failed, check_window, estimate_duration_s, launch_time,
)

log = logging.getLogger("spectro.plugin.planner.manual_list")

OBSERVATION_LIST_PATH = Path(
    SUBPLUGIN_PARAMS.get("observation_list_path", "~/.rosace/targets/ObservationList.txt")
).expanduser()
DEFAULT_MAX_SERIES_FAILURES = 3
_VALID_PRIORITIES = (0, 2, 3, 4)

# Errors reported at ERROR level on the previous call — only used to avoid
# repeating the same message every minute; no scheduling decision depends on it.
_previously_reported: set[str] = set()


def _is_given(value) -> bool:
    """False for a missing, empty or `none` field."""
    return value is not None and value.strip().lower() not in ("", "none")


def _report_errors(errors: list[str]) -> None:
    """Logs each error at ERROR level the first time, at DEBUG while it persists."""
    global _previously_reported
    for message in errors:
        level = logging.DEBUG if message in _previously_reported else logging.ERROR
        log.log(level, f"[SCHEDULER] {message}")
    _previously_reported = set(errors)


def _parse_ut(value: str, now: datetime) -> datetime:
    """`HH:MM` (that time during the astronomical night containing `now`) or
    `YYYY-MM-DD HH:MM`, both UT. Raises ValueError otherwise."""
    value = value.strip()
    try:
        clock = datetime.strptime(value, "%H:%M")
    except ValueError:
        pass
    else:
        night_start = datetime.fromisoformat(astronomical_day_start_utc(now))
        moment = night_start.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
        return moment + timedelta(days=1) if moment < night_start else moment
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise ValueError(f"'{value}' is not HH:MM nor YYYY-MM-DD HH:MM")


def _parse_series(value) -> bool:
    if not _is_given(value):
        return False
    flag = value.strip().lower()
    if flag not in ("true", "false"):
        raise ValueError(f"Series '{value.strip()}' is not true, false or none")
    return flag == "true"


def _read_observation_list(path: Path, now: datetime, errors: list[str]) -> list[dict]:
    """Returns the valid lines of the observation list, in file order, and
    appends a message to `errors` for every problem found."""
    try:
        f = open(path, encoding="utf-8", newline="")
    except OSError as e:
        errors.append(f"Observation list unreadable: {path} — {e} "
                      f"(see subplugin_params.observation_list_path in the configuration)")
        return []

    with f:
        reader = csv.DictReader(f, skipinitialspace=True)
        missing = [c for c in ("Priority", "Program", "Star") if c not in (reader.fieldnames or [])]
        if missing:
            errors.append(f"{path}: missing column(s) {missing} — expected: Priority, Program, "
                          f"Star, Start_after, End_before, Series, comment")
            return []

        rows = []
        for row in reader:
            line = reader.line_num
            try:
                priority = int(row["Priority"].strip())
                program = row["Program"].strip()
                star = row["Star"].strip()
            except (ValueError, AttributeError) as e:
                errors.append(f"{path}, line {line}: invalid line, ignored — {e}")
                continue

            if priority == PRIORITY_SERIES:
                errors.append(f"{path}, line {line}: priority 1 is reserved for a series "
                              f"in progress, line ignored")
                continue
            if priority not in _VALID_PRIORITIES:
                errors.append(f"{path}, line {line}: priority {priority} not in "
                              f"{_VALID_PRIORITIES}, line ignored")
                continue
            try:
                start_after = _parse_ut(row["Start_after"], now) if _is_given(row.get("Start_after")) else None
                end_before = _parse_ut(row["End_before"], now) if _is_given(row.get("End_before")) else None
                series = _parse_series(row.get("Series"))
            except ValueError as e:
                errors.append(f"{path}, line {line}: {e}, line ignored")
                continue
            if start_after and end_before and start_after >= end_before:
                errors.append(f"{path}, line {line}: Start_after is not before End_before, line ignored")
                continue
            window = TimeWindow(start_after=start_after, end_before=end_before) \
                if (start_after or end_before) else None
            rows.append({"line": line, "priority": priority, "program": program, "star": star,
                         "window": window, "series": series})
        return rows


class ManualListScheduler:
    """Proposes the first line of the observation list that is still to do
    and feasible (shared checks plus this program's own minimum altitude),
    a series element in progress, or a booked line."""

    async def propose(self, ctx: SchedulingContext) -> Candidate | None:
        path = OBSERVATION_LIST_PATH
        errors: list[str] = []
        try:
            return await self._propose(ctx, path, errors)
        finally:
            _report_errors(errors)

    async def _resolve(self, rows: list[dict], errors: list[str]) -> list[dict]:
        """Resolves each line in targets.db, in priority order (stable sort:
        ties keep the file order)."""
        resolved: list[dict] = []
        for row in sorted(rows, key=lambda r: r["priority"]):
            program = await program_get_by_name(row["program"])
            if not program:
                errors.append(f"Line {row['line']}: unknown program '{row['program']}', line ignored")
                continue
            star = await star_get_by_name(row["star"])
            if not star:
                errors.append(f"Line {row['line']}: unknown star '{row['star']}', line ignored")
                continue
            try:
                program_stars = await program_get_stars(program.id)
            except ValidationError as e:
                errors.append(
                    f"Line {row['line']}: invalid data in targets.db for program "
                    f"'{program.name}' (table program_stars) — exposure_time must be a number "
                    f"or empty (NULL), never ''. Line ignored. Detail: {e}"
                )
                continue
            matching = next((s for s in program_stars if s.id == star.id), None)
            if matching is None:
                errors.append(f"Line {row['line']}: star '{row['star']}' not linked to program "
                              f"'{program.name}', line ignored")
                continue
            resolved.append({**row, "program": program, "star_record": star,
                             "exposure": ExposureConfig(exposure_time=matching.exposure_time)})
        return resolved

    async def _propose(self, ctx, path, errors) -> Candidate | None:
        min_altitude_deg = ctx.params.get("min_altitude_deg")
        max_series_failures = ctx.params.get("max_series_failures", DEFAULT_MAX_SERIES_FAILURES)
        resolved = await self._resolve(_read_observation_list(path, ctx.now, errors), errors)

        recent = await db_get_recent_observations(limit=1)
        last_pair = (recent[0]["program_name"], recent[0]["target_name"]) if recent else None

        def skip(item, why: str) -> None:
            log.info(f"[SCHEDULER] {item['star']}: {why}, line {item['line']} ignored")

        def candidate(item, priority: int, reason: str) -> Candidate:
            return Candidate(queried_name=item["star"], program=item["program"].name,
                             priority=priority, exposure=item["exposure"],
                             window=item["window"], reason=reason)

        booked: Optional[tuple[dict, datetime]] = None   # the most urgent booked line, its launch time

        # Which lines are done. A line with a time window only counts the
        # successful observations made inside its window (from its launch time
        # to End_before): one for an ordinary line, all of them for a series.
        # The lines without a window share the other successes: the k-th such
        # line naming a (program, star) pair is done once that pair has k of
        # them tonight — a star listed at priorities 2 and 4 lets a priority-3
        # line through between its two occurrences.
        claimed: dict[tuple[str, int], int] = {}
        for item in resolved:
            if item["window"] is None:
                continue
            key = (item["program"].name, item["star_record"].id)
            item["in_window"], _ = await db_get_successes_for_target(
                item["program"].name, item["star_record"].name, ctx.now,
                start=launch_time(item["window"]), end=item["window"].end_before)
            claimed[key] = claimed.get(key, 0) + \
                (item["in_window"] if item["series"] else min(item["in_window"], 1))

        rank: dict[tuple[str, int], int] = {}
        for item in resolved:
            star = item["star_record"]
            program_name = item["program"].name
            key = (program_name, star.id)
            window = item["window"]
            duration_s = estimate_duration_s(star, item["exposure"])
            if duration_s is None:
                skip(item, "duration unknown (no exposure time and no magnitude)")
                continue

            # A series already started (its window open) goes on at level 1,
            # without failure backoff.
            launch = launch_time(window)
            started = launch is None or launch <= ctx.now
            if item["series"] and started and last_pair == (program_name, star.name):
                failures = await db_count_trailing_failures(program_name, star.name, ctx.now)
                status, why = check_window(window, ctx.now, duration_s)
                why = (why if status == "refused" else None) or \
                    check_feasible(star, item["exposure"], ctx.now, min_altitude_deg)
                if failures >= max_series_failures:
                    log.info(f"[SCHEDULER] {item['star']}: series stopped after {failures} "
                             f"consecutive failures (line {item['line']})")
                elif why:
                    log.info(f"[SCHEDULER] {item['star']}: series over — {why} (line {item['line']})")
                else:
                    return candidate(item, PRIORITY_SERIES,
                                     f"ObservationList line {item['line']}: series in progress")

            if window is not None:
                if item["in_window"] >= 1:
                    continue
            else:
                rank[key] = rank.get(key, 0) + 1
                successes, _ = await db_get_successes_for_target(program_name, star.name, ctx.now)
                if successes - claimed.get(key, 0) >= rank[key]:
                    continue

            reason = await check_not_recently_failed(program_name, star.name, ctx.now)
            if reason:
                skip(item, reason)
                continue

            status, reason = check_window(window, ctx.now, duration_s)
            if status == "refused":
                skip(item, reason)
                continue
            start = launch_time(window) if status == "booked" else ctx.now
            # A bounded series must be feasible until its end.
            span_s = (window.end_before - start).total_seconds() \
                if item["series"] and window and window.end_before else duration_s
            reason = check_feasible(star, item["exposure"], ctx.now, min_altitude_deg,
                                    duration_s=span_s, start=start)
            if reason:
                skip(item, reason)
                continue

            if status == "booked":
                if booked is None:
                    booked = (item, start)
                continue
            if booked is not None and ctx.now + timedelta(seconds=duration_s) > booked[1]:
                skip(item, f"would end after the launch of booked line {booked[0]['line']} "
                           f"({booked[1]:%H:%M} UT)")
                continue
            return candidate(item, item["priority"],
                             f"ObservationList line {item['line']} (priority {item['priority']})")

        if booked is not None:
            item, start = booked
            return candidate(item, item["priority"],
                             f"ObservationList line {item['line']} (priority {item['priority']}), "
                             f"booked: launch at {start:%H:%M} UT")
        return None

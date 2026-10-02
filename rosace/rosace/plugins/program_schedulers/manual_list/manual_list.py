"""
Manual list planner: proposes the observations an observer listed by hand in
ObservationList.txt. manual_list is the minimal program, always present
(programs/manual_list.yaml).

File format (CSV with a header; `none` or an empty field = not given):

    Priority, Program, Star, Start_after, End_before, Series, comment
    3, Be_stars, gam Cas, none, none, none, weekly follow-up

- Priority: selector level 0, 2, 3 or 4 (1 is reserved for a series in
  progress); lines of the same priority keep the file order.
- Program: the program whose YAML will run the observation; the star must be
  linked to it in targets.db.
- Star: canonical name or alias in targets.db.
- Start_after, End_before (UT) and Series: not supported yet. A line that uses
  them is ignored. The three columns may be absent from the file.

Each line is a separate request, so the same star may appear on several
lines. On every call, the k-th line naming a (program, star) pair counts as
done once that pair has at least k SUCCESSFUL observations during the current
astronomical night (rosace.db; aliases of the same star count as that star),
whichever planner proposed them. A failed observation does not consume its
line: the line is proposed again once the failure backoff (shared checks,
failure_backoff_hours) has elapsed.

The file may be edited during a session: it is re-read on every call and
never modified. A problem (missing file, missing column, invalid line, bad
data in targets.db) never stops the session: it is logged as an ERROR, the
affected line (or the whole file) is ignored, and the planner waits for the
correction. To keep the log readable, an error already reported on the
previous call is only repeated at DEBUG level, until it changes or disappears.
"""

import csv
import logging
from pathlib import Path

from pydantic import ValidationError

from ....database import db_get_successes_for_target
from ....models import ExposureConfig
from ....targets import program_get_by_name, program_get_stars, star_get_by_name
from .. import PRIORITY_SERIES, Candidate, SchedulingContext
from ..checks import check_feasible, check_not_recently_failed

log = logging.getLogger("spectro.plugin.planner.manual_list")

DEFAULT_OBSERVATION_LIST_PATH = "~/.rosace/targets/ObservationList.txt"
_VALID_PRIORITIES = (0, 2, 3, 4)
_UNSUPPORTED_COLUMNS = ("Start_after", "End_before", "Series")

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


def _read_observation_list(path: Path, errors: list[str]) -> list[dict]:
    """Returns the valid lines of the observation list, in file order, and
    appends a message to `errors` for every problem found."""
    try:
        f = open(path, encoding="utf-8", newline="")
    except OSError as e:
        errors.append(f"Observation list unreadable: {path} — {e} "
                      f"(see observation_list_path in programs/manual_list.yaml)")
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
            unsupported = [c for c in _UNSUPPORTED_COLUMNS if _is_given(row.get(c))]
            if unsupported:
                errors.append(f"{path}, line {line}: {', '.join(unsupported)} not supported "
                              f"yet, line ignored")
                continue
            rows.append({"line": line, "priority": priority, "program": program, "star": star})
        return rows


class ManualListScheduler:
    """Proposes the first line of the observation list that is still to do
    and feasible now (shared checks plus this program's own minimum altitude)."""

    async def propose(self, ctx: SchedulingContext) -> Candidate | None:
        path = Path(ctx.params.get("observation_list_path", DEFAULT_OBSERVATION_LIST_PATH)).expanduser()
        min_altitude_deg = ctx.params.get("min_altitude_deg")
        errors: list[str] = []
        try:
            return await self._propose(ctx, path, min_altitude_deg, errors)
        finally:
            _report_errors(errors)

    async def _propose(self, ctx, path, min_altitude_deg, errors) -> Candidate | None:
        rows = _read_observation_list(path, errors)

        # Resolves each line, in priority order (stable sort: ties keep the file order).
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
                             "exposure_time": matching.exposure_time})

        # The k-th line naming a (program, star) pair is done once that pair has
        # k successful observations tonight: a star listed at priorities 2 and 4
        # lets a priority-3 line through between its two occurrences.
        rank: dict[tuple[str, int], int] = {}
        for item in resolved:
            star = item["star_record"]
            program_name = item["program"].name
            key = (program_name, star.id)
            rank[key] = rank.get(key, 0) + 1
            successes, _ = await db_get_successes_for_target(program_name, star.name, ctx.now)
            if successes >= rank[key]:
                continue

            reason = await check_not_recently_failed(program_name, star.name, ctx.now)
            if reason:
                log.info(f"[SCHEDULER] {item['star']}: {reason}, line {item['line']} ignored")
                continue

            exposure = ExposureConfig(exposure_time=item["exposure_time"])
            reason = check_feasible(star, exposure, ctx.now, min_altitude_deg)
            if reason:
                log.info(f"[SCHEDULER] {item['star']}: {reason}, line {item['line']} ignored")
                continue

            return Candidate(
                queried_name=item["star"],
                program=item["program"].name,
                priority=item["priority"],
                exposure=exposure,
                reason=f"ObservationList line {item['line']} (priority {item['priority']})",
            )
        return None

"""
Be stars planner: observes every Be star of the Be_stars program, brighter
than a limit magnitude, as regularly as possible.

WHAT IT PROPOSES
----------------
At every call, at most one star of the program (targets.db) with magnitude_v
<= limit_magnitude, never one already observed successfully by Rosace tonight:

- last observation = the most recent of the BeSS database (Last_Obs column of
  the BeSS robot file, every observer included) and of Rosace (last successful
  observation of the program in rosace.db, any night). BeSS validation can
  take weeks, so both count the same.
- period = period_days (params), or the BeSS period (Obs_Period column) when
  BeSS asks for a shorter one.
- level: overdue (never observed, or last observation at least one period
  ago) and BeSS asks for a shorter period than period_days -> 2 (high);
  overdue otherwise -> 3 (ordinary); not overdue -> 4 (filler, leaving the
  telescope to the other programs).
- within a level: the most overdue first, by steps of half a period (lateness
  = age in whole days / period; never observed = infinitely late); then the
  highest in the sky at mid-observation; then the brightest (shortest
  exposure, more observations per night); then the name.
- the first star of that order that passes the shared checks is proposed:
  not failed recently (failure_backoff_hours), estimated duration not above
  max_duration_minutes (a guard, logged as a WARNING once per star and night),
  feasible over its whole duration (checks.check_feasible: altitude, local
  horizon, meridian rules, plus this program's min_altitude_deg if set).

THE BESS ROBOT FILE
-------------------
Published every night by Arasbeam (robot_url): all BeSS Be stars with their
last observation date and requested period. This planner keeps its own copy
(robot_path) and downloads it again when it is older than robot_max_age_hours
-- in practice at the first call of each night. A download is bounded
(DOWNLOAD_TIMEOUT_SECONDS, well under the planner timeout), the copy is only
replaced by a complete, valid file, and a failure never stops anything: a
WARNING, the previous copy is kept, a new attempt at most every
RETRY_MINUTES. Without any copy at all, an ERROR (once per night) and
Rosace's own history only. Robot lines are matched to targets.db stars by
the "HD n" alias or the BeSS name (canonical name or alias).

Params (programs/Be_stars.yaml, scheduler.params): limit_magnitude (7),
period_days (30), max_duration_minutes (90), min_altitude_deg (none: the
installation floor only), robot_url, robot_path (~/.rosace/be/ObsC2A_15.txt),
robot_max_age_hours (12).
"""

import logging
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple, Optional

import httpx

from ....database import db_get_last_success_per_target
from ....models import ExposureConfig
from ....targets import StarInProgram, program_get_by_name, program_get_stars, star_aliases_map
from ....time_helpers import astronomical_day_start_utc
from .. import PRIORITY_FILLER, PRIORITY_HIGH, PRIORITY_ORDINARY, Candidate, SchedulingContext
from .. import checks

log = logging.getLogger("spectro.plugin.planner.be_scheduler")

DEFAULT_LIMIT_MAGNITUDE = 7.0
DEFAULT_PERIOD_DAYS = 30.0
DEFAULT_MAX_DURATION_MINUTES = 90.0
DEFAULT_ROBOT_URL = "http://arasbeam.free.fr/robot/ObsC2A_15.txt"
DEFAULT_ROBOT_PATH = "~/.rosace/be/ObsC2A_15.txt"
DEFAULT_ROBOT_MAX_AGE_HOURS = 12.0
DOWNLOAD_TIMEOUT_SECONDS = 5.0
RETRY_MINUTES = 30.0
LATENESS_STEP = 0.5   # stars whose lateness falls in the same half period are equivalent

# One robot line: name (fixed 15-character field), HD (0 = none), RA, DEC,
# magnitude, last observation (date and time, 0000-00-00 = never), BeSS
# priority, requested period (days).
_ROBOT_LINE = re.compile(
    r"^(.{15})(\S*)\s+([+-]\d\d \d\d [\d.]+)\s+([+-]\d\d \d\d [\d.]+)\s+(\S+)\s+"
    r"(\S+ \S+|\S+)\s+(\S+)\s+(\S+)\s*$"
)
_ROBOT_HEADER = ("Name", "HD", "RA", "DEC", "Magn", "Last_Obs", "Prior.", "Obs_Period")

# Only used to limit network attempts and repeated log messages; no
# scheduling decision depends on them.
_last_download_attempt: Optional[float] = None
_reported: set[str] = set()


class BessEntry(NamedTuple):
    last_obs: Optional[datetime]
    period_days: Optional[float]


def _report_once(key: str, level: int, message: str) -> None:
    """Logs a message once per key (the key includes the night)."""
    if key not in _reported:
        _reported.add(key)
        log.log(level, f"[SCHEDULER] {message}")


def _is_robot_file(text: str) -> bool:
    lines = text.splitlines()
    return len(lines) > 2 and tuple(lines[1].split()) == _ROBOT_HEADER


def parse_robot_file(text: str) -> dict[str, BessEntry]:
    """BeSS entries keyed by lower-case name and by "hd <n>"; unreadable lines
    are skipped (DEBUG)."""
    entries: dict[str, BessEntry] = {}
    for line in text.splitlines()[2:]:
        match = _ROBOT_LINE.match(line)
        if not match:
            if line.strip():
                log.debug(f"[SCHEDULER] BeSS robot file: unreadable line skipped: {line!r}")
            continue
        name, hd, _, _, _, last_obs, _, period = (field.strip() for field in match.groups())
        try:
            last = (None if last_obs.startswith("0000")
                    else datetime.strptime(last_obs, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc))
            period_days = float(period)
        except ValueError:
            log.debug(f"[SCHEDULER] BeSS robot file: unreadable line skipped: {line!r}")
            continue
        entry = BessEntry(last, period_days if period_days > 0 else None)
        entries[name.lower()] = entry
        if hd not in ("", "0"):
            entries[f"hd {hd}"] = entry
    return entries


async def _download(url: str) -> str:
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def _refresh_robot_file(path: Path, url: str, max_age_hours: float) -> None:
    """Downloads the robot file again when missing or older than max_age_hours;
    the copy is replaced only by a complete, valid file."""
    global _last_download_attempt
    if path.exists() and time.time() - path.stat().st_mtime < max_age_hours * 3600:
        return
    if _last_download_attempt is not None and time.monotonic() - _last_download_attempt < RETRY_MINUTES * 60:
        return
    _last_download_attempt = time.monotonic()
    try:
        text = await _download(url)
        if not _is_robot_file(text):
            raise ValueError("unexpected content (not a BeSS robot file)")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        log.info(f"[SCHEDULER] BeSS robot file downloaded: {url} -> {path}")
    except Exception as e:
        kept = "previous copy kept" if path.exists() else "no copy available"
        log.warning(f"[SCHEDULER] BeSS robot file download failed ({url}): {e} — {kept}")


def _read_robot_file(path: Path, night: str) -> dict[str, BessEntry]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        _report_once(f"{night}|no-robot", logging.ERROR,
                     f"BeSS robot file unavailable ({path}: {e}) — Rosace's own history only")
        return {}
    if not _is_robot_file(text):
        _report_once(f"{night}|bad-robot", logging.ERROR,
                     f"BeSS robot file unreadable ({path}) — Rosace's own history only")
        return {}
    return parse_robot_file(text)


class _Ranked(NamedTuple):
    sort_key: tuple
    star: StarInProgram
    level: int
    reason: str


def _date(moment: Optional[datetime]) -> str:
    return f"{moment:%Y-%m-%d}" if moment else "never"


class BeSchedulerScheduler:

    async def propose(self, ctx: SchedulingContext) -> Candidate | None:
        program = await program_get_by_name(ctx.program) if ctx.program else None
        if program is None:
            log.warning(f"[SCHEDULER] Program '{ctx.program}' not found in targets.db")
            return None

        params = ctx.params
        limit_magnitude = float(params.get("limit_magnitude", DEFAULT_LIMIT_MAGNITUDE))
        period_param = float(params.get("period_days", DEFAULT_PERIOD_DAYS))
        max_duration_s = 60 * float(params.get("max_duration_minutes", DEFAULT_MAX_DURATION_MINUTES))
        min_altitude_deg = params.get("min_altitude_deg")
        robot_path = Path(params.get("robot_path", DEFAULT_ROBOT_PATH)).expanduser()
        night = astronomical_day_start_utc(ctx.now)
        night_start = datetime.fromisoformat(night)

        await _refresh_robot_file(robot_path, params.get("robot_url", DEFAULT_ROBOT_URL),
                                  float(params.get("robot_max_age_hours", DEFAULT_ROBOT_MAX_AGE_HOURS)))
        bess = _read_robot_file(robot_path, night)

        stars = [s for s in await program_get_stars(program.id)
                 if s.magnitude_v is not None and s.magnitude_v <= limit_magnitude]
        aliases = await star_aliases_map([s.id for s in stars])
        rosace_last = await db_get_last_success_per_target(program.name)

        ranked = []
        for star in stars:
            rosace = rosace_last.get(star.name)
            if rosace is not None and rosace >= night_start:
                continue   # already observed successfully tonight
            entry = next((bess[n.lower()] for n in [star.name, *aliases.get(star.id, [])]
                          if n.lower() in bess), None)
            ranked_star = self._rank(star, ctx, entry, rosace, period_param)
            if ranked_star is not None:
                ranked.append(ranked_star)
        ranked.sort(key=lambda r: r.sort_key)
        log.debug(f"[SCHEDULER] Be stars to consider: {len(ranked)} "
                  f"(level 2: {sum(r.level == 2 for r in ranked)}, level 3: {sum(r.level == 3 for r in ranked)}, "
                  f"level 4: {sum(r.level == 4 for r in ranked)}), BeSS entries: {len(bess)}")

        for r in ranked:
            if await checks.check_not_recently_failed(program.name, r.star.name, ctx.now) is not None:
                continue
            exposure = ExposureConfig(exposure_time=r.star.exposure_time)
            duration_s = checks.estimate_duration_s(r.star, exposure)
            if duration_s is None:
                continue
            if duration_s > max_duration_s:
                _report_once(f"{night}|duration|{r.star.name}", logging.WARNING,
                             f"{r.star.name} skipped: estimated duration {duration_s / 60:.0f} min "
                             f"above max_duration_minutes ({max_duration_s / 60:g}) — set its "
                             f"exposure time in targets.db")
                continue
            if checks.check_feasible(r.star, exposure, ctx.now, min_altitude_deg) is not None:
                continue
            return Candidate(
                queried_name=r.star.name,
                program=program.name,
                priority=r.level,
                exposure=exposure,
                reason=r.reason,
            )
        return None

    @staticmethod
    def _rank(star: StarInProgram, ctx: SchedulingContext, entry: Optional[BessEntry],
              rosace: Optional[datetime], period_param: float) -> Optional[_Ranked]:
        bess_last = entry.last_obs if entry else None
        bess_period = entry.period_days if entry else None
        known = [d for d in (bess_last, rosace) if d is not None]
        last = max(known) if known else None
        period = min(period_param, bess_period) if bess_period else period_param

        if last is None:
            lateness = math.inf
            overdue = True
            age_text = "never observed"
        else:
            age_days = (ctx.now - last).total_seconds() / 86400
            lateness = math.floor(age_days) / period
            overdue = age_days >= period
            age_text = f"last observed {age_days:.0f} d ago"
        if overdue and bess_period is not None and bess_period < period_param:
            level = PRIORITY_HIGH
        elif overdue:
            level = PRIORITY_ORDINARY
        else:
            level = PRIORITY_FILLER

        duration_s = checks.estimate_duration_s(star, ExposureConfig(exposure_time=star.exposure_time))
        if duration_s is None:
            return None
        altitude = checks.altitude_deg(star.ra, star.dec, ctx.site.latitude_deg, ctx.site.longitude_deg,
                                       ctx.now + timedelta(seconds=duration_s / 2))
        step = math.inf if math.isinf(lateness) else math.floor(lateness / LATENESS_STEP)

        reason = (f"{age_text} (BeSS {_date(bess_last)}, Rosace {_date(rosace)}), period {period:g} d"
                  + (f" (BeSS {bess_period:g} d)" if bess_period else ""))
        if last is not None:
            reason += f" — overdue x{lateness:.1f}" if overdue else " — not due yet"
        return _Ranked((level, -step, -altitude, star.magnitude_v, star.name), star, level, reason)

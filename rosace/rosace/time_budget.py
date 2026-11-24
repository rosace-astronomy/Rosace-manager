# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The night's time budget: where the time went while Rosace was running.

Called at the end of CLOSE_SESSION (build_night_budget()). Reads the night's
time line (time_segments, recorded by time_ledger.py) and the time
measurements of its observations, then:
  - splits the two mixed categories with those measurements: "science" into
    science_exposure (science_exposure_s) and science_readout (the rest),
    "pointing" into slew (slew_s), guiding_settle (guiding_settle_s) and
    fine_pointing (the rest);
  - turns every interval of a failed acquisition into failed_attempt;
  - counts any gap in the time line (between two sessions of the night, or
    after a crash) as between_sessions, so the sum is always the time from
    the night's first start to its last close -- nothing leaks;
  - writes one row per category into night_time_budget (rosace.db), and
    <date>_time_budget.csv and .pdf into the night's directory.

The whole night is recomputed at every close, so a night with several
sessions ends with one correct budget. Measurements are durations, not
times: the budget is exact, but in the PDF's timeline the parts of a step
are drawn in a fixed order (slew, fine pointing, settling; exposure then
readout), an approximation.

Generic: knows only category names, no sub-plugin. Never blocking (the
caller catches and logs any error).
"""

import csv
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure

from .database import db_get_night_time_data, db_replace_night_time_budget
from .time_ledger import ledger

log = logging.getLogger("spectro.time_budget")

# Budget categories, in display order: (name, label, colour).
CATEGORIES = [
    ("science_exposure", "Science exposure (shutter open)", "#2e7d32"),
    ("science_readout", "Science readout between frames", "#81c784"),
    ("slew", "Slew to the target", "#0d47a1"),
    ("fine_pointing", "Fine pointing (guide images, plate solves, slit)", "#1e88e5"),
    ("guiding_settle", "PHD2 settling", "#90caf9"),
    ("homing", "Mount homing", "#4dd0e1"),
    ("target_measurements", "Measurements on target (focus, wide field, end guide image)", "#6a1b9a"),
    ("calibrations", "Calibrations (dark, bias, lamp, flat)", "#ef6c00"),
    ("scheduler", "Scheduler (target choice)", "#00838f"),
    ("waiting_for_target", "Waiting for a target", "#bdbdbd"),
    ("orchestrator", "Orchestrator (between stages)", "#795548"),
    ("failed_attempt", "Failed attempts", "#c62828"),
    ("init", "Session start (connection, cooling)", "#455a64"),
    ("waiting_post_obs", "Waiting for the last reductions", "#90a4ae"),
    ("close", "Session close (park, disconnect)", "#263238"),
    ("other", "Other steps", "#f06292"),
    ("between_sessions", "Rosace stopped or interrupted", "#eeeeee"),
]
LABELS = {name: label for name, label, _ in CATEGORIES}
COLORS = {name: color for name, _, color in CATEGORIES}

# A gap shorter than this between two recorded intervals is rounding, not a stop.
GAP_TOLERANCE_S = 1.0
# Categories under this share of the night are grouped as "Other" in the pie chart...
PIE_MIN_FRACTION = 0.01
# ...except the three parts of pointing, always shown on their own, however small.
PIE_ALWAYS_SHOWN = {"slew", "fine_pointing", "guiding_settle"}


@dataclass
class Segment:
    start: datetime
    end: datetime
    category: str
    step: str = ""
    observation_id: str = ""

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass
class NightBudget:
    seconds: dict[str, float]          # category -> seconds, budget categories only
    segments: list[Segment]            # the final time line, after splitting
    successful_observations: int
    failed_observations: int
    capped: list[str]                  # observations whose measurements exceeded their step time

    @property
    def total(self) -> float:
        return sum(self.seconds.values())


def _split_sequentially(segments: list[Segment], parts: list[tuple[str, float]]) -> list[Segment]:
    """Cuts consecutive segments into the given (category, seconds) parts, in order."""
    out: list[Segment] = []
    queue = [(category, seconds) for category, seconds in parts if seconds > 0]
    for seg in segments:
        cursor = seg.start
        while cursor < seg.end and queue:
            category, seconds = queue[0]
            available = (seg.end - cursor).total_seconds()
            take = min(seconds, available)
            end = cursor + timedelta(seconds=take)
            out.append(replace(seg, start=cursor, end=end, category=category))
            cursor = end
            if seconds - take <= 1e-6:
                queue.pop(0)
            else:
                queue[0] = (category, seconds - take)
        if cursor < seg.end:  # rounding leftover
            out.append(replace(seg, start=cursor, category=out[-1].category if out else seg.category))
    return out


def _split_mixed(segments: list[Segment], total: float, measured: list[tuple[str, float]],
                 rest: str, rest_position: int) -> tuple[list[Segment], bool]:
    """Splits one observation's segments of a mixed category: the measured
    parts, capped to the time available, plus `rest` for what remains,
    inserted at `rest_position` in the drawing order. Returns the segments
    and whether the measurements had to be capped."""
    measured_total = sum(seconds for _, seconds in measured)
    capped = measured_total > total + GAP_TOLERANCE_S
    if measured_total > total and measured_total > 0:
        measured = [(category, seconds * total / measured_total) for category, seconds in measured]
        measured_total = total
    parts = list(measured)
    parts.insert(rest_position, (rest, total - measured_total))
    return _split_sequentially(segments, parts), capped


def compute_budget(segments: list[dict], measurements: dict[str, dict]) -> NightBudget:
    """Turns the night's recorded intervals (time_segments rows, in time
    order) into the budget. Pure function, no I/O."""
    raw = [
        Segment(datetime.fromisoformat(r["started_at"]), datetime.fromisoformat(r["ended_at"]),
                "failed_attempt" if r.get("outcome") == "failed" else r["category"],
                r.get("step") or "", r.get("observation_id") or "")
        for r in segments
    ]
    failed = {r.get("observation_id") for r in segments if r.get("outcome") == "failed"} - {None, ""}

    # Gaps (between sessions, or a crash) become explicit intervals.
    timeline: list[Segment] = []
    for seg in raw:
        if timeline and (seg.start - timeline[-1].end).total_seconds() > GAP_TOLERANCE_S:
            timeline.append(Segment(timeline[-1].end, seg.start, "between_sessions"))
        timeline.append(seg)

    # Split the mixed categories, observation by observation.
    by_obs: dict[tuple[str, str], list[Segment]] = {}
    for seg in timeline:
        if seg.category in ("science", "pointing"):
            by_obs.setdefault((seg.observation_id, seg.category), []).append(seg)
    replacement: dict[int, list[Segment]] = {}
    capped: list[str] = []
    for (obs_id, category), segs in by_obs.items():
        total = sum(s.seconds for s in segs)
        m = measurements.get(obs_id, {})
        if category == "science":
            parts, was_capped = _split_mixed(
                segs, total, [("science_exposure", m.get("science_exposure_s") or 0.0)],
                "science_readout", 1)
        else:
            parts, was_capped = _split_mixed(
                segs, total, [("slew", m.get("slew_s") or 0.0),
                              ("guiding_settle", m.get("guiding_settle_s") or 0.0)],
                "fine_pointing", 1)
        if was_capped:
            capped.append(obs_id)
        replacement[id(segs[0])] = parts
        for s in segs[1:]:
            replacement[id(s)] = []
    final: list[Segment] = []
    for seg in timeline:
        final.extend(replacement.get(id(seg), [seg]))

    seconds: dict[str, float] = {}
    for seg in final:
        category = seg.category if seg.category in LABELS else "other"
        seconds[category] = seconds.get(category, 0.0) + seg.seconds

    observed = {s.observation_id for s in final if s.observation_id}
    return NightBudget(seconds, final, len(observed - failed), len(failed), sorted(set(capped)))


def _fmt_hm(seconds: float) -> str:
    """1h05 from an hour up; 12 min 30 s, or 40 s, below (a short session)."""
    if seconds >= 3600:
        minutes = int(round(seconds / 60))
        return f"{minutes // 60}h{minutes % 60:02d}"
    whole = int(round(seconds))
    return f"{whole // 60} min {whole % 60:02d} s" if whole >= 60 else f"{whole} s"


# Timeline tick spacing (seconds): the first one giving at most 8 ticks.
_TICK_STEPS_S = (60, 300, 600, 900, 1800, 3600, 7200, 10800)


def write_csv(path: Path, night: str, budget: NightBudget) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["night", "category", "label", "seconds", "percent"])
        for name, label, _ in CATEGORIES:
            value = budget.seconds.get(name)
            if value:
                writer.writerow([night, name, label, round(value, 1), round(100 * value / budget.total, 2)])


def write_pdf(path: Path, title: str, budget: NightBudget) -> None:
    """One A4 portrait page: key numbers, pie chart, timeline. In the pie
    chart, the categories under 1 % are grouped as "Other", except the three
    parts of pointing (slew, fine pointing, PHD2 settling)."""
    total = budget.total
    main, other = [], 0.0
    for name, _, _ in CATEGORIES:
        value = budget.seconds.get(name, 0.0)
        if not value:
            continue
        if value / total >= PIE_MIN_FRACTION or name in PIE_ALWAYS_SHOWN:
            main.append(name)
        else:
            other += value
    sizes = [budget.seconds[n] for n in main] + ([other] if other else [])
    labels = [LABELS[n] for n in main] + (["Other (each under 1 %)"] if other else [])
    colors = [COLORS[n] for n in main] + (["#e0e0e0"] if other else [])

    start, end = budget.segments[0].start, budget.segments[-1].end
    exposure = budget.seconds.get("science_exposure", 0.0)
    fig = Figure(figsize=(8.27, 11.69))
    fig.text(0.08, 0.94, f"Time budget — night {title}", fontsize=16, weight="bold")
    lines = [
        ("Rosace active", f"{start:%Y-%m-%d %H:%M} → {end:%H:%M} UTC  ({_fmt_hm(total)})"),
        ("Shutter open", f"{_fmt_hm(exposure)}  —  {100 * exposure / total:.1f} % of the active time"),
        ("Successful observations", f"{budget.successful_observations}"),
        ("Failed attempts", f"{budget.failed_observations}  "
                            f"({_fmt_hm(budget.seconds.get('failed_attempt', 0.0))})"),
    ]
    for i, (key, value) in enumerate(lines):
        y = 0.89 - i * 0.03
        fig.text(0.08, y, key, fontsize=11, color="#555")
        fig.text(0.38, y, value, fontsize=11, weight="bold" if i == 1 else "normal")

    ax = fig.add_axes([0.15, 0.38, 0.7, 0.38])
    wedges, _ = ax.pie(sizes, colors=colors, startangle=90, counterclock=False,
                       wedgeprops={"linewidth": 1, "edgecolor": "white"})
    ax.set_aspect("equal")
    ax.legend(wedges, [f"{label}  —  {_fmt_hm(v)}  ({100 * v / total:.1f} %)"
                       for label, v in zip(labels, sizes)],
              loc="upper center", bbox_to_anchor=(0.5, -0.04), frameon=False, fontsize=10)

    ax = fig.add_axes([0.08, 0.08, 0.84, 0.05])
    for seg in budget.segments:
        ax.barh(0, seg.seconds / 3600, left=(seg.start - start).total_seconds() / 3600,
                color=COLORS.get(seg.category, COLORS["other"]), height=1, linewidth=0)
    ax.set_yticks([])
    ax.set_xlim(0, total / 3600)
    step = next((s for s in _TICK_STEPS_S if total / s <= 8), _TICK_STEPS_S[-1])
    ticks = [k * step for k in range(int(total // step) + 1)]
    ax.set_xticks([t / 3600 for t in ticks])
    ax.set_xticklabels([(start + timedelta(seconds=t)).strftime("%H:%M") for t in ticks])
    ax.set_title("Timeline (UTC)", loc="left", fontsize=11)
    with PdfPages(path) as pdf:
        pdf.savefig(fig)


async def build_night_budget(session_dir: str | Path) -> NightBudget | None:
    """Ends the current session's time line, then computes, records and
    writes the budget of the night `session_dir` belongs to. Returns None
    when the night has no recorded time line."""
    await ledger.stop()
    session_dir = Path(session_dir)
    _, segments, measurements = await db_get_night_time_data(str(session_dir))
    if not segments:
        log.info("[TIME_BUDGET] No time line recorded for this night, no budget")
        return None
    budget = compute_budget(segments, measurements)
    if budget.capped:
        log.warning(f"[TIME_BUDGET] Time measurements longer than their step, capped, for "
                    f"{len(budget.capped)} observation(s): {', '.join(budget.capped)}")
    night = session_dir.name[:10]
    await db_replace_night_time_budget(night, budget.seconds)
    write_csv(session_dir / f"{night}_time_budget.csv", night, budget)
    write_pdf(session_dir / f"{night}_time_budget.pdf", session_dir.name, budget)
    exposure = budget.seconds.get("science_exposure", 0.0)
    log.info(f"[TIME_BUDGET] Night {night}: {_fmt_hm(budget.total)} active, shutter open "
             f"{_fmt_hm(exposure)} ({100 * exposure / budget.total:.1f} %), "
             f"{budget.successful_observations} successful observation(s), "
             f"{budget.failed_observations} failed attempt(s) — {night}_time_budget.pdf")
    return budget

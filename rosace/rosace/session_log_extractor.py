# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Extracts one session's own log lines out of the global rotating log
(~/.rosace/log/rosace.log, see log_config.py) into a plain session.log file
inside that session's own directory -- called once, from CloseSessionPlugin,
right before the session closes.

Filters by timestamp window (SessionContext.started_at -> now), not by
tagging every log call site with a session_id: simpler, and correct since
only one session ever runs at a time (state.is_running is a singleton, no
concurrent sessions to disambiguate) -- see CLAUDE.md's state-machine
section.

Must read across the currently-active rosace.log plus however many rotated
backups (rosace.log.1 .. rosace.log.9 -- RotatingFileHandler, 1 MB x 9
backups, conf/logging.cfg) the session's own time window actually spans: a
chatty session (heavy DEBUG-level INDI/PHD2 traffic) can roll the active
file over more than once before the session closes.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import LOG_DIR
from .models import SessionContext
from .time_helpers import astronomical_day_local_date

_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - ")
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_HEADER_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# RotatingFileHandler backup suffixes, oldest first (see conf/logging.cfg:
# maxBytes=1_000_000, backupCount=9) -- rosace.log.9 is the oldest rotated
# chunk, rosace.log.1 the most recently rotated one, and the bare rosace.log
# is always the current, newest file.
_BACKUP_SUFFIXES = range(9, 0, -1)

# The earliest surviving log line can legitimately land a second or two after
# started_at (second-precision timestamps, plus whatever scheduling delay
# separates SessionContext's own construction from the first log call) --
# without this tolerance, that normal jitter would be misreported as rotated-
# away history. A real rotation-driven gap is orders of magnitude bigger than
# this (it takes a lot of 1 MB-chunk log volume to lose whole minutes/hours).
_TRUNCATION_TOLERANCE = timedelta(seconds=5)

_RULE = "#" + "═" * 78


def _build_header(
    ctx: SessionContext, extracted_at: datetime, source_log: Path, earliest_seen: datetime | None,
) -> str:
    night_of = astronomical_day_local_date(ctx.started_at)
    lines = [
        _RULE,
        "# ROSACE session log",
        f"# Night of:        {night_of} ({ctx.site_name})",
        f"# Session ID:      {ctx.session_id}",
        f"# Started (UTC):   {ctx.started_at.strftime(_HEADER_TIMESTAMP_FORMAT)}",
        f"# Extracted (UTC): {extracted_at.strftime(_HEADER_TIMESTAMP_FORMAT)}",
        f"# Extracted from:  {source_log} (+ rotated backups)",
    ]
    if earliest_seen is None:
        lines.append("# WARNING: no log lines found at all -- log files missing, empty, or rotated out.")
    elif earliest_seen - ctx.started_at > _TRUNCATION_TOLERANCE:
        lines.append(
            "# WARNING: earlier session history is missing (rotated out by logrotate) -- "
            f"this extract starts at {earliest_seen.strftime(_HEADER_TIMESTAMP_FORMAT)}, "
            f"{earliest_seen - ctx.started_at} after the session actually started."
        )
    lines += [_RULE, ""]
    return "\n".join(lines)


def read_log_lines(since: datetime) -> tuple[list[str], datetime | None]:
    """Every line of rosace.log and its rotated backups (oldest first)
    timestamped at or after since, continuation lines (no leading timestamp)
    kept with the record they follow; and the earliest timestamp seen in the
    files, or None. A missing or unreadable file contributes nothing."""
    log_files = [LOG_DIR / f"rosace.log.{n}" for n in _BACKUP_SUFFIXES] + [LOG_DIR / "rosace.log"]
    lines: list[str] = []
    earliest_seen: datetime | None = None
    for log_file in log_files:
        try:
            content = log_file.read_text(errors="replace")
        except OSError:
            continue
        keep = False
        for raw_line in content.splitlines(keepends=True):
            match = _TIMESTAMP_RE.match(raw_line)
            if match:
                timestamp = datetime.strptime(match.group(1), _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
                if earliest_seen is None:
                    earliest_seen = timestamp
                keep = timestamp >= since
            if keep:
                lines.append(raw_line)
    return lines, earliest_seen


def extract_session_log(ctx: SessionContext) -> Path:
    """Reads every existing rosace.log* file (oldest rotated backup first,
    current file last), and writes a header (night/session id/start/
    extraction time/source path, plus a warning if the session's own early
    history has been rotated out) followed by the lines timestamped at or
    after ctx.started_at into
    ctx.session_dir/<started_at>_<session_id>_session.log (same
    YYYYMMDDTHHMMSS timestamp convention as an observation dossier's own
    filename, see Observation.suggested_filename) -- the session_id suffix,
    not just the timestamp, is what actually disambiguates: several
    sessions can start within the same astronomical night (e.g. weather
    permitting a restart, or several short debug runs in a row), in
    chronological order, followed by a closing "End of file" footer (same
    rule style as the header) -- lets a reader tell at a glance that the
    file wasn't cut short. Returns the path written.

    A line with no parseable leading timestamp is treated as a continuation
    of the previous line (e.g. an embedded newline in a logged exception
    message) -- kept or dropped along with whichever record precedes it,
    never evaluated on its own. Best-effort throughout: a missing or
    unreadable log file contributes nothing rather than raising -- the
    extracted file is diagnostic convenience, not something the app depends
    on to close a session."""
    current_log = LOG_DIR / "rosace.log"
    lines, earliest_seen = read_log_lines(ctx.started_at)

    header = _build_header(ctx, datetime.now(timezone.utc), current_log, earliest_seen)
    footer = "\n".join(["", _RULE, "# End of file", _RULE, ""])
    filename = f"{ctx.started_at.strftime('%Y%m%dT%H%M%S')}_{ctx.session_id}_session.log"
    output_path = Path(ctx.session_dir) / filename
    output_path.write_text(header + "".join(lines) + footer)
    return output_path

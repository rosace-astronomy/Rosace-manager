# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Turns a time of day ("21:20"), local or UTC, into the instant it next occurs —
used to schedule a session start or stop from the GUI. Only a time is given,
never a date: the instant is always within the next 24 hours.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Literal

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def next_occurrence(hhmm: str, tz: Literal["local", "utc"], after: datetime) -> datetime:
    """
    First instant strictly after `after` whose time of day is `hhmm` ("HH:MM",
    24-hour), in the server's local timezone (the site's — same assumption as
    time_helpers.py) or in UTC. Returned as an aware UTC datetime. Raises
    ValueError on a malformed time.
    """
    match = _HHMM.match(hhmm or "")
    if not match:
        raise ValueError(f"Invalid time {hhmm!r}: expected HH:MM (00:00 to 23:59)")
    hour, minute = int(match.group(1)), int(match.group(2))

    if tz == "utc":
        base = after.astimezone(timezone.utc)
        candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= after:
            candidate += timedelta(days=1)
        return candidate

    # Built as a naive local time, then localized: .astimezone() on a naive datetime
    # picks the offset in force at that time, so a daylight-saving change between
    # now and then is handled.
    base = after.astimezone().replace(tzinfo=None)
    for days in (0, 1, 2):
        naive = (base + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        candidate = naive.astimezone().astimezone(timezone.utc)
        if candidate > after:
            return candidate
    raise ValueError(f"No occurrence of {hhmm} found after {after.isoformat()}")

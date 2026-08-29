# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Shared date/time helpers used across the application (database.py,
plugins/init_session.py, ...) — kept in one place so the "session boundary"
concept (local noon to local noon) is computed identically everywhere it's
needed, instead of being redefined ad hoc per caller.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _local_noon_boundary(now: datetime | None = None) -> datetime:
    """Most recent local noon at or before `now` (site's own local timezone —
    assumes the server runs in it, there is no explicit site timezone setting
    in conf/config.yaml)."""
    local_now = (now or datetime.now(timezone.utc)).astimezone()
    local_noon = local_now.replace(hour=12, minute=0, second=0, microsecond=0)
    if local_now < local_noon:
        local_noon -= timedelta(days=1)
    return local_noon


def astronomical_day_start_utc(now: datetime | None = None) -> str:
    """
    Start of the current astronomical day (local noon at or before `now`), as a UTC
    timestamp string in the same format as the created_at column, for a direct
    lexicographic comparison.
    """
    return _local_noon_boundary(now).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def astronomical_day_local_date(now: datetime | None = None) -> str:
    """
    Local calendar date (YYYY-MM-DD) of the local noon that starts the current
    astronomical day — same boundary as astronomical_day_start_utc(), expressed
    as the site's own local date. Used to name one directory/file per
    observation night (e.g. the session directory), so it stays stable across
    the local-midnight-to-local-noon window rather than flipping at UTC
    midnight or local midnight.
    """
    return _local_noon_boundary(now).strftime("%Y-%m-%d")
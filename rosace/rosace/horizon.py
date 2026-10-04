"""
Local horizon mask: reads a KStars "Artificial Horizon" text export and
answers "how high is the obstruction at this azimuth?".

File format (KStars export, one profile per file):

    # comment lines start with '#'
    Horizon Observatoire St-Pancrasse      <- profile name, free text, ignored
    220 0                                   <- "azimuth altitude", degrees
    230 9
    ...

Azimuth is measured from North through East (0 = N, 90 = E), the same
convention as astro_math.py. Points must be listed in increasing azimuth,
wrapping through 360/0 at most once (see load_horizon()). The profile is
treated as circular: the
altitude between the last and the first point interpolates across 360/0,
so a profile that doesn't close on itself still works.

A core module, like astro_math.py (which stays pure sky maths -- this one is
file parsing): horizon_file is a site fact of the Core configuration, and the
module is shared by the schedulers (scheduler_rosace_2026, and the selector's
planners through program_schedulers/checks.py) and by the command line
(cli.py's --check-config). A bad file raises HorizonFileError; each scheduler
turns it into its own SchedulerFatalError, so the core never imports a plugin.
"""

import bisect
from pathlib import Path


class HorizonFileError(ValueError):
    """The horizon file is missing, unreadable or invalid -- a configuration
    problem retrying can't fix. The message names the path and, where
    relevant, the offending line."""

# Sorted by azimuth: [(azimuth_deg, altitude_deg), ...], azimuths in [0, 360).
HorizonPoints = list[tuple[float, float]]


def _parse_pair(parts: list[str]) -> tuple[float, float] | None:
    """(azimuth, altitude) if the line is exactly two numbers, else None."""
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def load_horizon(path: Path) -> HorizonPoints:
    """Parses and validates a horizon file. Raises HorizonFileError (a
    configuration problem retrying can't fix) naming the path and, where
    relevant, the offending line."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise HorizonFileError(
            f"Horizon file not found: {path} (see horizon_file in conf/config.yaml)"
        ) from e
    except (OSError, UnicodeDecodeError) as e:
        raise HorizonFileError(f"Cannot read horizon file {path}: {e}") from e

    points: dict[float, float] = {}
    name_line_seen = False
    first_azimuth: float | None = None
    previous_azimuth: float | None = None
    wrapped_through_north = False
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        pair = _parse_pair(line.split())
        if pair is None:
            # The first non-numeric line, before any point, is the profile name.
            if not points and not name_line_seen:
                name_line_seen = True
                continue
            raise HorizonFileError(
                f"Horizon file {path}, line {line_number}: expected 'azimuth altitude', got {raw!r}"
            )

        azimuth, altitude = pair
        if not 0.0 <= azimuth <= 360.0:
            raise HorizonFileError(
                f"Horizon file {path}, line {line_number}: azimuth {azimuth} outside [0, 360]"
            )
        if not 0.0 <= altitude <= 90.0:
            raise HorizonFileError(
                f"Horizon file {path}, line {line_number}: altitude {altitude} outside [0, 90]"
            )

        azimuth %= 360.0  # 360 and 0 are the same direction

        # File order must be increasing azimuth, wrapping through 360/0 at
        # most once, and never going past the first point again after that
        # (a profile may start mid-circle, like the KStars export of a site
        # whose first point is at 220, but it covers one turn, not more).
        # Anything else is a point out of place -- almost surely a typo,
        # which sorting the points below would otherwise accept silently as
        # a plausible but wrong mask.
        out_of_order = False
        if previous_azimuth is not None and azimuth < previous_azimuth:
            out_of_order = wrapped_through_north  # a second descent
            wrapped_through_north = True
        if wrapped_through_north and azimuth > first_azimuth:
            out_of_order = True
        if out_of_order:
            raise HorizonFileError(
                f"Horizon file {path}, line {line_number}: azimuth {azimuth} is out of order "
                f"(after {previous_azimuth}) -- azimuths must increase, wrapping through 360/0 "
                f"at most once and stopping before the first point ({first_azimuth})"
            )
        if first_azimuth is None:
            first_azimuth = azimuth
        previous_azimuth = azimuth

        if azimuth in points:
            if points[azimuth] != altitude:
                raise HorizonFileError(
                    f"Horizon file {path}, line {line_number}: azimuth {azimuth} already given "
                    f"with altitude {points[azimuth]}, now {altitude}"
                )
            continue  # identical duplicate (e.g. a profile closing on its first point)
        points[azimuth] = altitude

    if len(points) < 2:
        raise HorizonFileError(
            f"Horizon file {path}: at least 2 distinct points are needed, found {len(points)}"
        )
    return sorted(points.items())


def horizon_altitude_deg(points: HorizonPoints, azimuth_deg: float) -> float:
    """Horizon altitude (degrees) at a given azimuth: linear interpolation
    between the two neighbouring points, circular across 360/0. `points` is
    what load_horizon() returns (sorted, at least 2 distinct azimuths)."""
    azimuth_deg %= 360.0
    azimuths = [p[0] for p in points]
    upper = bisect.bisect_right(azimuths, azimuth_deg)

    low_az, low_alt = points[upper - 1]  # upper == 0 wraps to the last point
    if upper == 0:
        low_az -= 360.0
    high_az, high_alt = points[upper % len(points)]  # upper == len wraps to the first point
    if upper == len(points):
        high_az += 360.0

    fraction = (azimuth_deg - low_az) / (high_az - low_az)
    return low_alt + (high_alt - low_alt) * fraction

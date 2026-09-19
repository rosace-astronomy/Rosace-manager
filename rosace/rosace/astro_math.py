# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Low-precision spherical-astronomy helpers: altitude above the horizon for an
ICRS (RA, Dec) position, given a UTC date/time and an observer's site
coordinates.

Pure math (Meeus' low-precision GMST formula + the standard spherical-
trigonometry altitude formula) -- no ephemeris/IERS data involved, unlike
AstroPy's AltAz transform. Treating the input UTC datetime as UT1 introduces
an error bounded by |UT1-UTC| < 0.9s (guaranteed by the leap-second
convention), i.e. under 15 arcseconds of sidereal time -- and the GMST
formula itself is accurate to a few arcseconds. Combined, this is accurate to
a few arcminutes, orders of magnitude better than MIN_TARGET_ALTITUDE_DEG
(config.py) needs. This replaced scheduler_rosace_2026's AstroPy-based
altitude check (2026-08-16): AstroPy's AltAz transform depends on IERS
Earth-orientation tables that either need periodic internet access to stay
fresh, or otherwise emit warnings (measured-only IERS-B, permanently behind
"now" by design) or eventually raise (predicted IERS-A/IERS_Auto, stale after
conf.auto_max_age) -- neither fits an observing site with no guaranteed
connectivity.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone


def julian_date(dt_utc: datetime) -> float:
    """Julian Date for a UTC datetime (Meeus, *Astronomical Algorithms*, ch. 7)."""
    year, month = dt_utc.year, dt_utc.month
    day = dt_utc.day + (
        dt_utc.hour + dt_utc.minute / 60 + dt_utc.second / 3600 + dt_utc.microsecond / 3.6e9
    ) / 24
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + b - 1524.5


def _greenwich_mean_sidereal_time_deg(dt_utc: datetime) -> float:
    """GMST in degrees (Meeus, low-precision polynomial)."""
    d = julian_date(dt_utc) - 2451545.0
    t = d / 36525.0
    gmst = 280.46061837 + 360.98564736629 * d + 0.000387933 * t**2 - t**3 / 38710000.0
    return gmst % 360.0


def local_sidereal_time_deg(longitude_deg: float, at: datetime | None = None) -> float:
    """Local (apparent, low-precision) sidereal time in degrees, at an
    observing site's longitude (east-positive), for a given UTC datetime
    (`at`, defaults to now). Same GMST formula/accuracy as altitude_deg --
    see module docstring."""
    at = at or datetime.now(timezone.utc)
    return (_greenwich_mean_sidereal_time_deg(at) + longitude_deg) % 360.0


def hour_angle_deg(
    ra_deg: float,
    longitude_deg: float,
    at: datetime | None = None,
) -> float:
    """Hour angle (degrees, in [-180, 180)) of an ICRS right ascension for an
    observing site (longitude_deg east-positive), at a given UTC datetime
    (`at`, defaults to now): local sidereal time minus RA. 0 = on the
    meridian, negative = before it (east, rising side), positive = after it
    (west) -- the sign convention the mount's pier-flip reasoning uses.

    Measured in degrees of hour angle, not of arc on the sky: it advances by
    15 degrees per hour whatever the declination, which is what a
    German-equatorial mount's RA axis actually sees (an arc-on-the-sky
    distance to the meridian would shrink by cos(dec) and make any
    near-polar target look permanently "on" the meridian). Same GMST
    formula/accuracy as the rest of this module (see module docstring)."""
    lst_deg = local_sidereal_time_deg(longitude_deg, at)
    return (lst_deg - ra_deg + 180.0) % 360.0 - 180.0


def altitude_deg(
    ra_deg: float,
    dec_deg: float,
    latitude_deg: float,
    longitude_deg: float,
    at: datetime | None = None,
) -> float:
    """Altitude (degrees) of an ICRS (RA, Dec) position above the horizon of
    an observing site (latitude_deg north-positive, longitude_deg
    east-positive), at a given UTC datetime (`at`, defaults to now)."""
    at = at or datetime.now(timezone.utc)
    lst_deg = local_sidereal_time_deg(longitude_deg, at)
    hour_angle_deg = (lst_deg - ra_deg + 180.0) % 360.0 - 180.0

    lat, dec, ha = math.radians(latitude_deg), math.radians(dec_deg), math.radians(hour_angle_deg)
    sin_alt = math.sin(dec) * math.sin(lat) + math.cos(dec) * math.cos(lat) * math.cos(ha)
    return math.degrees(math.asin(sin_alt))


def azimuth_deg(
    ra_deg: float,
    dec_deg: float,
    latitude_deg: float,
    longitude_deg: float,
    at: datetime | None = None,
) -> float:
    """Azimuth (degrees, in [0, 360)) of an ICRS (RA, Dec) position as seen
    from an observing site (latitude_deg north-positive, longitude_deg
    east-positive), at a given UTC datetime (`at`, defaults to now).

    Measured from North through East (0 = N, 90 = E, 180 = S, 270 = W) --
    the same convention as equatorial_from_horizontal_deg() (its exact
    inverse, together with altitude_deg()), INDI's HORIZONTAL_COORD and
    KStars. Same hour-angle/spherical-trigonometry approach, same accuracy
    class as the rest of this module (see module docstring)."""
    at = at or datetime.now(timezone.utc)
    lst_deg = local_sidereal_time_deg(longitude_deg, at)
    hour_angle = math.radians(lst_deg - ra_deg)

    lat, dec = math.radians(latitude_deg), math.radians(dec_deg)
    # atan2() below gives the azimuth measured from South, westward (Meeus,
    # ch. 13); +180 turns it into North-through-East.
    from_south = math.atan2(
        math.sin(hour_angle),
        math.cos(hour_angle) * math.sin(lat) - math.tan(dec) * math.cos(lat),
    )
    return (math.degrees(from_south) + 180.0) % 360.0


def equatorial_from_horizontal_deg(
    altitude_deg_: float,
    azimuth_deg: float,
    latitude_deg: float,
    longitude_deg: float,
    at: datetime | None = None,
) -> tuple[float, float]:
    """Inverse of altitude_deg(): given a horizontal position (altitude,
    azimuth -- degrees, azimuth East of North, matching INDI's own
    HORIZONTAL_COORD convention) above an observing site (latitude_deg
    north-positive, longitude_deg east-positive) at a given UTC datetime
    (`at`, defaults to now), returns the (RA, Dec) degrees -- in the same
    "ICRS to within a few arcminutes" sense as the rest of this module
    (precession itself is neglected, well within the accuracy budget
    already claimed by the module docstring).

    Same spherical-trigonometry identities as altitude_deg(), applied in
    reverse (declination/hour-angle swap for altitude/azimuth) -- no
    ephemeris/IERS data, same accuracy class as the rest of this module.
    Written for home_sync_am3.py's own measured-sync homing (picks a
    random azimuth at a fixed altitude, needs the equatorial coordinate to
    hand the mount) -- deliberately not AstroPy's AltAz transform, for the
    same IERS/network reason given above.
    """
    at = at or datetime.now(timezone.utc)
    lst_deg = local_sidereal_time_deg(longitude_deg, at)

    alt, az, lat = math.radians(altitude_deg_), math.radians(azimuth_deg), math.radians(latitude_deg)
    sin_dec = math.sin(alt) * math.sin(lat) + math.cos(alt) * math.cos(lat) * math.cos(az)
    dec = math.asin(sin_dec)

    sin_ha = -math.sin(az) * math.cos(alt) / math.cos(dec)
    cos_ha = (math.sin(alt) - math.sin(lat) * sin_dec) / (math.cos(lat) * math.cos(dec))
    hour_angle_deg = math.degrees(math.atan2(sin_ha, cos_ha))

    ra_deg = (lst_deg - hour_angle_deg) % 360.0
    return ra_deg, math.degrees(dec)
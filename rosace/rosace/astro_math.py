# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Low-precision spherical-astronomy helpers: altitude above the horizon for an
ICRS (RA, Dec) position, given a UTC date/time and an observer's site
coordinates; positions of the Sun and the Moon, the Moon's phase and its
distance to a target (end of the module).

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
from typing import NamedTuple


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

# ── Sun and Moon ─────────────────────────────────────────────────────────────
#
# Low-precision positions (Meeus, Astronomical Algorithms, ch. 25 for the Sun,
# ch. 47 for the Moon, main periodic terms only): within 0.02 deg for the Sun and
# 0.05 deg for the Moon of AstroPy's geocentric positions over 2026 -- plenty for a
# minimum distance of tens of degrees.
# Both are referred to J2000 by removing the general precession in longitude,
# so that they can be compared with the J2000/ICRS target coordinates used
# everywhere in Rosace. The Moon is geocentric: its parallax (up to about 1 deg,
# near the horizon) is neglected.

_J2000_OBLIQUITY_DEG = 23.4392911
_PRECESSION_DEG_PER_CENTURY = 1.3969713   # general precession in longitude

# Moon longitude terms: (D, M, M', F, coefficient in 1e-6 deg), Meeus table 47.A.
_MOON_LONGITUDE_TERMS = (
    (0, 0, 1, 0, 6288774), (2, 0, -1, 0, 1274027), (2, 0, 0, 0, 658314), (0, 0, 2, 0, 213618),
    (0, 1, 0, 0, -185116), (0, 0, 0, 2, -114332), (2, 0, -2, 0, 58793), (2, -1, -1, 0, 57066),
    (2, 0, 1, 0, 53322), (2, -1, 0, 0, 45758), (0, 1, -1, 0, -40923), (1, 0, 0, 0, -34720),
    (0, 1, 1, 0, -30383), (2, 0, 0, -2, 15327), (0, 0, 1, 2, -12528), (0, 0, 1, -2, 10980),
    (4, 0, -1, 0, 10675), (0, 0, 3, 0, 10034), (4, 0, -2, 0, 8548), (2, 1, -1, 0, -7888),
    (2, 1, 0, 0, -6766), (1, 0, -1, 0, -5163), (1, 1, 0, 0, 4987), (2, -1, 1, 0, 4036),
    (2, 0, 2, 0, 3994),
)
# Moon latitude terms: (D, M, M', F, coefficient in 1e-6 deg), Meeus table 47.B.
_MOON_LATITUDE_TERMS = (
    (0, 0, 0, 1, 5128122), (0, 0, 1, 1, 280602), (0, 0, 1, -1, 277693), (2, 0, 0, -1, 173237),
    (2, 0, -1, 1, 55413), (2, 0, -1, -1, 46271), (2, 0, 0, 1, 32573), (0, 0, 2, 1, 17198),
    (2, 0, 1, -1, 9266), (0, 0, 2, -1, 8822), (2, -1, 0, -1, 8216), (2, 0, -2, -1, 4324),
    (2, 0, 1, 1, 4200),
)


def _centuries_since_j2000(at: datetime) -> float:
    return (julian_date(at) - 2451545.0) / 36525.0


def _equatorial_j2000_from_ecliptic_of_date(longitude_deg: float, latitude_deg: float,
                                            t: float) -> tuple[float, float]:
    """(RA, Dec) J2000 degrees of an ecliptic position of date, precession
    removed in longitude only (the latitude change is negligible here)."""
    lon = math.radians(longitude_deg - _PRECESSION_DEG_PER_CENTURY * t)
    lat, eps = math.radians(latitude_deg), math.radians(_J2000_OBLIQUITY_DEG)
    ra = math.atan2(math.sin(lon) * math.cos(eps) - math.tan(lat) * math.sin(eps), math.cos(lon))
    dec = math.asin(math.sin(lat) * math.cos(eps) + math.cos(lat) * math.sin(eps) * math.sin(lon))
    return math.degrees(ra) % 360.0, math.degrees(dec)


def sun_equatorial_deg(at: datetime | None = None) -> tuple[float, float]:
    """(RA, Dec) J2000 degrees of the Sun at a UTC datetime (`at`, defaults to
    now), within 0.02 deg (Meeus ch. 25, low precision)."""
    t = _centuries_since_j2000(at or datetime.now(timezone.utc))
    mean_longitude = 280.46646 + 36000.76983 * t
    m = math.radians(357.52911 + 35999.05029 * t)
    center = ((1.914602 - 0.004817 * t) * math.sin(m) + (0.019993 - 0.000101 * t) * math.sin(2 * m)
              + 0.000289 * math.sin(3 * m))
    return _equatorial_j2000_from_ecliptic_of_date(mean_longitude + center, 0.0, t)


def moon_equatorial_deg(at: datetime | None = None) -> tuple[float, float]:
    """Geocentric (RA, Dec) J2000 degrees of the Moon at a UTC datetime (`at`,
    defaults to now), within 0.05 deg (Meeus ch. 47, main terms)."""
    t = _centuries_since_j2000(at or datetime.now(timezone.utc))
    mean_longitude = 218.3164477 + 481267.88123421 * t
    args = (297.8501921 + 445267.1114034 * t,     # D, mean elongation
            357.5291092 + 35999.0502909 * t,      # M, Sun's mean anomaly
            134.9633964 + 477198.8675055 * t,     # M', Moon's mean anomaly
            93.2720950 + 483202.0175233 * t)      # F, argument of latitude
    eccentricity = 1 - 0.002516 * t

    def periodic(terms) -> float:
        total = 0.0
        for d, m, m1, f, coefficient in terms:
            angle = math.radians(d * args[0] + m * args[1] + m1 * args[2] + f * args[3])
            total += coefficient * eccentricity ** abs(m) * math.sin(angle)
        return total

    a1 = math.radians(119.75 + 131.849 * t)
    a2 = math.radians(53.09 + 479264.290 * t)
    a3 = math.radians(313.45 + 481266.484 * t)
    lp, f = math.radians(mean_longitude), math.radians(args[3])
    m1 = math.radians(args[2])
    sum_l = periodic(_MOON_LONGITUDE_TERMS) + 3958 * math.sin(a1) + 1962 * math.sin(lp - f) + 318 * math.sin(a2)
    sum_b = (periodic(_MOON_LATITUDE_TERMS) - 2235 * math.sin(lp) + 382 * math.sin(a3)
             + 175 * math.sin(a1 - f) + 175 * math.sin(a1 + f) + 127 * math.sin(lp - m1)
             - 115 * math.sin(lp + m1))
    return _equatorial_j2000_from_ecliptic_of_date(mean_longitude + sum_l / 1e6, sum_b / 1e6, t)


def angular_separation_deg(ra1_deg: float, dec1_deg: float, ra2_deg: float, dec2_deg: float) -> float:
    """Angle (degrees) between two (RA, Dec) positions (haversine formula,
    accurate at small and large separations alike)."""
    ra1, dec1, ra2, dec2 = map(math.radians, (ra1_deg, dec1_deg, ra2_deg, dec2_deg))
    h = math.sin((dec2 - dec1) / 2) ** 2 + math.cos(dec1) * math.cos(dec2) * math.sin((ra2 - ra1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h))))


def moon_illuminated_fraction(at: datetime | None = None) -> float:
    """Illuminated fraction of the Moon's disc (0 = new, 1 = full) at a UTC
    datetime: (1 - cos elongation) / 2, the phase angle taken as 180 deg minus
    the Sun-Moon elongation (the Sun's distance makes the difference negligible)."""
    at = at or datetime.now(timezone.utc)
    elongation = angular_separation_deg(*sun_equatorial_deg(at), *moon_equatorial_deg(at))
    return (1 - math.cos(math.radians(elongation))) / 2


class MoonState(NamedTuple):
    distance_deg: float    # angle between the target and the Moon
    illumination: float    # illuminated fraction, 0-1
    altitude_deg: float    # the Moon's altitude (negative: below the horizon)


def moon_state(
    ra_deg: float,
    dec_deg: float,
    latitude_deg: float,
    longitude_deg: float,
    at: datetime | None = None,
) -> MoonState:
    """The Moon as seen from a site (latitude_deg north-positive, longitude_deg
    east-positive) relative to an ICRS (RA, Dec) target, at a UTC datetime."""
    at = at or datetime.now(timezone.utc)
    moon_ra, moon_dec = moon_equatorial_deg(at)
    return MoonState(
        distance_deg=angular_separation_deg(ra_deg, dec_deg, moon_ra, moon_dec),
        illumination=moon_illuminated_fraction(at),
        altitude_deg=altitude_deg(moon_ra, moon_dec, latitude_deg, longitude_deg, at),
    )

"""Unit tests for astro_math.py (altitude calculation with no IERS dependency)."""

from datetime import datetime, timezone

import pytest

from rosace.astro_math import altitude_deg, julian_date, local_sidereal_time_deg


class TestJulianDate:

    def test_j2000_epoch(self):
        # Standard reference: 2000-01-01T12:00:00 UTC == JD 2451545.0 exactly.
        assert julian_date(datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)) == pytest.approx(2451545.0)


class TestAltitudeDeg:

    def test_north_celestial_pole_equals_latitude_exactly(self):
        # Dec=90 -> the north celestial pole's altitude exactly equals the
        # site's latitude, regardless of RA/time (geocentric formula, no oblateness modeled).
        at = datetime(2026, 8, 16, 21, 0, 0, tzinfo=timezone.utc)
        for ra in (0.0, 123.4, 350.0):
            alt = altitude_deg(ra_deg=ra, dec_deg=90.0, latitude_deg=45.0, longitude_deg=5.0, at=at)
            assert alt == pytest.approx(45.0, abs=1e-9)

    def test_matches_known_reference_values(self):
        # Reference values obtained by comparison with astropy.coordinates.AltAz
        # (site 45N/5E/950m) during implementation -- 0.3° tolerance is well
        # within what MIN_TARGET_ALTITUDE_DEG (10°) needs.
        cases = [
            (10.0, 20.0, datetime(2026, 8, 16, 16, 3, 10, tzinfo=timezone.utc), -22.22),
            (279.23, 38.78, datetime(2026, 8, 16, 22, 30, 0, tzinfo=timezone.utc), 68.12),
            (88.79, 7.41, datetime(2026, 1, 15, 3, 0, 0, tzinfo=timezone.utc), 15.52),
            (201.3, -11.16, datetime(2026, 8, 16, 2, 0, 0, tzinfo=timezone.utc), -51.33),
        ]
        for ra, dec, at, expected in cases:
            alt = altitude_deg(ra_deg=ra, dec_deg=dec, latitude_deg=45.0, longitude_deg=5.0, at=at)
            assert alt == pytest.approx(expected, abs=0.3)

    def test_defaults_to_now_when_at_omitted(self):
        # Must not raise -- just check that calling without `at` works.
        alt = altitude_deg(ra_deg=10.0, dec_deg=20.0, latitude_deg=45.0, longitude_deg=5.0)
        assert -90.0 <= alt <= 90.0


class TestLocalSiderealTimeDeg:

    def test_object_at_lst_and_site_latitude_is_at_zenith(self):
        # Zero hour angle (RA == LST) and Dec == site latitude -> the object
        # is exactly at zenith (altitude 90°), an invariant independent of
        # any external GMST reference value.
        at = datetime(2026, 8, 16, 21, 0, 0, tzinfo=timezone.utc)
        longitude, latitude = 5.0, 45.0
        lst_deg = local_sidereal_time_deg(longitude, at)
        alt = altitude_deg(ra_deg=lst_deg, dec_deg=latitude, latitude_deg=latitude, longitude_deg=longitude, at=at)
        assert alt == pytest.approx(90.0, abs=1e-9)

    def test_result_is_within_360_degrees_range(self):
        assert 0.0 <= local_sidereal_time_deg(5.0) < 360.0

    def test_defaults_to_now_when_at_omitted(self):
        lst_deg = local_sidereal_time_deg(5.0)
        assert 0.0 <= lst_deg < 360.0

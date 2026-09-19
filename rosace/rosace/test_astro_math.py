"""Unit tests for astro_math.py (altitude calculation with no IERS dependency)."""

from datetime import datetime, timezone

import pytest

from rosace.astro_math import (
    altitude_deg,
    azimuth_deg,
    equatorial_from_horizontal_deg,
    hour_angle_deg,
    julian_date,
    local_sidereal_time_deg,
)


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


class TestHourAngleDeg:

    AT = datetime(2026, 8, 16, 21, 0, 0, tzinfo=timezone.utc)
    LONGITUDE = 5.0

    def test_ra_equal_to_lst_is_on_the_meridian(self):
        lst_deg = local_sidereal_time_deg(self.LONGITUDE, self.AT)
        assert hour_angle_deg(lst_deg, self.LONGITUDE, at=self.AT) == pytest.approx(0.0, abs=1e-9)

    def test_ra_below_lst_is_after_the_meridian_and_positive(self):
        # An object whose RA is 1.5 deg less than the LST crossed the meridian
        # 1.5 deg of hour angle ago (the V923 Aql case of 2026-09-14: +0.2 .. +1.5).
        lst_deg = local_sidereal_time_deg(self.LONGITUDE, self.AT)
        assert hour_angle_deg(lst_deg - 1.5, self.LONGITUDE, at=self.AT) == pytest.approx(1.5, abs=1e-9)

    def test_ra_above_lst_is_before_the_meridian_and_negative(self):
        lst_deg = local_sidereal_time_deg(self.LONGITUDE, self.AT)
        assert hour_angle_deg(lst_deg + 2.0, self.LONGITUDE, at=self.AT) == pytest.approx(-2.0, abs=1e-9)

    def test_result_wraps_into_minus_180_to_plus_180(self):
        for ra in (0.0, 45.0, 90.0, 179.9, 180.0, 270.0, 359.9):
            ha = hour_angle_deg(ra, self.LONGITUDE, at=self.AT)
            assert -180.0 <= ha < 180.0

    def test_wraps_across_the_ra_zero_boundary(self):
        # LST just past 0 with RA just below 360 is a small negative angle
        # difference on the circle, not ~ +360: 2 deg after the meridian.
        # Build it from a known LST rather than a fixed date.
        lst_deg = local_sidereal_time_deg(self.LONGITUDE, self.AT)
        ra = (lst_deg - 2.0) % 360.0
        assert hour_angle_deg(ra, self.LONGITUDE, at=self.AT) == pytest.approx(2.0, abs=1e-9)

    def test_on_the_meridian_south_of_zenith_matches_azimuth_180(self):
        # equatorial_from_horizontal_deg() at az=180 puts the object on the
        # meridian: hour angle 0.
        ra, _dec = equatorial_from_horizontal_deg(65.0, 180.0, 45.0, self.LONGITUDE, at=self.AT)
        assert hour_angle_deg(ra, self.LONGITUDE, at=self.AT) == pytest.approx(0.0, abs=1e-9)

    def test_advances_fifteen_degrees_per_sidereal_hour_whatever_the_declination(self):
        # The point of measuring in hour angle: no dependence on declination
        # at all (the function doesn't even take one). One hour later, the
        # same RA has moved ~15.04 deg (sidereal rate) towards the west.
        later = datetime(2026, 8, 16, 22, 0, 0, tzinfo=timezone.utc)
        before = hour_angle_deg(100.0, self.LONGITUDE, at=self.AT)
        after = hour_angle_deg(100.0, self.LONGITUDE, at=later)
        assert (after - before) % 360.0 == pytest.approx(15.0411, abs=1e-3)

    def test_defaults_to_now_when_at_omitted(self):
        ha = hour_angle_deg(120.0, 5.0)
        assert -180.0 <= ha < 180.0


class TestAzimuthDeg:

    AT = datetime(2026, 8, 16, 21, 0, 0, tzinfo=timezone.utc)
    LATITUDE, LONGITUDE = 45.0, 5.0

    def test_meridian_transit_south_of_zenith_is_due_south(self):
        # Hour angle 0 (RA == LST) with dec < latitude: transits south of zenith.
        lst_deg = local_sidereal_time_deg(self.LONGITUDE, self.AT)
        az = azimuth_deg(lst_deg, 20.0, self.LATITUDE, self.LONGITUDE, at=self.AT)
        assert az == pytest.approx(180.0, abs=1e-9)

    def test_meridian_transit_north_of_zenith_is_due_north(self):
        # Hour angle 0 with dec > latitude: transits north of zenith.
        lst_deg = local_sidereal_time_deg(self.LONGITUDE, self.AT)
        az = azimuth_deg(lst_deg, 70.0, self.LATITUDE, self.LONGITUDE, at=self.AT)
        assert az == pytest.approx(0.0, abs=1e-9) or az == pytest.approx(360.0, abs=1e-9)

    def test_object_east_of_meridian_has_azimuth_below_180(self):
        # Rising objects (hour angle < 0, i.e. RA > LST) are in the east: 0 < az < 180.
        lst_deg = local_sidereal_time_deg(self.LONGITUDE, self.AT)
        az = azimuth_deg(lst_deg + 40.0, 10.0, self.LATITUDE, self.LONGITUDE, at=self.AT)
        assert 0.0 < az < 180.0

    def test_object_west_of_meridian_has_azimuth_above_180(self):
        lst_deg = local_sidereal_time_deg(self.LONGITUDE, self.AT)
        az = azimuth_deg(lst_deg - 40.0, 10.0, self.LATITUDE, self.LONGITUDE, at=self.AT)
        assert 180.0 < az < 360.0

    def test_round_trips_through_equatorial_from_horizontal_deg(self):
        # equatorial_from_horizontal_deg() and azimuth_deg() solve the same
        # spherical triangle in opposite directions: an (alt, az) sent through
        # the first and back through the second must reproduce the same az.
        for alt, az in [(65.0, 5.0), (65.0, 90.0), (30.0, 180.0), (30.0, 270.0), (12.0, 350.0), (80.0, 170.0)]:
            ra, dec = equatorial_from_horizontal_deg(alt, az, self.LATITUDE, self.LONGITUDE, at=self.AT)
            recovered = azimuth_deg(ra, dec, self.LATITUDE, self.LONGITUDE, at=self.AT)
            assert recovered == pytest.approx(az, abs=1e-6)

    def test_result_is_within_360_degrees_range(self):
        for ra in (0.0, 90.0, 180.0, 270.0):
            az = azimuth_deg(ra, 30.0, self.LATITUDE, self.LONGITUDE, at=self.AT)
            assert 0.0 <= az < 360.0

    def test_defaults_to_now_when_at_omitted(self):
        az = azimuth_deg(120.0, 30.0, 45.0, 5.0)
        assert 0.0 <= az < 360.0


class TestEquatorialFromHorizontalDeg:

    def test_round_trips_through_altitude_deg(self):
        # For any (alt, az) this produces an (ra, dec) that, fed back through
        # altitude_deg(), must reproduce the same altitude -- the two functions
        # share the same underlying spherical triangle, just solved for
        # different unknowns.
        at = datetime(2026, 8, 16, 21, 0, 0, tzinfo=timezone.utc)
        latitude, longitude = 45.0, 5.0
        for alt, az in [(65.0, 5.0), (65.0, 90.0), (65.0, 175.0), (30.0, 45.0), (80.0, 170.0)]:
            ra, dec = equatorial_from_horizontal_deg(alt, az, latitude, longitude, at=at)
            recovered_alt = altitude_deg(ra_deg=ra, dec_deg=dec, latitude_deg=latitude, longitude_deg=longitude, at=at)
            assert recovered_alt == pytest.approx(alt, abs=1e-6)

    def test_due_south_meridian_crossing(self):
        # Az=180 (due south): hour angle is exactly 0 (meridian transit), so
        # RA == LST exactly, and the standard meridian-transit identity
        # dec = alt + lat - 90 holds (northern-hemisphere observer, object
        # transiting south of zenith).
        at = datetime(2026, 8, 16, 21, 0, 0, tzinfo=timezone.utc)
        latitude, longitude, alt = 45.0, 5.0, 65.0
        lst_deg = local_sidereal_time_deg(longitude, at)

        ra, dec = equatorial_from_horizontal_deg(alt, 180.0, latitude, longitude, at=at)

        assert ra == pytest.approx(lst_deg, abs=1e-9)
        assert dec == pytest.approx(alt + latitude - 90.0, abs=1e-9)

    def test_east_west_mirror_symmetry(self):
        # Azimuths symmetric about the meridian (az and 360-az) at the same
        # altitude are mirror images through the meridian plane: same
        # declination, and their hour angles are negatives of each other
        # (so their RAs average out to LST).
        at = datetime(2026, 8, 16, 21, 0, 0, tzinfo=timezone.utc)
        latitude, longitude, alt = 45.0, 5.0, 65.0
        lst_deg = local_sidereal_time_deg(longitude, at)

        ra_east, dec_east = equatorial_from_horizontal_deg(alt, 60.0, latitude, longitude, at=at)
        ra_west, dec_west = equatorial_from_horizontal_deg(alt, 300.0, latitude, longitude, at=at)

        assert dec_east == pytest.approx(dec_west, abs=1e-9)
        assert ((ra_east + ra_west) / 2.0) % 360.0 == pytest.approx(lst_deg, abs=1e-9)

    def test_defaults_to_now_when_at_omitted(self):
        ra, dec = equatorial_from_horizontal_deg(65.0, 90.0, 45.0, 5.0)
        assert 0.0 <= ra < 360.0
        assert -90.0 <= dec <= 90.0


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

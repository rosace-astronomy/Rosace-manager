"""
Tests for dark_library_helpers.py (temperature rounding, library bucket
naming, and the shared DARK/BIAS series lookup used by dark_acquisition's
current_session/library origins and bias_acquisition's current_session
origin).
Run: pytest plugins/sequencer_plugins/test_dark_library_helpers.py -v
"""

from __future__ import annotations

import numpy as np
import pytest
from astropy.io import fits

from rosace.plugins.sequencer_plugins.dark_library_helpers import (
    compute_camera_target_temperature,
    copy_darks_from_library,
    find_usable_frame_series,
    round_to_5,
    temperature_bucket_dirname,
)


class TestRoundTo5:

    def test_rounds_negative_up_to_next_multiple_of_5(self):
        assert round_to_5(-9) == -5

    def test_rounds_positive_up_to_next_multiple_of_5(self):
        assert round_to_5(3) == 5

    def test_value_already_a_multiple_of_5_is_unchanged(self):
        assert round_to_5(-5) == -5
        assert round_to_5(0) == 0
        assert round_to_5(10) == 10


class TestComputeCameraTargetTemperature:

    def test_user_worked_example(self):
        # Ambient 15C, cooling_capability -30C -> delta 24C -> raw -9C -> -5C
        assert compute_camera_target_temperature(ambient_temp_c=15, cooling_capability_deg_c=-30) == -5

    def test_accepts_positive_cooling_capability_too(self):
        assert compute_camera_target_temperature(ambient_temp_c=15, cooling_capability_deg_c=30) == -5


class TestTemperatureBucketDirname:

    def test_negative_temperature(self):
        assert temperature_bucket_dirname(-5) == "TempMinus5degrees"

    def test_zero(self):
        assert temperature_bucket_dirname(0) == "Temp0degrees"

    def test_positive_temperature(self):
        assert temperature_bucket_dirname(5) == "TempPlus5degrees"


def _write_frame_fits(path, *, image_type="DARK", naxis1=100, naxis2=80, exptime=300, temp_c=-5, index=1):
    data = np.zeros((naxis2, naxis1), dtype=np.uint16)
    hdu = fits.PrimaryHDU(data)
    hdu.header["EXPTIME"] = exptime
    filename = f"{image_type}_{round(temp_c)}deg_{exptime}s_2026081{index}_120000.fits"
    hdu.writeto(path / filename, overwrite=True)
    return filename


class TestFindUsableFrameSeries:

    def test_returns_none_if_directory_missing(self, tmp_path):
        assert find_usable_frame_series(
            tmp_path / "missing", image_type="DARK", temp_c=-5, count_required=3,
            exposure_time_required=300, sensor_size=(100, 80),
        ) is None

    def test_returns_none_if_not_enough_files(self, tmp_path):
        _write_frame_fits(tmp_path, index=1)
        _write_frame_fits(tmp_path, index=2)
        assert find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=3,
            exposure_time_required=300, sensor_size=(100, 80),
        ) is None

    def test_ignores_files_at_a_different_temperature(self, tmp_path):
        _write_frame_fits(tmp_path, temp_c=0, index=1)
        _write_frame_fits(tmp_path, temp_c=0, index=2)
        assert find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, sensor_size=(100, 80),
        ) is None

    def test_returns_none_if_sensor_size_does_not_match(self, tmp_path):
        _write_frame_fits(tmp_path, naxis1=100, naxis2=80, index=1)
        _write_frame_fits(tmp_path, naxis1=100, naxis2=80, index=2)
        assert find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, sensor_size=(200, 160),
        ) is None

    def test_returns_none_if_exposure_time_too_short(self, tmp_path):
        _write_frame_fits(tmp_path, exptime=100, index=1)
        _write_frame_fits(tmp_path, exptime=100, index=2)
        assert find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, sensor_size=(100, 80),
        ) is None

    def test_accepts_a_longer_exposure_time_than_required(self, tmp_path):
        _write_frame_fits(tmp_path, exptime=600, index=1)
        _write_frame_fits(tmp_path, exptime=600, index=2)
        result = find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, sensor_size=(100, 80),
        )
        assert result is not None
        assert len(result) == 2

    def test_returns_first_count_required_files_when_more_are_present(self, tmp_path):
        for i in range(1, 6):
            _write_frame_fits(tmp_path, index=i)
        result = find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=3,
            exposure_time_required=300, sensor_size=(100, 80),
        )
        assert result is not None
        assert len(result) == 3

    def test_ignores_files_of_a_different_image_type(self, tmp_path):
        _write_frame_fits(tmp_path, image_type="DARK", exptime=0, index=1)
        _write_frame_fits(tmp_path, image_type="DARK", exptime=0, index=2)
        assert find_usable_frame_series(
            tmp_path, image_type="BIAS", temp_c=-5, count_required=2,
            exposure_time_required=0, sensor_size=(100, 80),
        ) is None

    def test_finds_a_bias_series(self, tmp_path):
        _write_frame_fits(tmp_path, image_type="BIAS", exptime=0, index=1)
        _write_frame_fits(tmp_path, image_type="BIAS", exptime=0, index=2)
        result = find_usable_frame_series(
            tmp_path, image_type="BIAS", temp_c=-5, count_required=2,
            exposure_time_required=0, sensor_size=(100, 80),
        )
        assert result is not None
        assert len(result) == 2


class TestCopyDarksFromLibrary:

    def test_copies_named_files_and_returns_their_names(self, tmp_path):
        library_dir = tmp_path / "library"
        library_dir.mkdir()
        dest_dir = tmp_path / "session"
        dest_dir.mkdir()
        name = _write_frame_fits(library_dir, index=1)

        result = copy_darks_from_library(library_dir, [name], dest_dir)

        assert result == [name]
        assert (dest_dir / name).exists()
        assert (library_dir / name).exists()  # source untouched, copy not move

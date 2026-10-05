"""
Tests for dark_library_helpers.py (library bucket naming, and the shared DARK/BIAS series lookup used by dark_acquisition's
current_session/library origins and bias_acquisition's current_session
origin).
Run: pytest plugins/sequencer_plugins/test_dark_library_helpers.py -v
"""

from __future__ import annotations

import numpy as np
import pytest
from astropy.io import fits

from rosace.plugins.sequencer_plugins.dark_library_helpers import (
    copy_darks_from_library,
    ensure_dark_library_structure,
    find_usable_frame_series,
    summarize_series_root_name,
    temperature_bucket_dirname,
)


class TestTemperatureBucketDirname:

    def test_negative_temperature(self):
        assert temperature_bucket_dirname(-5) == "TempMinus5degrees"

    def test_zero(self):
        assert temperature_bucket_dirname(0) == "Temp0degrees"

    def test_positive_temperature(self):
        assert temperature_bucket_dirname(5) == "TempPlus5degrees"


def _write_frame_fits(path, *, image_type="DARK", naxis1=100, naxis2=80, exptime=300, temp_c=-5, index=1,
                      timestamp="20260815_120000"):
    """One file of a series, named like a real capture: every file of a
    series shares the same root (same timestamp), plus its own "-<index>"."""
    data = np.zeros((naxis2, naxis1), dtype=np.uint16)
    hdu = fits.PrimaryHDU(data)
    hdu.header["EXPTIME"] = exptime
    filename = f"{image_type}_{round(temp_c)}deg_{exptime}s_{timestamp}-{index}.fits"
    hdu.writeto(path / filename, overwrite=True)
    return filename


class TestFindUsableFrameSeries:

    def test_returns_none_if_directory_missing(self, tmp_path):
        assert find_usable_frame_series(
            tmp_path / "missing", image_type="DARK", temp_c=-5, count_required=3,
            exposure_time_required=300, frame_size=(100, 80),
        ) is None

    def test_returns_none_if_not_enough_files(self, tmp_path):
        _write_frame_fits(tmp_path, index=1)
        _write_frame_fits(tmp_path, index=2)
        assert find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=3,
            exposure_time_required=300, frame_size=(100, 80),
        ) is None

    def test_ignores_files_at_a_different_temperature(self, tmp_path):
        _write_frame_fits(tmp_path, temp_c=0, index=1)
        _write_frame_fits(tmp_path, temp_c=0, index=2)
        assert find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, frame_size=(100, 80),
        ) is None

    def test_accepts_a_file_within_the_temperature_tolerance(self, tmp_path):
        """A session restart can recompute a slightly different target
        temperature -- an existing series within TEMPERATURE_TOLERANCE_DEG_C
        (2C) must still be reused rather than reshot."""
        _write_frame_fits(tmp_path, temp_c=-3, index=1)
        _write_frame_fits(tmp_path, temp_c=-3, index=2)
        result = find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, frame_size=(100, 80),
        )
        assert result is not None

    def test_rejects_a_file_just_beyond_the_temperature_tolerance(self, tmp_path):
        _write_frame_fits(tmp_path, temp_c=-2, index=1)
        _write_frame_fits(tmp_path, temp_c=-2, index=2)
        assert find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, frame_size=(100, 80),
        ) is None

    def test_returns_none_if_frame_size_does_not_match(self, tmp_path):
        _write_frame_fits(tmp_path, naxis1=100, naxis2=80, index=1)
        _write_frame_fits(tmp_path, naxis1=100, naxis2=80, index=2)
        assert find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, frame_size=(200, 160),
        ) is None

    def test_returns_none_if_exposure_time_too_short(self, tmp_path):
        _write_frame_fits(tmp_path, exptime=100, index=1)
        _write_frame_fits(tmp_path, exptime=100, index=2)
        assert find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, frame_size=(100, 80),
        ) is None

    def test_accepts_a_file_within_the_exposure_time_tolerance(self, tmp_path):
        """A real camera driver doesn't guarantee EXPTIME to the millisecond
        -- a series up to EXPOSURE_TIME_TOLERANCE_SECONDS (1s) short of the
        requirement must still be reused rather than reshot."""
        _write_frame_fits(tmp_path, exptime=299.5, index=1)
        _write_frame_fits(tmp_path, exptime=299.5, index=2)
        result = find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, frame_size=(100, 80),
        )
        assert result is not None

    def test_rejects_a_file_just_beyond_the_exposure_time_tolerance(self, tmp_path):
        _write_frame_fits(tmp_path, exptime=298.5, index=1)
        _write_frame_fits(tmp_path, exptime=298.5, index=2)
        assert find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, frame_size=(100, 80),
        ) is None

    def test_accepts_a_longer_exposure_time_than_required(self, tmp_path):
        _write_frame_fits(tmp_path, exptime=600, index=1)
        _write_frame_fits(tmp_path, exptime=600, index=2)
        result = find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, frame_size=(100, 80),
        )
        assert result is not None
        assert len(result) == 2

    def test_returns_first_count_required_files_when_more_are_present(self, tmp_path):
        for i in range(1, 6):
            _write_frame_fits(tmp_path, index=i)
        result = find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=3,
            exposure_time_required=300, frame_size=(100, 80),
        )
        assert result is not None
        assert len(result) == 3

    def test_ignores_files_of_a_different_image_type(self, tmp_path):
        _write_frame_fits(tmp_path, image_type="DARK", exptime=0, index=1)
        _write_frame_fits(tmp_path, image_type="DARK", exptime=0, index=2)
        assert find_usable_frame_series(
            tmp_path, image_type="BIAS", temp_c=-5, count_required=2,
            exposure_time_required=0, frame_size=(100, 80),
        ) is None

    def test_finds_a_bias_series(self, tmp_path):
        _write_frame_fits(tmp_path, image_type="BIAS", exptime=0, index=1)
        _write_frame_fits(tmp_path, image_type="BIAS", exptime=0, index=2)
        result = find_usable_frame_series(
            tmp_path, image_type="BIAS", temp_c=-5, count_required=2,
            exposure_time_required=0, frame_size=(100, 80),
        )
        assert result is not None
        assert len(result) == 2

    def test_files_of_different_series_are_not_combined(self, tmp_path):
        """Two incomplete series must never add up to a usable one."""
        _write_frame_fits(tmp_path, index=1, timestamp="20260815_120000")
        _write_frame_fits(tmp_path, index=1, timestamp="20260815_130000")
        assert find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, frame_size=(100, 80),
        ) is None

    def test_skips_an_older_interrupted_series(self, tmp_path):
        """Night 2026-09-27: a series cut short by a stop request (1 file out
        of 5) sat first in the session directory and hid every complete
        series shot after it -- each observation reshot its darks."""
        _write_frame_fits(tmp_path, index=1, timestamp="20260815_120000")
        for i in range(1, 4):
            _write_frame_fits(tmp_path, index=i, timestamp="20260815_130000")
        result = find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=3,
            exposure_time_required=300, frame_size=(100, 80),
        )
        assert result == [f"DARK_-5deg_300s_20260815_130000-{i}.fits" for i in range(1, 4)]

    def test_skips_an_older_series_of_another_frame_size(self, tmp_path):
        """Same night: an older series shot while the camera's CCD_FRAME was
        stuck on a sub-window must not block a newer full-frame one."""
        for i in range(1, 3):
            _write_frame_fits(tmp_path, naxis1=90, naxis2=70, index=i, timestamp="20260815_120000")
            _write_frame_fits(tmp_path, index=i, timestamp="20260815_130000")
        result = find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, frame_size=(100, 80),
        )
        assert result == [f"DARK_-5deg_300s_20260815_130000-{i}.fits" for i in range(1, 3)]

    def test_picks_the_most_recent_of_several_usable_series(self, tmp_path):
        for timestamp in ("20260815_130000", "20260815_120000", "20260816_010000"):
            for i in range(1, 3):
                _write_frame_fits(tmp_path, index=i, timestamp=timestamp)
        result = find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=2,
            exposure_time_required=300, frame_size=(100, 80),
        )
        assert result == [f"DARK_-5deg_300s_20260816_010000-{i}.fits" for i in range(1, 3)]

    def test_returns_files_in_capture_order_beyond_nine(self, tmp_path):
        """"-10" must come after "-9", not after "-1"."""
        for i in range(1, 12):
            _write_frame_fits(tmp_path, index=i)
        result = find_usable_frame_series(
            tmp_path, image_type="DARK", temp_c=-5, count_required=11,
            exposure_time_required=300, frame_size=(100, 80),
        )
        assert result == [f"DARK_-5deg_300s_20260815_120000-{i}.fits" for i in range(1, 12)]


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


class TestSummarizeSeriesRootName:

    def test_replaces_trailing_series_index_with_xxx(self):
        files = [
            "DARK_-15deg_5x300s_20260815_130137-1.fits",
            "DARK_-15deg_5x300s_20260815_130137-2.fits",
        ]
        assert summarize_series_root_name(files) == "DARK_-15deg_5x300s_20260815_130137-xxx.fits"

    def test_single_file_series_has_no_index_and_is_unchanged(self):
        files = ["DARK_-15deg_300s_20260815_130137.fits"]
        assert summarize_series_root_name(files) == "DARK_-15deg_300s_20260815_130137.fits"

    def test_empty_list_returns_empty_string(self):
        assert summarize_series_root_name([]) == ""


class TestEnsureDarkLibraryStructure:

    def test_creates_one_subdirectory_per_temperature_bucket(self, tmp_path):
        library_dir = tmp_path / "dark_library"

        ensure_dark_library_structure(library_dir)

        assert {p.name for p in library_dir.iterdir()} == {
            "TempMinus20degrees", "TempMinus15degrees", "TempMinus10degrees",
            "TempMinus5degrees", "Temp0degrees",
        }

    def test_idempotent_and_leaves_existing_content_untouched(self, tmp_path):
        library_dir = tmp_path / "dark_library"
        ensure_dark_library_structure(library_dir)
        marker = library_dir / "TempMinus5degrees" / "DARK_-5deg_5x300s_20260815_120000-1.fits"
        marker.write_text("not a real fits file, just a marker")

        ensure_dark_library_structure(library_dir)  # second call, e.g. the next dark_acquisition run

        assert marker.exists()
        assert marker.read_text() == "not a real fits file, just a marker"

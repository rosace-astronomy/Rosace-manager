"""
Tests for reduction_helpers.py (shared input lookup, master calibration
frames: min/max-rejected mean, file naming and reuse; intermediate-file
directory; preprocessing of raw frames; column shifts and frame
transforms), on synthetic frames.
Run: pytest plugins/reduction_plugins/test_reduction_helpers.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.reduction_helpers import (
    find_shared_input,
    flat_band,
    intermediate_dir,
    master_filename,
    minmax_mean,
    preprocess_frames,
    resolve_master,
    series_root,
    shift_columns,
    shift_rows,
    transform_frames,
    write_float_image,
)

SHAPE = (4, 6)


def _obs(obs_dir=None, **casiers) -> Observation:
    obs = Observation(id="obs-master", created_at=datetime.now(timezone.utc), session_id="sess1234",
                      obs_dir=str(obs_dir) if obs_dir else None)
    for casier, content in casiers.items():
        getattr(obs, casier).update(content)
    return obs


def _write_series(directory, root, values, exptime=0.0):
    """One uint16 frame per value (constant frame, camera-like BZERO header);
    returns the file names in capture order."""
    names = []
    for i, value in enumerate(values, start=1):
        name = f"{root}-{i}.fits"
        hdu = fits.PrimaryHDU(np.full(SHAPE, value, dtype=np.uint16))
        hdu.header["EXPTIME"] = exptime
        hdu.header["IMAGETYP"] = "Bias Frame"
        hdu.writeto(directory / name)
        names.append(name)
    return names


class TestMinmaxMean:

    def test_rejects_highest_and_lowest_per_pixel(self):
        stack = np.array([[[1.0, 10.0]], [[2.0, 20.0]], [[3.0, 30.0]], [[4.0, 40.0]], [[100.0, 0.0]]])
        result = minmax_mean(stack)
        # pixel 0: 1 and 100 rejected -> mean(2, 3, 4); pixel 1: 0 and 40 rejected -> mean(10, 20, 30)
        assert result == pytest.approx(np.array([[3.0, 20.0]]))

    def test_three_frames_keep_the_median(self):
        stack = np.array([[[5.0]], [[1.0]], [[9.0]]])
        assert minmax_mean(stack)[0, 0] == 5.0

    def test_equal_extremes_reject_one_value_each(self):
        stack = np.array([[[7.0]], [[7.0]], [[7.0]], [[1.0]]])
        assert minmax_mean(stack)[0, 0] == 7.0

    def test_fewer_than_three_frames_is_an_error(self):
        with pytest.raises(ValueError, match="at least 3"):
            minmax_mean(np.zeros((2, 3, 3)))


class TestMasterFilename:

    def test_strips_series_index(self):
        assert master_filename(["BIAS_-5deg_5x0s_20261007_210000-1.fits", "x-2.fits"]) == \
            "BIAS_-5deg_5x0s_20261007_210000_master.fits"

    def test_single_file_without_index(self):
        assert master_filename(["FLAT_20261007.fits"]) == "FLAT_20261007_master.fits"

    def test_series_root(self):
        assert series_root(["CALIB_3x3s_20260925_194111-1.fits", "x-2.fits"]) == "CALIB_3x3s_20260925_194111"


class TestFindSharedInput:

    def test_found_in_sequencer_data(self):
        assert find_shared_input(_obs(sequencer_data={"bias_images": ["a.fits"]}), "bias_images") == ["a.fits"]

    def test_found_in_reduction_data(self):
        obs = _obs(reduction_data={"master_bias_image": "m.fits"})
        assert find_shared_input(obs, "master_bias_image") == "m.fits"

    def test_missing_names_the_key(self):
        with pytest.raises(RuntimeError, match="bias_images"):
            find_shared_input(_obs(), "bias_images")

    def test_empty_names_the_key(self):
        with pytest.raises(RuntimeError, match="bias_images.*empty"):
            find_shared_input(_obs(sequencer_data={"bias_images": []}), "bias_images")


class TestWriteMaster:

    def test_float32_without_integer_scaling_and_with_comments(self, tmp_path):
        header = fits.Header()
        header["BZERO"] = 32768
        header["BSCALE"] = 1
        header["EXPTIME"] = 3.0
        write_float_image(tmp_path / "m.fits", np.full(SHAPE, 1234.5), header, ["line one", "line two"])

        with fits.open(tmp_path / "m.fits") as hdul:
            assert hdul[0].data.dtype == np.dtype(">f4")
            assert hdul[0].data[0, 0] == pytest.approx(1234.5)
            assert "BZERO" not in hdul[0].header
            assert "BSCALE" not in hdul[0].header
            assert hdul[0].header["EXPTIME"] == 3.0
            assert list(hdul[0].header["COMMENT"]) == ["line one", "line two"]


class TestResolveMaster:

    def test_computes_and_writes_the_master(self, tmp_path):
        files = _write_series(tmp_path, "BIAS_-5deg_5x0s_20261007_210000", [100, 101, 102, 103, 5000])
        obs = _obs(tmp_path, sequencer_data={"bias_images": files})

        name, reused = resolve_master(obs, "bias_images", "Master bias")

        assert (name, reused) == ("BIAS_-5deg_5x0s_20261007_210000_master.fits", False)
        with fits.open(tmp_path / name) as hdul:
            # 100 and 5000 rejected -> mean(101, 102, 103)
            assert np.all(hdul[0].data == pytest.approx(102.0))
            comments = list(hdul[0].header["COMMENT"])
            assert comments[0] == "Master bias: 5x0s frames"
            assert comments[1] == "Per-pixel mean after rejecting the highest and lowest value"
            assert comments[2:] == [f"Source: {f}" for f in files]
            assert hdul[0].header["IMAGETYP"] == "Bias Frame"

    def test_reuses_an_existing_master_without_rewriting_it(self, tmp_path):
        files = _write_series(tmp_path, "BIAS_x", [1, 2, 3])
        existing = tmp_path / "BIAS_x_master.fits"
        existing.write_bytes(b"already here")

        name, reused = resolve_master(_obs(tmp_path, sequencer_data={"bias_images": files}), "bias_images", "Master bias")

        assert (name, reused) == ("BIAS_x_master.fits", True)
        assert existing.read_bytes() == b"already here"

    def test_finish_changes_data_and_adds_comments(self, tmp_path):
        files = _write_series(tmp_path, "FLAT_x", [10, 10, 10], exptime=3.0)

        def finish(data, comments):
            comments.append("Doubled")
            return data * 2

        name, _ = resolve_master(_obs(tmp_path, sequencer_data={"flat_images": files}), "flat_images", "Master flat", finish)

        with fits.open(tmp_path / name) as hdul:
            assert np.all(hdul[0].data == 20.0)
            assert list(hdul[0].header["COMMENT"])[0] == "Master flat: 3x3s frames"
            assert list(hdul[0].header["COMMENT"])[-1] == "Doubled"

    def test_too_few_frames_is_an_error(self, tmp_path):
        files = _write_series(tmp_path, "BIAS_x", [1, 2])
        with pytest.raises(ValueError, match="at least 3"):
            resolve_master(_obs(tmp_path, sequencer_data={"bias_images": files}), "bias_images", "Master bias")

    def test_obs_dir_required(self):
        with pytest.raises(RuntimeError, match="obs_dir"):
            resolve_master(_obs(sequencer_data={"bias_images": ["a-1.fits"]}), "bias_images", "Master bias")


class TestIntermediateDir:

    def test_named_after_queried_name_and_observation_id(self, tmp_path):
        obs = _obs(tmp_path, scheduler_data={"queried_name": "alf Lyr / Vega"})
        directory = intermediate_dir(obs)
        assert directory == tmp_path / "alfLyrVega_obs-master"
        assert directory.is_dir()

    def test_existing_directory_is_kept(self, tmp_path):
        obs = _obs(tmp_path, scheduler_data={"queried_name": "Vega"})
        (intermediate_dir(obs) / "kept.fits").write_bytes(b"x")
        assert (intermediate_dir(obs) / "kept.fits").exists()

    def test_queried_name_required(self, tmp_path):
        with pytest.raises(RuntimeError, match="queried_name"):
            intermediate_dir(_obs(tmp_path))


class TestPreprocessFrames:

    def _setup(self, tmp_path, flat, raw_values=(1300.0,)):
        fits.writeto(tmp_path / "bias_m.fits", np.full(SHAPE, 300.0, dtype=np.float32))
        fits.writeto(tmp_path / "flat_m.fits", flat.astype(np.float32))
        names = []
        for i, value in enumerate(raw_values, start=1):
            name = f"CALIB_x-{i}.fits"
            fits.writeto(tmp_path / name, np.full(SHAPE, value, dtype=np.float32))
            names.append(name)
        return _obs(tmp_path, scheduler_data={"queried_name": "Vega"},
                    sequencer_data={"calib_images": names},
                    reduction_data={"master_bias_image": "bias_m.fits", "master_flat_image": "flat_m.fits"})

    def test_subtracts_bias_and_divides_by_flat(self, tmp_path):
        flat = np.full(SHAPE, 2.0)
        flat[0, 0] = 0.5
        obs = self._setup(tmp_path, flat, raw_values=(1300.0, 2300.0))

        written, out_dir = preprocess_frames(obs, "calib_images")

        assert written == ["Vega_obs-master/CALIB_x-1_preproc.fits", "Vega_obs-master/CALIB_x-2_preproc.fits"]
        assert out_dir == tmp_path / "Vega_obs-master"
        first = fits.getdata(tmp_path / written[0])
        assert first[1, 1] == pytest.approx(500.0)   # (1300 - 300) / 2
        assert first[0, 0] == pytest.approx(2000.0)  # (1300 - 300) / 0.5
        assert fits.getdata(tmp_path / written[1])[1, 1] == pytest.approx(1000.0)

    def test_flat_at_or_below_zero_gives_zero(self, tmp_path):
        flat = np.full(SHAPE, 1.0)
        flat[0, 0] = 0.0
        flat[0, 1] = -0.01
        obs = self._setup(tmp_path, flat)

        written, _ = preprocess_frames(obs, "calib_images")

        data = fits.getdata(tmp_path / written[0])
        assert data[0, 0] == 0.0
        assert data[0, 1] == 0.0
        assert np.all(np.isfinite(data))

    def test_outside_the_lit_band_and_dead_pixels_give_zero(self, tmp_path):
        flat = np.full(SHAPE, 1.0)
        flat[0] = 0.2          # row below 50 % of the brightest: outside the lit band
        flat[2, 3] = 0.04      # dead pixel inside the band
        obs = self._setup(tmp_path, flat)

        written, _ = preprocess_frames(obs, "calib_images")

        data = fits.getdata(tmp_path / written[0])
        assert np.all(data[0] == 0.0)
        assert data[2, 3] == 0.0
        assert data[1, 1] == pytest.approx(1000.0)

    def test_frame_of_another_size_is_an_error(self, tmp_path):
        obs = self._setup(tmp_path, np.ones(SHAPE))
        fits.writeto(tmp_path / "CALIB_x-1.fits", np.ones((2, 2), dtype=np.float32), overwrite=True)
        with pytest.raises(RuntimeError, match="masters are"):
            preprocess_frames(obs, "calib_images")

    def test_masters_of_different_sizes_are_an_error(self, tmp_path):
        obs = self._setup(tmp_path, np.ones(SHAPE))
        fits.writeto(tmp_path / "flat_m.fits", np.ones((2, 2), dtype=np.float32), overwrite=True)
        with pytest.raises(RuntimeError, match="master flat"):
            preprocess_frames(obs, "calib_images")


class TestShiftColumns:

    def test_whole_pixel_shift_is_exact(self):
        data = np.zeros((10, 2))
        data[4, 0] = 7.0
        data[4, 1] = 5.0
        result = shift_columns(data, np.array([2.0, -3.0]))
        assert result[6, 0] == 7.0 and result[1, 1] == 5.0
        assert result.sum() == 12.0

    def test_half_pixel_shift_splits_the_value(self):
        data = np.zeros((10, 1))
        data[4, 0] = 8.0
        result = shift_columns(data, np.array([0.5]))
        assert result[4, 0] == pytest.approx(4.0)
        assert result[5, 0] == pytest.approx(4.0)

    def test_negative_fractional_shift(self):
        data = np.zeros((10, 1))
        data[4, 0] = 10.0
        result = shift_columns(data, np.array([-0.25]))
        assert result[3, 0] == pytest.approx(2.5)
        assert result[4, 0] == pytest.approx(7.5)

    def test_each_column_total_is_kept(self):
        rng = np.random.default_rng(0)
        data = np.zeros((60, 40))
        data[10:50] = rng.uniform(0, 1000, (40, 40))
        shifts = rng.uniform(-5, 5, 40)
        result = shift_columns(data, shifts)
        assert result.sum(axis=0) == pytest.approx(data.sum(axis=0))

    def test_entering_pixels_are_zero_and_leaving_ones_are_lost(self):
        data = np.ones((5, 1))
        result = shift_columns(data, np.array([2.0]))
        assert list(result[:, 0]) == [0.0, 0.0, 1.0, 1.0, 1.0]


class TestTransformFrames:

    def test_writes_suffixed_frames_with_extra_comments(self, tmp_path):
        obs = _obs(tmp_path, scheduler_data={"queried_name": "Vega"})
        sub = intermediate_dir(obs)
        header = fits.Header()
        header["COMMENT"] = "earlier step"
        fits.writeto(sub / "a_preproc.fits", np.full(SHAPE, 2.0, dtype=np.float32), header)

        written = transform_frames(obs, ["Vega_obs-master/a_preproc.fits"], "tilt", lambda d: d * 3, ["this step"])

        assert written == ["Vega_obs-master/a_preproc_tilt.fits"]
        with fits.open(tmp_path / written[0]) as hdul:
            assert np.all(hdul[0].data == 6.0)
            assert list(hdul[0].header["COMMENT"]) == ["earlier step", "this step"]


class TestShiftRows:

    def test_shifts_each_row_and_keeps_its_total(self):
        rng = np.random.default_rng(1)
        data = np.zeros((30, 60))
        data[:, 10:50] = rng.uniform(0, 1000, (30, 40))
        shifts = rng.uniform(-5, 5, 30)
        result = shift_rows(data, shifts)
        assert result.sum(axis=1) == pytest.approx(data.sum(axis=1))

    def test_positive_shift_moves_right(self):
        data = np.zeros((1, 10))
        data[0, 4] = 8.0
        result = shift_rows(data, np.array([1.5]))
        assert result[0, 5] == pytest.approx(4.0)
        assert result[0, 6] == pytest.approx(4.0)


class TestFlatBand:

    def test_rows_above_half_of_the_brightest_row(self):
        flat = np.zeros((20, 8))
        flat[5:15] = 1.0
        flat[9] = 1.2
        flat[4] = 0.7   # 0.7 >= 0.6: in
        flat[15] = 0.5  # 0.5 < 0.6: out
        assert flat_band(flat) == (4, 14)

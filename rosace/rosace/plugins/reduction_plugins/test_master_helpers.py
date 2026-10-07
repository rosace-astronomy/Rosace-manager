"""
Tests for master_helpers.py (master calibration frames: min/max-rejected
mean, file naming and reuse, shared input lookup), on synthetic frames.
Run: pytest plugins/reduction_plugins/test_master_helpers.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.master_helpers import (
    find_shared_input,
    master_filename,
    minmax_mean,
    resolve_master,
    write_master,
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
        write_master(tmp_path / "m.fits", np.full(SHAPE, 1234.5), header, ["line one", "line two"])

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

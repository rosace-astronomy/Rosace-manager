"""
Tests for the MASTER_FLAT sub-plugin (MasterFlatPlugin), on synthetic frames.
The combination itself is covered by test_master_helpers.py.
Run: pytest plugins/reduction_plugins/master_flat/test_master_flat.py -v
"""

from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.master_flat.master_flat import MasterFlatPlugin

SHAPE = (3, 4)
MASTER = "FLAT_5x3s_20261007_210000_master.fits"
BIAS_MASTER = "BIAS_-5deg_5x0s_20261007_205000_master.fits"


def _obs(obs_dir, files=None, bias_master=BIAS_MASTER) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(obs_dir))
    if files is not None:
        obs.sequencer_data["flat_images"] = files
    if bias_master is not None:
        obs.reduction_data["master_bias_image"] = bias_master
    return obs


def _flat_frame(left, right):
    """Left half of the frame at `left` ADU, right half at `right` ADU."""
    data = np.full(SHAPE, left, dtype=np.uint16)
    data[:, SHAPE[1] // 2:] = right
    return data


def _write_flat_series(directory, frames):
    names = []
    for i, data in enumerate(frames, start=1):
        name = f"FLAT_5x3s_20261007_210000-{i}.fits"
        hdu = fits.PrimaryHDU(data)
        hdu.header["EXPTIME"] = 3.0
        hdu.writeto(directory / name)
        names.append(name)
    return names


def _write_bias_master(directory, value=300.0, shape=SHAPE):
    fits.writeto(directory / BIAS_MASTER, np.full(shape, value, dtype=np.float32))


class TestMasterFlatPlugin:

    async def test_subtracts_bias_and_normalizes_by_the_mean(self, tmp_path):
        _write_bias_master(tmp_path)
        # Combined flat: 1300 / 3300 ADU (outlier frames rejected); minus a 300 ADU bias -> 1000 / 3000, mean 2000.
        files = _write_flat_series(tmp_path, [
            _flat_frame(1300, 3300), _flat_frame(1300, 3300), _flat_frame(1300, 3300),
            _flat_frame(0, 0), _flat_frame(60000, 60000),
        ])

        result = await MasterFlatPlugin().execute(_obs(tmp_path, files))

        assert result.reduction_data["master_flat_image"] == MASTER
        with fits.open(tmp_path / MASTER) as hdul:
            assert hdul[0].data == pytest.approx(_flat_frame(0, 0).astype(float) + np.where(
                np.arange(SHAPE[1]) < SHAPE[1] // 2, 0.5, 1.5))
            assert float(hdul[0].data.mean()) == pytest.approx(1.0)
            comments = list(hdul[0].header["COMMENT"])
            assert comments[0] == "Master flat: 5x3s frames"
            assert comments[-3:] == [
                f"Master bias subtracted: {BIAS_MASTER}",
                "Dark current neglected",
                "Normalized by its mean: 2000.0 ADU",
            ]

    async def test_second_observation_reuses_the_master(self, tmp_path):
        _write_bias_master(tmp_path)
        files = _write_flat_series(tmp_path, [_flat_frame(1300, 3300)] * 3)
        await MasterFlatPlugin().execute(_obs(tmp_path, files))
        mtime = (tmp_path / MASTER).stat().st_mtime_ns

        result = await MasterFlatPlugin().execute(_obs(tmp_path, files))

        assert result.reduction_data["master_flat_image"] == MASTER
        assert (tmp_path / MASTER).stat().st_mtime_ns == mtime

    async def test_missing_master_bias_is_an_error(self, tmp_path):
        files = _write_flat_series(tmp_path, [_flat_frame(1300, 3300)] * 3)
        with pytest.raises(RuntimeError, match="master_bias_image"):
            await MasterFlatPlugin().execute(_obs(tmp_path, files, bias_master=None))
        assert not (tmp_path / MASTER).exists()

    async def test_missing_flat_images_is_an_error(self, tmp_path):
        with pytest.raises(RuntimeError, match="flat_images"):
            await MasterFlatPlugin().execute(_obs(tmp_path))

    async def test_bias_of_another_size_is_an_error(self, tmp_path):
        _write_bias_master(tmp_path, shape=(5, 5))
        files = _write_flat_series(tmp_path, [_flat_frame(1300, 3300)] * 3)
        with pytest.raises(RuntimeError, match="master bias"):
            await MasterFlatPlugin().execute(_obs(tmp_path, files))

    async def test_flat_without_signal_is_an_error(self, tmp_path):
        _write_bias_master(tmp_path)
        files = _write_flat_series(tmp_path, [_flat_frame(300, 300)] * 3)
        with pytest.raises(RuntimeError, match="cannot normalize"):
            await MasterFlatPlugin().execute(_obs(tmp_path, files))

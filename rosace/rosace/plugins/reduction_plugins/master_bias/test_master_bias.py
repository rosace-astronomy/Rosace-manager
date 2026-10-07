"""
Tests for the MASTER_BIAS sub-plugin (MasterBiasPlugin), on synthetic frames.
The combination itself is covered by test_master_helpers.py.
Run: pytest plugins/reduction_plugins/master_bias/test_master_bias.py -v
"""

from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.master_bias.master_bias import MasterBiasPlugin


def _obs(obs_dir, files=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(obs_dir))
    if files is not None:
        obs.sequencer_data["bias_images"] = files
    return obs


def _write_bias_series(directory, values):
    names = []
    for i, value in enumerate(values, start=1):
        name = f"BIAS_-5deg_5x0s_20261007_210000-{i}.fits"
        hdu = fits.PrimaryHDU(np.full((3, 5), value, dtype=np.uint16))
        hdu.header["EXPTIME"] = 0.0
        hdu.writeto(directory / name)
        names.append(name)
    return names


class TestMasterBiasPlugin:

    async def test_computes_master_and_records_shared_key(self, tmp_path):
        files = _write_bias_series(tmp_path, [300, 301, 302, 303, 60000])

        result = await MasterBiasPlugin().execute(_obs(tmp_path, files))

        name = "BIAS_-5deg_5x0s_20261007_210000_master.fits"
        assert result.reduction_data == {"master_bias_image": name}
        assert np.all(fits.getdata(tmp_path / name) == pytest.approx(302.0))

    async def test_second_observation_reuses_the_master(self, tmp_path):
        files = _write_bias_series(tmp_path, [1, 2, 3])
        await MasterBiasPlugin().execute(_obs(tmp_path, files))
        master = tmp_path / "BIAS_-5deg_5x0s_20261007_210000_master.fits"
        mtime = master.stat().st_mtime_ns

        result = await MasterBiasPlugin().execute(_obs(tmp_path, files))

        assert result.reduction_data["master_bias_image"] == master.name
        assert master.stat().st_mtime_ns == mtime

    async def test_missing_bias_images_is_an_error(self, tmp_path):
        with pytest.raises(RuntimeError, match="bias_images"):
            await MasterBiasPlugin().execute(_obs(tmp_path))

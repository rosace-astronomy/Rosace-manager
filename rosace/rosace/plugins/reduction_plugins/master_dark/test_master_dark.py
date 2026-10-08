"""
Tests for the MASTER_DARK sub-plugin (MasterDarkPlugin), on synthetic frames.
The combination itself is covered by test_reduction_helpers.py.
Run: pytest plugins/reduction_plugins/master_dark/test_master_dark.py -v
"""

from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.master_dark.master_dark import MasterDarkPlugin

MASTER = "DARK_-5deg_5x180s_20261007_210000_master.fits"


def _obs(obs_dir, files=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(obs_dir))
    if files is not None:
        obs.sequencer_data["dark_images"] = files
    return obs


def _write_dark_series(directory, values):
    names = []
    for i, value in enumerate(values, start=1):
        name = f"DARK_-5deg_5x180s_20261007_210000-{i}.fits"
        hdu = fits.PrimaryHDU(np.full((3, 5), value, dtype=np.uint16))
        hdu.header["EXPTIME"] = 180.0
        hdu.writeto(directory / name)
        names.append(name)
    return names


class TestMasterDarkPlugin:

    async def test_computes_master_keeping_the_bias(self, tmp_path):
        # Bias level ~300 ADU plus dark current: nothing is subtracted.
        files = _write_dark_series(tmp_path, [320, 0, 322, 324, 65000])

        result = await MasterDarkPlugin().execute(_obs(tmp_path, files))

        assert result.reduction_data == {"master_dark_image": MASTER}
        with fits.open(tmp_path / MASTER) as hdul:
            assert np.all(hdul[0].data == pytest.approx(322.0))
            comments = list(hdul[0].header["COMMENT"])
            assert comments[0] == "Master dark: 5x180s frames"
            assert comments[-1] == "Bias not subtracted"

    async def test_second_observation_reuses_the_master(self, tmp_path):
        files = _write_dark_series(tmp_path, [1, 2, 3])
        await MasterDarkPlugin().execute(_obs(tmp_path, files))
        mtime = (tmp_path / MASTER).stat().st_mtime_ns

        result = await MasterDarkPlugin().execute(_obs(tmp_path, files))

        assert result.reduction_data["master_dark_image"] == MASTER
        assert (tmp_path / MASTER).stat().st_mtime_ns == mtime

    async def test_missing_dark_images_is_an_error(self, tmp_path):
        with pytest.raises(RuntimeError, match="dark_images"):
            await MasterDarkPlugin().execute(_obs(tmp_path))

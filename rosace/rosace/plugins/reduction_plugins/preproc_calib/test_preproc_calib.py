"""
Tests for the PREPROC_CALIB sub-plugin (PreprocCalibPlugin), on synthetic frames.
The computation itself is covered by test_reduction_helpers.py.
Run: pytest plugins/reduction_plugins/preproc_calib/test_preproc_calib.py -v
"""

from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.preproc_calib.preproc_calib import PreprocCalibPlugin

SHAPE = (3, 4)


def _obs(session_dir, calib_files=None) -> Observation:
    obs = Observation(id="0e37017f", created_at=datetime.now(timezone.utc), session_id="sess1234",
                      obs_dir=str(session_dir))
    obs.scheduler_data["queried_name"] = "Vega"
    if calib_files is not None:
        obs.sequencer_data["calib_images"] = calib_files
    obs.reduction_data["master_bias_image"] = "BIAS_master.fits"
    obs.reduction_data["master_dark_image"] = "DARK_master.fits"
    obs.reduction_data["master_flat_image"] = "FLAT_master.fits"
    return obs


def _write_inputs(session_dir):
    fits.writeto(session_dir / "BIAS_master.fits", np.full(SHAPE, 300.0, dtype=np.float32))
    fits.writeto(session_dir / "DARK_master.fits", np.full(SHAPE, 300.0, dtype=np.float32))
    fits.writeto(session_dir / "FLAT_master.fits", np.full(SHAPE, 2.0, dtype=np.float32))
    names = []
    for i in (1, 2, 3):
        name = f"CALIB_3x3s_20261008_210000-{i}.fits"
        hdu = fits.PrimaryHDU(np.full(SHAPE, 1300, dtype=np.uint16))
        hdu.header["EXPTIME"] = 3.0
        hdu.writeto(session_dir / name)
        names.append(name)
    return names


class TestPreprocCalibPlugin:

    async def test_writes_corrected_frames_and_shared_key(self, tmp_path):
        names = _write_inputs(tmp_path)

        result = await PreprocCalibPlugin().execute(_obs(tmp_path, names))

        expected = [f"Vega_0e37017f/{n[:-5]}_preproc.fits" for n in names]
        assert result.reduction_data["preproc_calib_images"] == expected
        for rel in expected:
            data = fits.getdata(tmp_path / rel)
            assert np.all(data == pytest.approx(500.0))  # (1300 - 300) / 2

    async def test_missing_calib_images_is_an_error(self, tmp_path):
        _write_inputs(tmp_path)
        with pytest.raises(RuntimeError, match="calib_images"):
            await PreprocCalibPlugin().execute(_obs(tmp_path))

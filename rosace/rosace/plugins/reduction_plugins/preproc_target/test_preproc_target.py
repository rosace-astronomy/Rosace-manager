"""
Tests for the PREPROC_TARGET sub-plugin (PreprocTargetPlugin), on synthetic frames.
The computation itself is covered by test_reduction_helpers.py.
Run: pytest plugins/reduction_plugins/preproc_target/test_preproc_target.py -v
"""

from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from rosace.models import Observation
from rosace.plugins.reduction_plugins.preproc_target.preproc_target import PreprocTargetPlugin

SHAPE = (3, 4)


def _obs(session_dir, star_files) -> Observation:
    obs = Observation(id="0e37017f", created_at=datetime.now(timezone.utc), session_id="sess1234",
                      obs_dir=str(session_dir))
    obs.scheduler_data["queried_name"] = "alf Lyr"
    obs.sequencer_data["star_spectra_images"] = star_files
    obs.reduction_data["master_bias_image"] = "BIAS_master.fits"
    obs.reduction_data["master_flat_image"] = "FLAT_master.fits"
    return obs


def _write_inputs(session_dir):
    fits.writeto(session_dir / "BIAS_master.fits", np.full(SHAPE, 300.0, dtype=np.float32))
    fits.writeto(session_dir / "FLAT_master.fits", np.full(SHAPE, 0.5, dtype=np.float32))
    names = []
    for i in (1, 2):
        name = f"STAR_alfLyr_5x4s_20261008_210000-{i}.fits"
        hdu = fits.PrimaryHDU(np.full(SHAPE, 300 + 100 * i, dtype=np.uint16))
        hdu.header["EXPTIME"] = 4.0
        hdu.writeto(session_dir / name)
        names.append(name)
    return names


class TestPreprocTargetPlugin:

    async def test_writes_corrected_frames_and_shared_key(self, tmp_path):
        names = _write_inputs(tmp_path)

        result = await PreprocTargetPlugin().execute(_obs(tmp_path, names))

        expected = [f"alfLyr_0e37017f/{n[:-5]}_preproc.fits" for n in names]
        assert result.reduction_data["preproc_target_images"] == expected
        for i, rel in enumerate(expected, start=1):
            with fits.open(tmp_path / rel) as hdul:
                # (300 + 100 i - 300) / 0.5
                assert np.all(hdul[0].data == pytest.approx(200.0 * i))
                assert hdul[0].data.dtype == np.dtype(">f4")
                assert "BZERO" not in hdul[0].header
                assert hdul[0].header["EXPTIME"] == 4.0
                assert list(hdul[0].header["COMMENT"]) == [
                    "Master bias subtracted: BIAS_master.fits",
                    "Divided by master flat: FLAT_master.fits",
                    "Dark not subtracted",
                ]

    async def test_raw_frames_are_left_untouched(self, tmp_path):
        names = _write_inputs(tmp_path)
        before = (tmp_path / names[0]).read_bytes()

        await PreprocTargetPlugin().execute(_obs(tmp_path, names))

        assert (tmp_path / names[0]).read_bytes() == before

    async def test_missing_master_flat_is_an_error(self, tmp_path):
        names = _write_inputs(tmp_path)
        obs = _obs(tmp_path, names)
        del obs.reduction_data["master_flat_image"]
        with pytest.raises(RuntimeError, match="master_flat_image"):
            await PreprocTargetPlugin().execute(obs)

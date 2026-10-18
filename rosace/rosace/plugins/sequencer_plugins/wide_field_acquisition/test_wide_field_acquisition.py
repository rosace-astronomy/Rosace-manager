"""
Tests for the WIDE_FIELD_ACQUISITION sub-plugin (WideFieldAcquisitionPlugin):
one exposure of a fake pointing camera, written with the target stamped in
its header; nothing without a pointing camera; never raises.
Run: pytest plugins/sequencer_plugins/wide_field_acquisition/test_wide_field_acquisition.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import numpy as np
from astropy.io import fits

from rosace.models import Observation, Target
from rosace.plugins.sequencer_plugins.wide_field_acquisition import wide_field_acquisition
from rosace.plugins.sequencer_plugins.wide_field_acquisition.wide_field_acquisition import WideFieldAcquisitionPlugin


def _obs(tmp_path, camera=None) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(tmp_path))
    obs.scheduler_data["target"] = Target(name="* alf Lyr", ra=279.23, dec=38.78)
    obs.scheduler_data["queried_name"] = "Vega"
    if camera is not None:
        obs.hardware["pointing_camera"] = camera
    return obs


def _camera() -> AsyncMock:
    camera = AsyncMock()
    camera.expose.return_value = fits.HDUList([fits.PrimaryHDU(np.zeros((4, 6), dtype=np.uint16))])
    return camera


async def test_one_exposure_written_with_the_target_stamped(tmp_path, monkeypatch):
    monkeypatch.setattr(wide_field_acquisition, "SUBPLUGIN_PARAMS", {"wide_field": {"exposure_seconds": 3}})
    camera = _camera()
    obs = await WideFieldAcquisitionPlugin().execute(_obs(tmp_path, camera))

    camera.expose.assert_awaited_once_with(3.0)
    (name,) = obs.sequencer_data["wide_field_images"]
    assert name.startswith("WIDEFIELD_Vega_3s_") and name.endswith(".fits")
    header = fits.getheader(tmp_path / name)
    assert (header["RA"], header["DEC"], header["EQUINOX"], header["OBJECT"]) == (279.23, 38.78, 2000, "Vega")


async def test_default_exposure_time(tmp_path, monkeypatch):
    monkeypatch.setattr(wide_field_acquisition, "SUBPLUGIN_PARAMS", {})
    camera = _camera()
    await WideFieldAcquisitionPlugin().execute(_obs(tmp_path, camera))
    camera.expose.assert_awaited_once_with(wide_field_acquisition.DEFAULT_EXPOSURE_SECONDS)


async def test_nothing_without_a_pointing_camera(tmp_path, caplog):
    caplog.set_level("INFO", logger="spectro.subplugin.wide_field_acquisition")
    obs = await WideFieldAcquisitionPlugin().execute(_obs(tmp_path))
    assert "wide_field_images" not in obs.sequencer_data
    assert "Not used" in caplog.text
    assert list(tmp_path.iterdir()) == []


async def test_failed_exposure_never_raises(tmp_path, caplog):
    camera = AsyncMock()
    camera.expose.side_effect = RuntimeError("USB lost")
    obs = await WideFieldAcquisitionPlugin().execute(_obs(tmp_path, camera))
    assert "wide_field_images" not in obs.sequencer_data
    assert any(r.levelname == "WARNING" and "USB lost" in r.message for r in caplog.records)

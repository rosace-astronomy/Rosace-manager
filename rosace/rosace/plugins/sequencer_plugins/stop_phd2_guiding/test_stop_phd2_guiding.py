"""
Tests for the STOP_PHD2_GUIDING sub-plugin (StopPhd2GuidingPlugin), indi
backend. capture_guide_image()/solve_wcs()/world_to_pixel() are all
monkeypatched here -- their own behavior is covered by acquisition_helpers'
and plate_solve_helper's own test files; these tests only cover this
sub-plugin's own orchestration.
Run: pytest plugins/sequencer_plugins/stop_phd2_guiding/test_stop_phd2_guiding.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from astropy.coordinates import SkyCoord

from rosace.models import Observation, Target
from rosace.plugins.sequencer_plugins.stop_phd2_guiding import stop_phd2_guiding
from rosace.plugins.sequencer_plugins.stop_phd2_guiding.stop_phd2_guiding import StopPhd2GuidingPlugin

TARGET_NAME = "* alf Lyr"
QUERIED_NAME = "Vega"
TARGET_RA, TARGET_DEC = 279.23, 38.78
EXPOSURE_SECONDS = 0.3
SAMPLING_ARCSEC = 1.75
SLIT_X, SLIT_Y = 10.0, 12.0

TARGET_COORD = SkyCoord(ra=TARGET_RA, dec=TARGET_DEC, unit="deg", frame="icrs")
TARGET_PIXEL_X, TARGET_PIXEL_Y = 15.0, 20.0
GUIDE_IMAGE_NAME = "GUIDING_Vega_0_30s_20260830_120000.fits"


def _make_obs() -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir="/tmp/obsdir",
    )
    obs.scheduler_data["target"] = Target(name=TARGET_NAME, ra=TARGET_RA, dec=TARGET_DEC)
    obs.scheduler_data["queried_name"] = QUERIED_NAME
    obs.hardware["phd2"] = AsyncMock()
    obs.hardware["guiding_camera"] = AsyncMock()
    return obs


@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch):
    image = MagicMock()
    image.name = GUIDE_IMAGE_NAME
    capture_mock = AsyncMock(return_value=image)
    solve_wcs_mock = AsyncMock()
    world_to_pixel_mock = MagicMock(return_value=(TARGET_PIXEL_X, TARGET_PIXEL_Y))
    monkeypatch.setattr(stop_phd2_guiding, "capture_guide_image", capture_mock)
    monkeypatch.setattr(stop_phd2_guiding, "solve_wcs", solve_wcs_mock)
    monkeypatch.setattr(stop_phd2_guiding, "world_to_pixel", world_to_pixel_mock)
    monkeypatch.setattr(stop_phd2_guiding, "SUBPLUGIN_PARAMS", {
        "guiding": {
            "exposure_seconds": EXPOSURE_SECONDS, "sampling_arcsec": SAMPLING_ARCSEC,
            "slit_x": SLIT_X, "slit_y": SLIT_Y,
        },
    })
    return {"capture_guide_image": capture_mock, "solve_wcs": solve_wcs_mock, "world_to_pixel": world_to_pixel_mock}


class TestStopPhd2GuidingPlugin:

    async def test_stops_phd2_capture_first(self):
        obs = _make_obs()

        await StopPhd2GuidingPlugin().execute(obs)

        obs.hardware["phd2"].stop_capture.assert_awaited_once_with()

    async def test_captures_one_post_science_guide_image_at_target_position(self, mock_dependencies):
        obs = _make_obs()

        await StopPhd2GuidingPlugin().execute(obs)

        mock_dependencies["capture_guide_image"].assert_awaited_once()
        call = mock_dependencies["capture_guide_image"].await_args
        passed_obs, exposure = call.args
        assert passed_obs is obs
        assert exposure == EXPOSURE_SECONDS
        assert call.kwargs["ra_deg"] == pytest.approx(TARGET_RA)
        assert call.kwargs["dec_deg"] == pytest.approx(TARGET_DEC)
        assert call.kwargs["target_name"] == QUERIED_NAME
        assert call.kwargs["image_to_gui"] is True

    async def test_solves_without_recording_a_pointing_error(self, mock_dependencies):
        # pointing_errors only ever records the mount's own *initial* pointing error
        # (measured right after its first slew of the night) -- a plate-solve-corrected
        # post-science position must never be recorded there, or it pollutes the
        # long-term polar-alignment drift signal that table exists for.
        obs = _make_obs()

        await StopPhd2GuidingPlugin().execute(obs)

        mock_dependencies["solve_wcs"].assert_awaited_once()
        call = mock_dependencies["solve_wcs"].await_args
        assert call.kwargs["sampling_arcsec"] == SAMPLING_ARCSEC
        assert "record_error" not in call.kwargs
        assert "observation_id" not in call.kwargs

    async def test_sets_sequencer_data_with_guide_image_and_residual(self):
        obs = _make_obs()

        result = await StopPhd2GuidingPlugin().execute(obs)

        assert result is obs
        expected_residual = (
            (TARGET_PIXEL_X - SLIT_X) ** 2 + (TARGET_PIXEL_Y - SLIT_Y) ** 2
        ) ** 0.5 * SAMPLING_ARCSEC
        data = obs.sequencer_data["stop_phd2_guiding"]
        assert data["guide_image"] == GUIDE_IMAGE_NAME
        assert data["residual_arcsec"] == pytest.approx(expected_residual)

"""
Tests for the TEMPORARY diagnostic sub-plugin StopPhd2GuidingDiagPlugin,
indi backend. capture_guide_image()/solve_wcs()/world_to_pixel() are all
monkeypatched here -- their own behavior is covered by acquisition_helpers'
and plate_solve_helper's own test files; these tests only cover this
sub-plugin's own orchestration.
Run: pytest plugins/sequencer_plugins/stop_phd2_guiding_diag/test_stop_phd2_guiding_diag.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from astropy.coordinates import SkyCoord

from rosace.models import Observation, Target
from rosace.plugins.sequencer_plugins.stop_phd2_guiding_diag import stop_phd2_guiding_diag
from rosace.plugins.sequencer_plugins.stop_phd2_guiding_diag.stop_phd2_guiding_diag import (
    SHOT_COUNT,
    StopPhd2GuidingDiagPlugin,
)

TARGET_NAME = "* alf Lyr"
QUERIED_NAME = "Vega"
TARGET_RA, TARGET_DEC = 279.23, 38.78
EXPOSURE_SECONDS = 0.3
SAMPLING_ARCSEC = 1.75
SLIT_X, SLIT_Y = 10.0, 12.0

TARGET_COORD = SkyCoord(ra=TARGET_RA, dec=TARGET_DEC, unit="deg", frame="icrs")
# A different pixel position per shot, so the per-shot residual/stats math is
# genuinely exercised rather than collapsing to a degenerate stdev of 0.
SHOT_PIXELS = [(15.0, 20.0), (14.0, 19.0), (16.0, 21.0), (13.5, 18.5), (15.5, 20.5)]


def _make_obs() -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir="/tmp/obsdir",
    )
    obs.scheduler_data["target"] = Target(name=TARGET_NAME, ra=TARGET_RA, dec=TARGET_DEC)
    obs.scheduler_data["queried_name"] = QUERIED_NAME
    obs.hardware["phd2"] = AsyncMock()
    obs.hardware["phd2"].get_calibration_data.return_value = {
        "calibrated": True, "xAngle": 0.0, "yAngle": 90.0, "xRate": 1.0, "yRate": 1.0,
    }
    obs.hardware["guiding_camera"] = AsyncMock()
    obs.hardware_config["guiding_camera"] = {
        "exposure_seconds": EXPOSURE_SECONDS, "sampling_arcsec": SAMPLING_ARCSEC,
        "slit_x": SLIT_X, "slit_y": SLIT_Y,
    }
    return obs


@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch):
    images = []
    for i in range(SHOT_COUNT):
        image = MagicMock()
        image.name = f"GUIDING_Vega_0_30s_20260830_12000{i}-{i + 1}.fits"
        images.append(image)
    capture_mock = AsyncMock(side_effect=images)
    solve_wcs_mock = AsyncMock()
    world_to_pixel_mock = MagicMock(side_effect=SHOT_PIXELS)
    monkeypatch.setattr(stop_phd2_guiding_diag, "capture_guide_image", capture_mock)
    monkeypatch.setattr(stop_phd2_guiding_diag, "solve_wcs", solve_wcs_mock)
    monkeypatch.setattr(stop_phd2_guiding_diag, "world_to_pixel", world_to_pixel_mock)
    return {"capture_guide_image": capture_mock, "solve_wcs": solve_wcs_mock, "world_to_pixel": world_to_pixel_mock}


class TestStopPhd2GuidingDiagPlugin:

    async def test_stops_phd2_capture_first(self):
        obs = _make_obs()

        await StopPhd2GuidingDiagPlugin().execute(obs)

        obs.hardware["phd2"].stop_capture.assert_awaited_once_with()

    async def test_queries_phd2_calibration_data_once(self):
        obs = _make_obs()

        await StopPhd2GuidingDiagPlugin().execute(obs)

        obs.hardware["phd2"].get_calibration_data.assert_awaited_once_with()

    async def test_captures_shot_count_guide_images_at_target_position(self, mock_dependencies):
        obs = _make_obs()

        await StopPhd2GuidingDiagPlugin().execute(obs)

        calls = mock_dependencies["capture_guide_image"].await_args_list
        assert len(calls) == SHOT_COUNT
        for call in calls:
            passed_obs, exposure = call.args
            assert passed_obs is obs
            assert exposure == EXPOSURE_SECONDS
            assert call.kwargs["ra_deg"] == pytest.approx(TARGET_RA)
            assert call.kwargs["dec_deg"] == pytest.approx(TARGET_DEC)
            assert call.kwargs["target_name"] == QUERIED_NAME
            assert call.kwargs["image_to_gui"] is True
        # Each shot gets its own filename -- no collision between successive captures.
        filenames = {call.kwargs["filename"] for call in calls}
        assert len(filenames) == SHOT_COUNT

    async def test_every_solve_records_pointing_error(self, mock_dependencies):
        obs = _make_obs()

        await StopPhd2GuidingDiagPlugin().execute(obs)

        calls = mock_dependencies["solve_wcs"].await_args_list
        assert len(calls) == SHOT_COUNT
        for call in calls:
            assert call.kwargs["sampling_arcsec"] == SAMPLING_ARCSEC
            assert call.kwargs["record_error"] is True
            assert call.kwargs["observation_id"] == obs.id

    async def test_sets_sequencer_data_with_per_shot_and_summary_stats(self):
        obs = _make_obs()

        result = await StopPhd2GuidingDiagPlugin().execute(obs)

        assert result is obs
        data = obs.sequencer_data["stop_phd2_guiding_diag"]
        assert len(data["shots"]) == SHOT_COUNT

        expected_residuals = [
            ((x - SLIT_X) ** 2 + (y - SLIT_Y) ** 2) ** 0.5 * SAMPLING_ARCSEC for x, y in SHOT_PIXELS
        ]
        for shot, (x, y), expected_residual in zip(data["shots"], SHOT_PIXELS, expected_residuals):
            assert shot["target_x_px"] == pytest.approx(x)
            assert shot["target_y_px"] == pytest.approx(y)
            assert shot["dx_px"] == pytest.approx(x - SLIT_X)
            assert shot["dy_px"] == pytest.approx(y - SLIT_Y)
            assert shot["residual_arcsec"] == pytest.approx(expected_residual)

        expected_mean = sum(expected_residuals) / SHOT_COUNT
        expected_stdev = (sum((r - expected_mean) ** 2 for r in expected_residuals) / SHOT_COUNT) ** 0.5
        assert data["mean_residual_arcsec"] == pytest.approx(expected_mean)
        assert data["stdev_residual_arcsec"] == pytest.approx(expected_stdev)

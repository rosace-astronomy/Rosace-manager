"""
Tests for the STOP_PHD2_GUIDING sub-plugin (StopPhd2GuidingPlugin), indi
backend. capture_guide_image()/solve_wcs()/world_to_pixel() are all
monkeypatched here -- their own behavior is covered by acquisition_helpers'
and plate_solve_helper's own test files; these tests only cover this
sub-plugin's own orchestration.
Run: pytest plugins/sequencer_plugins/stop_phd2_guiding/test_stop_phd2_guiding.py -v
"""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from astropy.coordinates import SkyCoord

from rosace.models import Observation, Target
from rosace.phd2.guide_log import RecordedSection
from rosace.plugins.sequencer_plugins import acquisition_helpers
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
    obs.hardware["phd2"].guide_log_section = MagicMock(return_value=None)
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
    monkeypatch.setattr(stop_phd2_guiding, "SETTLE_AFTER_STOP_SECONDS", 0)
    guiding = {
        "exposure_seconds": EXPOSURE_SECONDS, "sampling_arcsec": SAMPLING_ARCSEC,
        "slit_x": SLIT_X, "slit_y": SLIT_Y,
    }
    monkeypatch.setattr(stop_phd2_guiding, "SUBPLUGIN_PARAMS", {"guiding": guiding})
    # slit_position() (not patched) reads the default slit from acquisition_helpers' own copy
    monkeypatch.setattr(acquisition_helpers, "SUBPLUGIN_PARAMS", {"guiding": guiding})
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

    async def test_waits_between_phd2_stop_and_guide_capture(self, monkeypatch, mock_dependencies):
        order = []
        obs = _make_obs()
        obs.hardware["phd2"].stop_capture.side_effect = lambda: order.append("stop")
        monkeypatch.setattr(stop_phd2_guiding, "SETTLE_AFTER_STOP_SECONDS", 3.0)

        async def fake_sleep(seconds):
            order.append(("sleep", seconds))

        async def fake_capture(*args, **kwargs):
            order.append("capture")
            return MagicMock()

        monkeypatch.setattr(stop_phd2_guiding.asyncio, "sleep", fake_sleep)
        mock_dependencies["capture_guide_image"].side_effect = fake_capture

        await StopPhd2GuidingPlugin().execute(obs)

        assert order == ["stop", ("sleep", 3.0), "capture"]

    async def test_guide_capture_failure_does_not_fail_the_observation(self, mock_dependencies, caplog):
        obs = _make_obs()
        mock_dependencies["capture_guide_image"].side_effect = RuntimeError("Timeout while waiting for BLOB")

        with caplog.at_level("WARNING"):
            result = await StopPhd2GuidingPlugin().execute(obs)

        assert result is obs
        assert "stop_phd2_guiding" not in obs.sequencer_data
        assert "Post-science pointing check failed" in caplog.text
        assert "Timeout while waiting for BLOB" in caplog.text

    async def test_plate_solve_failure_does_not_fail_the_observation(self, mock_dependencies):
        obs = _make_obs()
        mock_dependencies["solve_wcs"].side_effect = RuntimeError("no WCS solution")

        result = await StopPhd2GuidingPlugin().execute(obs)

        assert result is obs
        assert "stop_phd2_guiding" not in obs.sequencer_data

    async def test_phd2_stop_failure_still_fails_the_step(self, mock_dependencies):
        # Only the post-science check is optional: if PHD2 itself can't be stopped, it
        # keeps guiding into the next observation, which must not be silently ignored.
        obs = _make_obs()
        obs.hardware["phd2"].stop_capture.side_effect = RuntimeError("PHD2 unreachable")

        with pytest.raises(RuntimeError, match="PHD2 unreachable"):
            await StopPhd2GuidingPlugin().execute(obs)

        mock_dependencies["capture_guide_image"].assert_not_awaited()

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

    async def test_residual_is_measured_against_the_calculated_slit(self):
        obs = _make_obs()
        obs.sequencer_data["slit_xy"] = {"status": "calculated", "slit_x": SLIT_X, "slit_y": 15.0, "image": "a.fits"}

        await StopPhd2GuidingPlugin().execute(obs)

        expected_residual = (
            (TARGET_PIXEL_X - SLIT_X) ** 2 + (TARGET_PIXEL_Y - 15.0) ** 2
        ) ** 0.5 * SAMPLING_ARCSEC
        assert obs.sequencer_data["stop_phd2_guiding"]["residual_arcsec"] == pytest.approx(expected_residual)


class TestGuideLog:

    async def test_writes_the_section_started_during_the_observation(self, tmp_path, caplog):
        obs = _make_obs()
        obs.obs_dir = str(tmp_path)
        obs.yaml_file = str(tmp_path / "20261017T012650_Vega.yaml")
        section = RecordedSection(obs.created_at + timedelta(minutes=2), "Guiding Begins at ...\n")
        obs.hardware["phd2"].guide_log_section = MagicMock(return_value=section)

        with caplog.at_level(logging.INFO, logger="spectro.subplugin.stop_phd2_guiding"):
            await StopPhd2GuidingPlugin().execute(obs)

        name = "20261017T012650_Vega_obs12345_PHD2_GuideLog.txt"
        assert obs.sequencer_data["guide_log_file"] == name
        assert (tmp_path / name).read_text() == section.text
        assert f"Guide log written: {name}" in caplog.text

    async def test_a_section_from_before_the_observation_is_not_written(self, tmp_path, caplog):
        obs = _make_obs()
        obs.obs_dir = str(tmp_path)
        section = RecordedSection(obs.created_at - timedelta(minutes=30), "old\n")
        obs.hardware["phd2"].guide_log_section = MagicMock(return_value=section)

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.stop_phd2_guiding"):
            await StopPhd2GuidingPlugin().execute(obs)

        assert "guide_log_file" not in obs.sequencer_data
        assert list(tmp_path.iterdir()) == []
        assert "no guiding section started during this observation" in caplog.text

    async def test_the_receipt_time_decides_not_phd2_time(self, tmp_path):
        # The demo replays a recorded night: PHD2's own start time is in the past
        obs = _make_obs()
        obs.obs_dir = str(tmp_path)
        section = RecordedSection(obs.created_at - timedelta(days=3), "replayed\n",
                                  received_at=obs.created_at + timedelta(minutes=2))
        obs.hardware["phd2"].guide_log_section = MagicMock(return_value=section)

        await StopPhd2GuidingPlugin().execute(obs)

        assert "guide_log_file" in obs.sequencer_data

    async def test_a_write_failure_does_not_fail_the_observation(self, caplog):
        obs = _make_obs()
        obs.obs_dir = "/nonexistent/directory"
        section = RecordedSection(obs.created_at + timedelta(minutes=2), "text\n")
        obs.hardware["phd2"].guide_log_section = MagicMock(return_value=section)

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.stop_phd2_guiding"):
            await StopPhd2GuidingPlugin().execute(obs)

        assert "guide_log_file" not in obs.sequencer_data
        assert "Guide log not written" in caplog.text
        assert "stop_phd2_guiding" in obs.sequencer_data

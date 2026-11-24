"""
Tests for the POINTING_72ED sub-plugin (Pointing72edPlugin), indi backend.
capture_guide_image()/solve_wcs()/world_to_pixel() are all monkeypatched
here -- their own behavior is covered by acquisition_helpers' and
plate_solve_helper's own test files; these tests only cover this
sub-plugin's own orchestration: which arguments it passes to each of them,
in which order, and the pixel-correction math in between. slit_position()
is not patched: the real helper reads the default slit from
acquisition_helpers.SUBPLUGIN_PARAMS, patched below.
Launch: pytest plugins/sequencer_plugins/pointing_72ed/test_pointing_72ed.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from astropy.coordinates import SkyCoord

from rosace.hardware.errors import DeviceCommandError
from rosace.models import Observation, Target
from rosace.plugins.sequencer_plugins import acquisition_helpers
from rosace.plugins.sequencer_plugins.pointing_72ed import pointing_72ed
from rosace.plugins.sequencer_plugins.pointing_72ed.pointing_72ed import Pointing72edPlugin

TARGET_NAME = "* alf Lyr"
QUERIED_NAME = "Vega"
TARGET_RA, TARGET_DEC = 279.23, 38.78
EXPOSURE_SECONDS = 0.3
SAMPLING_ARCSEC = 1.75
SLIT_X, SLIT_Y = 10.0, 12.0

TARGET_COORD = SkyCoord(ra=TARGET_RA, dec=TARGET_DEC, unit="deg", frame="icrs")
# The position slew_72ed aimed the mount at -- deliberately distinct from the target.
CORRECTED_RA, CORRECTED_DEC = TARGET_RA - 0.01, TARGET_DEC - 0.006

LOCK_X, LOCK_Y = 300.0, 250.0
TARGET_PIXEL_X, TARGET_PIXEL_Y = 15.0, 20.0  # distinct from SLIT_X/SLIT_Y, to exercise the residual

IMAGE_NAME = "GUIDING_Vega_0_30s_20260813_120010.fits"


def _make_obs() -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir="/tmp/obsdir",
    )
    obs.scheduler_data["target"] = Target(name=TARGET_NAME, ra=TARGET_RA, dec=TARGET_DEC)
    obs.scheduler_data["queried_name"] = QUERIED_NAME
    obs.sequencer_data["slew_72ed"] = {
        "guide_image": "GUIDING_Vega_0_30s_20260813_120000.fits",
        "corrected_ra_deg": CORRECTED_RA, "corrected_dec_deg": CORRECTED_DEC,
    }
    obs.hardware["guiding_camera"] = AsyncMock()
    obs.hardware["spectroscope"] = AsyncMock()
    obs.hardware["phd2"] = AsyncMock()
    obs.hardware["phd2"].get_lock_position.return_value = (LOCK_X, LOCK_Y)
    return obs


@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch):
    # MagicMock(name=...) does NOT set a real `.name` attribute -- assigning
    # `.name` after construction is what makes it a plain attribute.
    image = MagicMock()
    image.name = IMAGE_NAME
    capture_mock = AsyncMock(return_value=image)
    solve_wcs_mock = AsyncMock()
    world_to_pixel_mock = MagicMock(return_value=(TARGET_PIXEL_X, TARGET_PIXEL_Y))
    monkeypatch.setattr(pointing_72ed, "capture_guide_image", capture_mock)
    monkeypatch.setattr(pointing_72ed, "solve_wcs", solve_wcs_mock)
    monkeypatch.setattr(pointing_72ed, "world_to_pixel", world_to_pixel_mock)
    guiding = {
        "exposure_seconds": EXPOSURE_SECONDS, "sampling_arcsec": SAMPLING_ARCSEC,
        "slit_x": SLIT_X, "slit_y": SLIT_Y,
    }
    monkeypatch.setattr(pointing_72ed, "SUBPLUGIN_PARAMS", {"guiding": guiding})
    monkeypatch.setattr(acquisition_helpers, "SUBPLUGIN_PARAMS", {"guiding": guiding})
    return {
        "capture_guide_image": capture_mock, "solve_wcs": solve_wcs_mock, "world_to_pixel": world_to_pixel_mock,
    }


class TestPointing72edPlugin:

    async def test_raises_on_missing_target(self):
        obs = _make_obs()
        obs.scheduler_data.pop("target")

        with pytest.raises(RuntimeError, match="target"):
            await Pointing72edPlugin().execute(obs)

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs()
        obs.obs_dir = None

        with pytest.raises(RuntimeError, match="obs_dir"):
            await Pointing72edPlugin().execute(obs)

    async def test_raises_if_slew_72ed_did_not_run(self):
        obs = _make_obs()
        obs.sequencer_data.pop("slew_72ed")

        with pytest.raises(RuntimeError, match="slew_72ed"):
            await Pointing72edPlugin().execute(obs)

    async def test_second_start_guiding_is_not_guarded(self):
        """A recovery between set_lock_position() and the second start_guiding()
        would lose PHD2's lock offset, and replaying would guide on the wrong
        position -- so that call keeps the ordinary-error behavior."""
        obs = _make_obs()
        obs.hardware["phd2"].start_guiding.side_effect = [None, DeviceCommandError("boom")]
        obs.hardware["guiding_camera"].expose.side_effect = DeviceCommandError("CAMERA_REMOVED")

        with pytest.raises(DeviceCommandError, match="boom"):
            await Pointing72edPlugin().execute(obs)

        obs.hardware["guiding_camera"].expose.assert_not_awaited()

    async def test_captures_guide_image_at_corrected_position(self, mock_dependencies):
        obs = _make_obs()

        await Pointing72edPlugin().execute(obs)

        calls = mock_dependencies["capture_guide_image"].await_args_list
        assert len(calls) == 1
        passed_obs, exposure = calls[0].args
        assert passed_obs is obs
        assert exposure == EXPOSURE_SECONDS
        assert calls[0].kwargs["ra_deg"] == pytest.approx(CORRECTED_RA)
        assert calls[0].kwargs["dec_deg"] == pytest.approx(CORRECTED_DEC)
        assert calls[0].kwargs["target_name"] == QUERIED_NAME
        assert calls[0].kwargs["image_to_gui"] is True

    async def test_does_not_move_the_mount(self):
        obs = _make_obs()
        obs.hardware["mount"] = AsyncMock()

        await Pointing72edPlugin().execute(obs)

        obs.hardware["mount"].slew_to.assert_not_awaited()

    async def test_solve_does_not_record_pointing_error_and_uses_tight_radius(self, mock_dependencies):
        obs = _make_obs()

        await Pointing72edPlugin().execute(obs)

        call = mock_dependencies["solve_wcs"].await_args_list[0]
        assert call.kwargs["sampling_arcsec"] == SAMPLING_ARCSEC
        assert call.kwargs.get("record_error", False) is False
        assert call.kwargs["radius_deg"] == pointing_72ed.PHASE2_SEARCH_RADIUS_DEG

    async def test_starts_phd2_guiding_twice(self):
        obs = _make_obs()

        await Pointing72edPlugin().execute(obs)

        assert obs.hardware["phd2"].start_guiding.await_count == 2

    async def test_corrects_phd2_lock_position_against_the_default_slit(self):
        obs = _make_obs()

        await Pointing72edPlugin().execute(obs)

        expected_dx = TARGET_PIXEL_X - SLIT_X
        expected_dy = TARGET_PIXEL_Y - SLIT_Y
        obs.hardware["phd2"].set_lock_position.assert_awaited_once_with(
            LOCK_X - expected_dx, LOCK_Y - expected_dy, exact=True
        )

    async def test_corrects_phd2_lock_position_against_the_calculated_slit(self):
        obs = _make_obs()
        obs.sequencer_data["slit_xy"] = {"status": "calculated", "slit_x": SLIT_X, "slit_y": 15.23, "image": "a.fits"}

        await Pointing72edPlugin().execute(obs)

        expected_dx = TARGET_PIXEL_X - SLIT_X
        expected_dy = TARGET_PIXEL_Y - 15.23
        obs.hardware["phd2"].set_lock_position.assert_awaited_once_with(
            LOCK_X - expected_dx, pytest.approx(LOCK_Y - expected_dy), exact=True
        )

    async def test_failed_slit_calculation_falls_back_to_the_default_slit(self):
        obs = _make_obs()
        obs.sequencer_data["slit_xy"] = {"status": "default", "image": None}

        await Pointing72edPlugin().execute(obs)

        obs.hardware["phd2"].set_lock_position.assert_awaited_once_with(
            LOCK_X - (TARGET_PIXEL_X - SLIT_X), LOCK_Y - (TARGET_PIXEL_Y - SLIT_Y), exact=True
        )

    async def test_world_to_pixel_uses_the_real_target_not_the_aim_point(self, mock_dependencies):
        """Regression test for a real bug found 2026-08-12 against a live observation:
        this call must locate the real star (target_coord) in the image to compute the
        residual vs. the slit -- not the corrected position, which is only the artificial
        point the mount was aimed at by slew_72ed's correction slew, and differs from the
        real star by roughly the size of that same correction (a huge, spurious residual
        fed to PHD2 -- empirically ~120px on the real observation that caught this)."""
        obs = _make_obs()

        await Pointing72edPlugin().execute(obs)

        used_coord = mock_dependencies["world_to_pixel"].call_args.args[1]
        assert used_coord.ra.deg == pytest.approx(TARGET_COORD.ra.deg)
        assert used_coord.dec.deg == pytest.approx(TARGET_COORD.dec.deg)
        assert used_coord.ra.deg != pytest.approx(CORRECTED_RA)

    async def test_sets_sequencer_data(self):
        obs = _make_obs()

        result = await Pointing72edPlugin().execute(obs)

        assert result is obs
        assert obs.sequencer_data["pointing_72ed"] == {"guide_image": IMAGE_NAME}

    async def test_records_the_phd2_settling_time(self):
        """guiding_settle_s, for the night's time budget: both settles."""
        import asyncio

        obs = _make_obs()

        async def slow_settle(*args, **kwargs):
            await asyncio.sleep(0.05)

        obs.hardware["phd2"].start_guiding.side_effect = slow_settle

        await Pointing72edPlugin().execute(obs)

        assert 0.09 <= obs.measurements["guiding_settle_s"] < 0.5

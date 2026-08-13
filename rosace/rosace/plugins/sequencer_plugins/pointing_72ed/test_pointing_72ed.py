"""
Tests for the POINTING_72ED sub-plugin (Pointing72edPlugin), indi backend.
capture_guide_image()/solve_wcs()/solve_result()/world_to_pixel() are all
monkeypatched here -- their own behavior is covered by acquisition_helpers'
and plate_solve_helper's own test files; these tests only cover this
sub-plugin's own orchestration: which arguments it passes to each of them,
in which order, and the pointing/pixel-correction math in between.
Launch: pytest plugins/sequencer_plugins/pointing_72ed/test_pointing_72ed.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from astropy.coordinates import SkyCoord

from rosace.models import Observation, Target
from rosace.plugins.sequencer_plugins.pointing_72ed import pointing_72ed
from rosace.plugins.sequencer_plugins.pointing_72ed.pointing_72ed import Pointing72edPlugin

TARGET_NAME = "* alf Lyr"
QUERIED_NAME = "Vega"
TARGET_RA, TARGET_DEC = 279.23, 38.78
BACKLASH_OFFSET_DEG = 2.0
EXPOSURE_SECONDS = 0.3
SAMPLING_ARCSEC = 1.75
SLIT_X, SLIT_Y = 10.0, 12.0

TARGET_COORD = SkyCoord(ra=TARGET_RA, dec=TARGET_DEC, unit="deg", frame="icrs")

# A deliberately non-zero pointing error, so the correction math (and its
# sign) is genuinely exercised, not left at a degenerate (0, 0) case.
REAL_POSITION = SkyCoord(ra=TARGET_RA + 0.01, dec=TARGET_DEC + 0.006, unit="deg", frame="icrs")
_d_lon, _d_lat = TARGET_COORD.spherical_offsets_to(REAL_POSITION)
CORRECTED_COORD = TARGET_COORD.spherical_offsets_by(-_d_lon, -_d_lat)

LOCK_X, LOCK_Y = 300.0, 250.0
TARGET_PIXEL_X, TARGET_PIXEL_Y = 15.0, 20.0  # distinct from SLIT_X/SLIT_Y, to exercise the residual


def _make_obs() -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir="/tmp/obsdir",
    )
    obs.scheduler_data["target"] = Target(name=TARGET_NAME, ra=TARGET_RA, dec=TARGET_DEC)
    obs.scheduler_data["queried_name"] = QUERIED_NAME
    obs.hardware["mount"] = AsyncMock()
    obs.hardware["guiding_camera"] = AsyncMock()
    obs.hardware["phd2"] = AsyncMock()
    obs.hardware["phd2"].get_lock_position.return_value = (LOCK_X, LOCK_Y)
    obs.hardware_config["mount"] = {"backlash_offset_deg": BACKLASH_OFFSET_DEG}
    obs.hardware_config["guiding_camera"] = {
        "exposure_seconds": EXPOSURE_SECONDS, "sampling_arcsec": SAMPLING_ARCSEC,
        "slit_x": SLIT_X, "slit_y": SLIT_Y,
    }
    return obs


@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch):
    capture_mock = AsyncMock(side_effect=[MagicMock(name="initial_image"), MagicMock(name="corrected_image")])
    solve_wcs_mock = AsyncMock()
    solve_result_mock = MagicMock(return_value=REAL_POSITION)
    world_to_pixel_mock = MagicMock(return_value=(TARGET_PIXEL_X, TARGET_PIXEL_Y))
    monkeypatch.setattr(pointing_72ed, "capture_guide_image", capture_mock)
    monkeypatch.setattr(pointing_72ed, "solve_wcs", solve_wcs_mock)
    monkeypatch.setattr(pointing_72ed, "solve_result", solve_result_mock)
    monkeypatch.setattr(pointing_72ed, "world_to_pixel", world_to_pixel_mock)
    return {
        "capture_guide_image": capture_mock, "solve_wcs": solve_wcs_mock,
        "solve_result": solve_result_mock, "world_to_pixel": world_to_pixel_mock,
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

    async def test_stops_phd2_capture_first(self):
        obs = _make_obs()

        await Pointing72edPlugin().execute(obs)

        obs.hardware["phd2"].stop_capture.assert_awaited_once_with()

    async def test_two_step_slew_toward_target_then_corrected_position(self):
        obs = _make_obs()

        await Pointing72edPlugin().execute(obs)

        calls = obs.hardware["mount"].slew_to.await_args_list
        assert len(calls) == 4
        # Phase 1: approach offset, then the exact target
        assert calls[0].args == pytest.approx((TARGET_RA + BACKLASH_OFFSET_DEG, TARGET_DEC + BACKLASH_OFFSET_DEG))
        assert calls[1].args == pytest.approx((TARGET_RA, TARGET_DEC))
        # Phase 1 correction: approach offset, then the exact corrected position
        assert calls[2].args == pytest.approx(
            (CORRECTED_COORD.ra.deg + BACKLASH_OFFSET_DEG, CORRECTED_COORD.dec.deg + BACKLASH_OFFSET_DEG)
        )
        assert calls[3].args == pytest.approx((CORRECTED_COORD.ra.deg, CORRECTED_COORD.dec.deg))
        # Sanity: the correction actually moved the target (a real, non-degenerate case)
        assert calls[3].args != pytest.approx((TARGET_RA, TARGET_DEC))

    async def test_captures_two_guide_images_at_target_then_corrected_position(self, mock_dependencies):
        obs = _make_obs()

        await Pointing72edPlugin().execute(obs)

        calls = mock_dependencies["capture_guide_image"].await_args_list
        assert len(calls) == 2
        camera, exposure, obs_dir = calls[0].args
        assert camera is obs.hardware["guiding_camera"]
        assert exposure == EXPOSURE_SECONDS
        assert obs_dir == obs.obs_dir
        assert calls[0].kwargs["ra_deg"] == pytest.approx(TARGET_RA)
        assert calls[0].kwargs["dec_deg"] == pytest.approx(TARGET_DEC)
        assert calls[0].kwargs["target_name"] == QUERIED_NAME
        assert calls[1].kwargs["ra_deg"] == pytest.approx(CORRECTED_COORD.ra.deg)
        assert calls[1].kwargs["dec_deg"] == pytest.approx(CORRECTED_COORD.dec.deg)
        assert calls[1].kwargs["target_name"] == QUERIED_NAME

    async def test_first_solve_records_pointing_error(self, mock_dependencies):
        obs = _make_obs()

        await Pointing72edPlugin().execute(obs)

        first_call = mock_dependencies["solve_wcs"].await_args_list[0]
        assert first_call.kwargs["sampling_arcsec"] == SAMPLING_ARCSEC
        assert first_call.kwargs["record_error"] is True
        assert first_call.kwargs["observation_id"] == obs.id

    async def test_second_solve_does_not_record_pointing_error(self, mock_dependencies):
        obs = _make_obs()

        await Pointing72edPlugin().execute(obs)

        second_call = mock_dependencies["solve_wcs"].await_args_list[1]
        assert second_call.kwargs["sampling_arcsec"] == SAMPLING_ARCSEC
        assert second_call.kwargs.get("record_error", False) is False

    async def test_starts_phd2_guiding_twice(self):
        obs = _make_obs()

        await Pointing72edPlugin().execute(obs)

        assert obs.hardware["phd2"].start_guiding.await_count == 2

    async def test_corrects_phd2_lock_position_by_the_residual_pixel_offset(self):
        obs = _make_obs()

        await Pointing72edPlugin().execute(obs)

        expected_dx = TARGET_PIXEL_X - SLIT_X
        expected_dy = TARGET_PIXEL_Y - SLIT_Y
        obs.hardware["phd2"].set_lock_position.assert_awaited_once_with(
            LOCK_X - expected_dx, LOCK_Y - expected_dy, exact=True
        )

    async def test_second_world_to_pixel_call_uses_the_real_target_not_the_aim_point(self, mock_dependencies):
        """Regression test for a real bug found 2026-08-12 against a live observation:
        this call must locate the real star (target_coord) in the corrected image to
        compute the residual vs. the slit -- not corrected_coord, which is only the
        artificial point the mount was aimed at during Phase 1's correction slew, and
        differs from the real star by roughly the size of that same correction (a huge,
        spurious residual fed to PHD2 -- empirically ~120px on the real observation that
        caught this)."""
        obs = _make_obs()

        await Pointing72edPlugin().execute(obs)

        call_args = mock_dependencies["world_to_pixel"].call_args
        used_coord = call_args.args[1]
        assert used_coord.ra.deg == pytest.approx(TARGET_COORD.ra.deg)
        assert used_coord.dec.deg == pytest.approx(TARGET_COORD.dec.deg)
        # Sanity: TARGET_COORD and CORRECTED_COORD are genuinely different in this test
        # fixture -- a test that passed with either coordinate wouldn't have caught the bug.
        assert used_coord.ra.deg != pytest.approx(CORRECTED_COORD.ra.deg)

    async def test_sets_sequencer_data(self):
        obs = _make_obs()

        result = await Pointing72edPlugin().execute(obs)

        assert result is obs
        data = obs.sequencer_data["pointing_72ed"]
        assert "initial_guide_image" in data
        assert "corrected_guide_image" in data

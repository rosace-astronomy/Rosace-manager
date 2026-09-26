"""
Tests for the SLEW_72ED sub-plugin (Slew72edPlugin), indi backend.
capture_guide_image()/solve_wcs()/solve_result() are all monkeypatched
here -- their own behavior is covered by acquisition_helpers' and
plate_solve_helper's own test files; these tests only cover this
sub-plugin's own orchestration: which arguments it passes to each of them,
in which order, and the pointing-correction math in between.
Launch: pytest plugins/sequencer_plugins/slew_72ed/test_slew_72ed.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from astropy.coordinates import SkyCoord

from rosace.errors import SessionFatalError
from rosace.hardware.errors import DeviceCommandError
from rosace.models import Observation, Target
from rosace.plugins.sequencer_plugins import guiding_chain_recovery
from rosace.plugins.sequencer_plugins.slew_72ed import slew_72ed
from rosace.plugins.sequencer_plugins.slew_72ed.slew_72ed import Slew72edPlugin

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

IMAGE_NAME = "GUIDING_Vega_0_30s_20260813_120000.fits"


def _make_obs() -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir="/tmp/obsdir",
    )
    obs.scheduler_data["target"] = Target(name=TARGET_NAME, ra=TARGET_RA, dec=TARGET_DEC)
    obs.scheduler_data["queried_name"] = QUERIED_NAME
    obs.hardware["mount"] = AsyncMock()
    obs.hardware["mount"].get_position.return_value = (TARGET_RA, TARGET_DEC)
    obs.hardware["guiding_camera"] = AsyncMock()
    obs.hardware["spectroscope"] = AsyncMock()
    obs.hardware["phd2"] = AsyncMock()
    obs.hardware_config["mount"] = {"backlash_offset_deg": BACKLASH_OFFSET_DEG}
    return obs


@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch):
    # MagicMock(name=...) does NOT set a real `.name` attribute -- name= is
    # special-cased by the constructor for the mock's own repr. Assigning
    # `.name` after construction is what actually makes it a plain attribute,
    # needed here since production code reads guide_image.name.
    image = MagicMock()
    image.name = IMAGE_NAME
    capture_mock = AsyncMock(return_value=image)
    solve_wcs_mock = AsyncMock()
    solve_result_mock = MagicMock(return_value=REAL_POSITION)
    monkeypatch.setattr(slew_72ed, "capture_guide_image", capture_mock)
    monkeypatch.setattr(slew_72ed, "solve_wcs", solve_wcs_mock)
    monkeypatch.setattr(slew_72ed, "solve_result", solve_result_mock)
    monkeypatch.setattr(slew_72ed, "SUBPLUGIN_PARAMS", {
        "guiding": {
            "exposure_seconds": EXPOSURE_SECONDS, "sampling_arcsec": SAMPLING_ARCSEC,
            "slit_x": SLIT_X, "slit_y": SLIT_Y,
        },
    })
    return {
        "capture_guide_image": capture_mock, "solve_wcs": solve_wcs_mock, "solve_result": solve_result_mock,
    }


class TestSlew72edPlugin:

    async def test_raises_on_missing_target(self):
        obs = _make_obs()
        obs.scheduler_data.pop("target")

        with pytest.raises(RuntimeError, match="target"):
            await Slew72edPlugin().execute(obs)

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs()
        obs.obs_dir = None

        with pytest.raises(RuntimeError, match="obs_dir"):
            await Slew72edPlugin().execute(obs)

    async def test_stops_phd2_capture_first(self):
        obs = _make_obs()

        await Slew72edPlugin().execute(obs)

        obs.hardware["phd2"].stop_capture.assert_awaited_once_with()

    async def test_dead_guiding_camera_at_first_phd2_call_stops_the_session(self, monkeypatch):
        """The 2026-09-24 night: PHD2's stop_capture failed because the guiding
        camera was gone. The guiding-chain recovery finds it dead and cannot
        revive it -> SessionFatalError, before anything moves."""
        monkeypatch.setattr(guiding_chain_recovery, "PAUSE_BETWEEN_ATTEMPTS_SECONDS", 0.0)
        obs = _make_obs()
        obs.hardware["phd2"].stop_capture.side_effect = DeviceCommandError("stuck")
        obs.hardware["guiding_camera"].expose.side_effect = DeviceCommandError("CAMERA_REMOVED")

        with pytest.raises(SessionFatalError):
            await Slew72edPlugin().execute(obs)

        obs.hardware["mount"].slew_to.assert_not_awaited()

    async def test_phd2_failure_with_a_healthy_camera_stays_an_ordinary_error(self):
        obs = _make_obs()
        obs.hardware["phd2"].stop_capture.side_effect = DeviceCommandError("stuck")

        with pytest.raises(DeviceCommandError, match="stuck"):
            await Slew72edPlugin().execute(obs)

        obs.hardware["guiding_camera"].disconnect.assert_not_awaited()

    async def test_does_not_start_phd2_guiding(self):
        """Guiding starts in pointing_72ed, after slit_xy -- a lamp-lit slit
        image must never be taken while PHD2 is guiding."""
        obs = _make_obs()

        await Slew72edPlugin().execute(obs)

        obs.hardware["phd2"].start_guiding.assert_not_awaited()

    async def test_reads_back_mount_position_after_each_slew(self):
        """DEBUG-only diagnostic added 2026-08-30 -- only checks the mount is
        actually queried after both slews, not the log content itself."""
        obs = _make_obs()

        await Slew72edPlugin().execute(obs)

        assert obs.hardware["mount"].get_position.await_count == 2

    async def test_records_pier_side_with_the_pointing_error(self, mock_dependencies):
        obs = _make_obs()
        obs.hardware["mount"].get_pier_side.return_value = "west"

        await Slew72edPlugin().execute(obs)

        recorded = [c for c in mock_dependencies["solve_wcs"].await_args_list if c.kwargs.get("record_error")]
        assert len(recorded) == 1
        assert recorded[0].kwargs["pier_side"] == "west"

    async def test_unreadable_pier_side_is_recorded_as_unknown(self, mock_dependencies):
        obs = _make_obs()
        obs.hardware["mount"].get_pier_side.side_effect = DeviceCommandError("no pier side")

        await Slew72edPlugin().execute(obs)

        recorded = [c for c in mock_dependencies["solve_wcs"].await_args_list if c.kwargs.get("record_error")]
        assert recorded[0].kwargs["pier_side"] is None

    async def test_two_step_slew_toward_target_then_corrected_position(self):
        obs = _make_obs()

        await Slew72edPlugin().execute(obs)

        calls = obs.hardware["mount"].slew_to.await_args_list
        assert len(calls) == 4
        # Approach offset, then the exact target
        assert calls[0].args == pytest.approx((TARGET_RA + BACKLASH_OFFSET_DEG, TARGET_DEC + BACKLASH_OFFSET_DEG))
        assert calls[1].args == pytest.approx((TARGET_RA, TARGET_DEC))
        # Correction: approach offset, then the exact corrected position
        assert calls[2].args == pytest.approx(
            (CORRECTED_COORD.ra.deg + BACKLASH_OFFSET_DEG, CORRECTED_COORD.dec.deg + BACKLASH_OFFSET_DEG)
        )
        assert calls[3].args == pytest.approx((CORRECTED_COORD.ra.deg, CORRECTED_COORD.dec.deg))
        # Sanity: the correction actually moved the target (a real, non-degenerate case)
        assert calls[3].args != pytest.approx((TARGET_RA, TARGET_DEC))

    async def test_captures_one_guide_image_at_target(self, mock_dependencies):
        obs = _make_obs()

        await Slew72edPlugin().execute(obs)

        calls = mock_dependencies["capture_guide_image"].await_args_list
        assert len(calls) == 1
        passed_obs, exposure = calls[0].args
        assert passed_obs is obs
        assert exposure == EXPOSURE_SECONDS
        assert calls[0].kwargs["ra_deg"] == pytest.approx(TARGET_RA)
        assert calls[0].kwargs["dec_deg"] == pytest.approx(TARGET_DEC)
        assert calls[0].kwargs["target_name"] == QUERIED_NAME
        assert calls[0].kwargs["image_to_gui"] is True

    async def test_solve_records_pointing_error(self, mock_dependencies):
        obs = _make_obs()

        await Slew72edPlugin().execute(obs)

        call = mock_dependencies["solve_wcs"].await_args_list[0]
        assert call.kwargs["sampling_arcsec"] == SAMPLING_ARCSEC
        assert call.kwargs["record_error"] is True
        assert call.kwargs["observation_id"] == obs.id
        # No precise position hint yet -- keeps solve_wcs()'s own wider default.
        assert "radius_deg" not in call.kwargs

    async def test_reads_sky_position_at_the_default_slit(self, mock_dependencies):
        obs = _make_obs()
        # Even a calculated slit position is ignored here: slit_xy runs after this step.
        obs.sequencer_data["slit_xy"] = {"status": "calculated", "slit_x": 99.0, "slit_y": 99.0, "image": "x"}

        await Slew72edPlugin().execute(obs)

        _, x, y = mock_dependencies["solve_result"].call_args.args
        assert (x, y) == (SLIT_X, SLIT_Y)

    async def test_sets_sequencer_data_with_corrected_position(self):
        obs = _make_obs()

        result = await Slew72edPlugin().execute(obs)

        assert result is obs
        data = obs.sequencer_data["slew_72ed"]
        assert data["guide_image"] == IMAGE_NAME
        assert data["corrected_ra_deg"] == pytest.approx(CORRECTED_COORD.ra.deg)
        assert data["corrected_dec_deg"] == pytest.approx(CORRECTED_COORD.dec.deg)

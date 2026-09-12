"""
Tests for the HOME_SYNC_AM3 sub-plugin (HomeSyncAm3Plugin), indi backend.
Covers the once-per-session gating (keyed on session_id), the
park/unpark/measured-sync retry loop (random azimuth per attempt, up to
MAX_HOMING_ATTEMPTS), and the park/unpark-only fallback when every attempt
fails or the sync is disabled.
Run: pytest plugins/sequencer_plugins/home_sync_am3/test_home_sync_am3.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from astropy.coordinates import SkyCoord

from rosace.astro_math import equatorial_from_horizontal_deg
from rosace.config import SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG
from rosace.models import Observation
from rosace.plugins.sequencer_plugins.home_sync_am3 import home_sync_am3 as hsa_mod
from rosace.plugins.sequencer_plugins.home_sync_am3.home_sync_am3 import HomeSyncAm3Plugin
from rosace.plugins.sequencer_plugins.plate_solve_helper import PlateSolveError


@pytest.fixture(autouse=True)
def reset_module_state():
    """The 'already homed this session' flag is module-level, shared across
    tests running in the same process -- reset it before and after every
    test so tests don't leak state into each other."""
    hsa_mod._last_homed_session_id = None
    yield
    hsa_mod._last_homed_session_id = None


MEASURED = SkyCoord(ra=123.456, dec=45.678, unit="deg", frame="icrs")


def _make_mount() -> AsyncMock:
    return AsyncMock()


def _make_obs(session_id, mount=None):
    obs = Observation(
        id="obs1", created_at=datetime.now(timezone.utc), session_id=session_id, obs_dir="/tmp/session_dir",
    )
    obs.hardware["mount"] = mount if mount is not None else _make_mount()
    return obs


def _patch_successful_capture_and_solve(monkeypatch, measured=MEASURED):
    """Patches capture_guide_image/solve_wcs/solve_result so every homing
    attempt succeeds -- solve_result always returns `measured`."""
    capture = AsyncMock(return_value=Path("GUIDING_HOMESYNC_10s_fake.fits"))
    solve_wcs = AsyncMock()
    solve_result = MagicMock(return_value=measured)
    monkeypatch.setattr(hsa_mod, "capture_guide_image", capture)
    monkeypatch.setattr(hsa_mod, "solve_wcs", solve_wcs)
    monkeypatch.setattr(hsa_mod, "solve_result", solve_result)
    return capture, solve_wcs, solve_result


class TestHomeSyncAm3PluginGating:

    async def test_runs_park_and_unpark_on_first_call_for_a_session(self, monkeypatch):
        _patch_successful_capture_and_solve(monkeypatch)
        mount = _make_mount()
        obs = _make_obs("session-a", mount=mount)

        result = await HomeSyncAm3Plugin().execute(obs)

        mount.park.assert_awaited_once()
        mount.unpark.assert_awaited_once()
        assert result is obs

    async def test_skips_on_second_call_for_the_same_session(self, monkeypatch):
        _patch_successful_capture_and_solve(monkeypatch)
        mount = _make_mount()
        await HomeSyncAm3Plugin().execute(_make_obs("session-a", mount=mount))

        mount2 = _make_mount()
        obs2 = _make_obs("session-a", mount=mount2)
        await HomeSyncAm3Plugin().execute(obs2)

        mount2.park.assert_not_awaited()
        mount2.unpark.assert_not_awaited()
        mount2.sync_to_coordinates.assert_not_awaited()

    async def test_runs_again_for_a_new_session(self, monkeypatch):
        _patch_successful_capture_and_solve(monkeypatch)
        mount1 = _make_mount()
        await HomeSyncAm3Plugin().execute(_make_obs("session-a", mount=mount1))

        mount2 = _make_mount()
        obs2 = _make_obs("session-b", mount=mount2)
        await HomeSyncAm3Plugin().execute(obs2)

        mount2.park.assert_awaited_once()
        mount2.unpark.assert_awaited_once()
        mount2.sync_to_coordinates.assert_awaited_once()


class TestMeasuredSyncHoming:

    async def test_syncs_to_measured_position_on_first_successful_attempt(self, monkeypatch):
        monkeypatch.setattr(hsa_mod.random, "uniform", MagicMock(return_value=90.0))
        _patch_successful_capture_and_solve(monkeypatch)
        mount = _make_mount()
        obs = _make_obs("session-a", mount=mount)

        await HomeSyncAm3Plugin().execute(obs)

        mount.sync_to_coordinates.assert_awaited_once_with(MEASURED.ra.deg, MEASURED.dec.deg)
        casier = obs.sequencer_data["home_sync_am3"]
        assert casier == {
            "method": "measured_sync",
            "attempts": 1,
            "guide_image": "GUIDING_HOMESYNC_10s_fake.fits",
            "synced_ra": MEASURED.ra.deg,
            "synced_dec": MEASURED.dec.deg,
        }

    async def test_azimuth_is_drawn_within_the_configured_east_band(self, monkeypatch):
        uniform_mock = MagicMock(return_value=90.0)
        monkeypatch.setattr(hsa_mod.random, "uniform", uniform_mock)
        _patch_successful_capture_and_solve(monkeypatch)
        obs = _make_obs("session-a")

        await HomeSyncAm3Plugin().execute(obs)

        uniform_mock.assert_called_once_with(hsa_mod.HOMING_AZIMUTH_MIN_DEG, hsa_mod.HOMING_AZIMUTH_MAX_DEG)

    async def test_slews_to_the_fixed_altitude_converted_from_azimuth(self, monkeypatch):
        monkeypatch.setattr(hsa_mod.random, "uniform", MagicMock(return_value=42.0))
        _patch_successful_capture_and_solve(monkeypatch)
        mount = _make_mount()
        obs = _make_obs("session-a", mount=mount)
        expected_ra, expected_dec = equatorial_from_horizontal_deg(
            hsa_mod.HOMING_ALTITUDE_DEG, 42.0, SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG,
        )

        await HomeSyncAm3Plugin().execute(obs)

        mount.slew_to.assert_awaited_once()
        called_ra, called_dec = mount.slew_to.call_args[0]
        assert called_ra == pytest.approx(expected_ra, abs=1e-3)
        assert called_dec == pytest.approx(expected_dec, abs=1e-3)

    async def test_retries_with_a_new_random_point_after_a_plate_solve_failure(self, monkeypatch):
        monkeypatch.setattr(hsa_mod.random, "uniform", MagicMock(side_effect=[10.0, 20.0]))
        capture, solve_wcs, solve_result = _patch_successful_capture_and_solve(monkeypatch)
        solve_wcs.side_effect = [PlateSolveError("no match"), None]
        mount = _make_mount()
        obs = _make_obs("session-a", mount=mount)

        await HomeSyncAm3Plugin().execute(obs)

        assert mount.slew_to.await_count == 2
        mount.sync_to_coordinates.assert_awaited_once_with(MEASURED.ra.deg, MEASURED.dec.deg)
        assert obs.sequencer_data["home_sync_am3"]["attempts"] == 2

    async def test_retries_with_a_new_random_point_after_a_slew_failure(self, monkeypatch):
        monkeypatch.setattr(hsa_mod.random, "uniform", MagicMock(side_effect=[10.0, 20.0]))
        _patch_successful_capture_and_solve(monkeypatch)
        mount = _make_mount()
        mount.slew_to.side_effect = [RuntimeError("mount never reached target"), None]
        obs = _make_obs("session-a", mount=mount)

        await HomeSyncAm3Plugin().execute(obs)

        assert mount.slew_to.await_count == 2
        mount.sync_to_coordinates.assert_awaited_once_with(MEASURED.ra.deg, MEASURED.dec.deg)
        assert obs.sequencer_data["home_sync_am3"]["attempts"] == 2

    async def test_gives_up_after_max_attempts_and_falls_back_to_park_unpark_only(self, monkeypatch):
        monkeypatch.setattr(hsa_mod.random, "uniform", MagicMock(return_value=90.0))
        capture, solve_wcs, solve_result = _patch_successful_capture_and_solve(monkeypatch)
        solve_wcs.side_effect = PlateSolveError("no match")
        mount = _make_mount()
        obs = _make_obs("session-a", mount=mount)

        await HomeSyncAm3Plugin().execute(obs)

        assert mount.slew_to.await_count == hsa_mod.MAX_HOMING_ATTEMPTS
        mount.sync_to_coordinates.assert_not_awaited()
        assert obs.sequencer_data["home_sync_am3"] == {
            "method": "park_unpark_only",
            "attempts": hsa_mod.MAX_HOMING_ATTEMPTS,
            "guide_image": None,
            "synced_ra": None,
            "synced_dec": None,
        }

    async def test_stop_check_interrupts_between_attempts(self, monkeypatch):
        monkeypatch.setattr(hsa_mod.random, "uniform", MagicMock(return_value=90.0))
        capture, solve_wcs, solve_result = _patch_successful_capture_and_solve(monkeypatch)
        solve_wcs.side_effect = PlateSolveError("no match")
        mount = _make_mount()
        obs = _make_obs("session-a", mount=mount)
        calls = {"n": 0}

        def stop_after_two():
            calls["n"] += 1
            return calls["n"] > 2

        obs.stop_check = stop_after_two

        await HomeSyncAm3Plugin().execute(obs)

        assert mount.slew_to.await_count == 2
        mount.sync_to_coordinates.assert_not_awaited()
        assert "home_sync_am3" not in obs.sequencer_data

    async def test_raises_if_obs_dir_missing(self):
        mount = _make_mount()
        obs = _make_obs("session-a", mount=mount)
        obs.obs_dir = None

        with pytest.raises(RuntimeError, match="obs_dir"):
            await HomeSyncAm3Plugin().execute(obs)

    async def test_sync_disabled_skips_homing_attempts_entirely(self, monkeypatch):
        monkeypatch.setattr(hsa_mod, "SYNC_HOME_VIA_PLATE_SOLVE", False)
        mount = _make_mount()
        obs = _make_obs("session-a", mount=mount)

        result = await HomeSyncAm3Plugin().execute(obs)

        mount.park.assert_awaited_once()
        mount.unpark.assert_awaited_once()
        mount.slew_to.assert_not_awaited()
        mount.sync_to_coordinates.assert_not_awaited()
        assert "home_sync_am3" not in obs.sequencer_data
        assert result is obs

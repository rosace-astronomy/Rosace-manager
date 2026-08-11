"""
Tests du sous-plugin POINTING_72ED (Pointing72edPlugin), backend indi.
Vérifie l'enchaînement mount.slew_to() -> capture_guide_image() sur la
guiding_camera — mount et caméra sont des mocks, pas de matériel réel.
Lancement : pytest plugins/sequencer_plugins/pointing_72ed/test_pointing_72ed.py -v
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from astropy.io import fits

from rosace.config import GUIDE_EXPOSURE_TIME_SECONDS
from rosace.models import Observation, Target
from rosace.plugins.sequencer_plugins import acquisition_helpers
from rosace.plugins.sequencer_plugins.pointing_72ed.pointing_72ed import Pointing72edPlugin

FIXED_TS = "20260807_143022"


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 7, 14, 30, 22, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def freeze_fits_timestamp(monkeypatch):
    """build_fits_root_name() stamps every FITS filename with the current
    UTC time — freeze it so tests can assert on exact filenames."""
    monkeypatch.setattr(acquisition_helpers, "datetime", _FrozenDatetime)


MOUNT_POSITION = (279.30, 38.81)  # deliberately distinct from the target's own ra/dec below

# target.name is deliberately the Simbad canonical form, queried_name the
# observer-typed one -- distinct on purpose, to catch a regression where
# OBJECT gets stamped with the wrong one of the two (a real bug found in
# review: the canonical name was showing up in FITS headers instead of the
# name the observer actually typed in the observation list).
CANONICAL_NAME = "* bet Lyr"
QUERIED_NAME = "Vega"


def _make_obs(obs_dir=None, target=None, queried_name=None, guiding_camera=None) -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir) if obs_dir else None,
    )
    if target is not None:
        obs.scheduler_data["target"] = target
        obs.scheduler_data["queried_name"] = queried_name or target.name
    obs.hardware["mount"] = AsyncMock()
    obs.hardware["mount"].get_position.return_value = MOUNT_POSITION
    obs.hardware["guiding_camera"] = guiding_camera if guiding_camera is not None else AsyncMock()
    obs.hardware["phd2"] = AsyncMock()
    return obs


class TestPointing72edPlugin:

    async def test_raises_on_missing_target(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, target=None)

        with pytest.raises(RuntimeError, match="target"):
            await Pointing72edPlugin().execute(obs)

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None, target=Target(name="Vega", ra=279.23, dec=38.78))

        with pytest.raises(RuntimeError, match="obs_dir"):
            await Pointing72edPlugin().execute(obs)

    async def test_calls_mount_slew_to_with_target_coordinates(self, tmp_path):
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(obs_dir=tmp_path, target=Target(name="Vega", ra=279.23, dec=38.78), guiding_camera=camera)

        await Pointing72edPlugin().execute(obs)

        obs.hardware["mount"].slew_to.assert_awaited_once_with(279.23, 38.78)

    async def test_starts_phd2_guiding(self, tmp_path):
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(obs_dir=tmp_path, target=Target(name="Vega", ra=279.23, dec=38.78), guiding_camera=camera)

        await Pointing72edPlugin().execute(obs)

        obs.hardware["phd2"].start_guiding.assert_awaited_once_with()

    async def test_captures_guide_image_with_configured_exposure_time(self, tmp_path):
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()
        obs = _make_obs(obs_dir=tmp_path, target=Target(name="Vega", ra=279.23, dec=38.78), guiding_camera=camera)

        await Pointing72edPlugin().execute(obs)

        camera.expose.assert_awaited_once_with(GUIDE_EXPOSURE_TIME_SECONDS)

    async def test_stamps_mount_position_and_queried_name_into_guide_image_header(self, tmp_path):
        fake_image = MagicMock()
        fake_image.__getitem__.return_value.header = fits.Header()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        obs = _make_obs(
            obs_dir=tmp_path, target=Target(name=CANONICAL_NAME, ra=279.23, dec=38.78),
            queried_name=QUERIED_NAME, guiding_camera=camera,
        )

        await Pointing72edPlugin().execute(obs)

        header = fake_image[0].header
        assert header["RA"] == MOUNT_POSITION[0]
        assert header["DEC"] == MOUNT_POSITION[1]
        assert header["EQUINOX"] == 2000
        # OBJECT must be the observer-typed name, not the Simbad canonical form.
        assert header["OBJECT"] == QUERIED_NAME
        assert "Rosace" in header.comments["RA"]
        assert "Rosace" in header.comments["OBJECT"]

    async def test_sets_sequencer_data(self, tmp_path):
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image
        obs = _make_obs(obs_dir=tmp_path, target=Target(name="Vega", ra=279.23, dec=38.78), guiding_camera=camera)

        result = await Pointing72edPlugin().execute(obs)

        expected_path = str(tmp_path / f"GUIDING_Vega_0_30s_{FIXED_TS}.fits")
        assert result is obs
        assert obs.sequencer_data["pointing_72ed"] == {
            "target": "Vega",
            "guide_image": expected_path,
            "guiding_started": True,
        }

"""
Tests for the STAR_SPECTRA_ACQUISITION sub-plugin (StarSpectraAcquisitionPlugin), internal backend.
Run: pytest plugins/sequencer_plugins/star_spectra_acquisition/test_star_spectra_acquisition_internal.py -v
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation, Target, ExposureConfig
from rosace.plugins.sequencer_plugins.star_spectra_acquisition.star_spectra_acquisition_internal import (
    StarSpectraAcquisitionPlugin,
)


def _make_obs(obs_dir=None, exposure=None, target=None) -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234",
        obs_dir=str(obs_dir) if obs_dir else None,
    )
    if exposure is not None:
        obs.scheduler_data["exposure"] = exposure
    if target is not None:
        obs.scheduler_data["target"] = target
    return obs


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr(
        "rosace.plugins.sequencer_plugins.star_spectra_acquisition.star_spectra_acquisition_internal.asyncio.sleep",
        AsyncMock(return_value=None),
    )


class TestStarSpectraAcquisitionPlugin:

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None, exposure=ExposureConfig(exposure_time=1.0))
        with pytest.raises(RuntimeError, match="obs_dir"):
            await StarSpectraAcquisitionPlugin().execute(obs)

    async def test_raises_if_exposure_missing(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, exposure=None)
        with pytest.raises(RuntimeError, match="exposure"):
            await StarSpectraAcquisitionPlugin().execute(obs)

    async def test_produces_expected_number_of_files(self, tmp_path):
        exposure = ExposureConfig(exposure_time=1.0, num_exposures=3)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, target=Target(name="Vega", ra=1.0, dec=1.0))

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        raw_files = result.sequencer_data["star_spectra_images"]
        assert len(raw_files) == 3
        for f in raw_files:
            assert Path(f).is_file()  # really created on disk (simulate mode)

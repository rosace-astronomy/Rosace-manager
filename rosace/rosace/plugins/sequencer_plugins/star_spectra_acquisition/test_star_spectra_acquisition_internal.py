"""
Tests for the STAR_SPECTRA_ACQUISITION sub-plugin (StarSpectraAcquisitionPlugin), internal backend.
Run: pytest plugins/sequencer_plugins/star_spectra_acquisition/test_star_spectra_acquisition_internal.py -v
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rosace.models import Observation, Target, ExposureConfig
from rosace.plugins.sequencer_plugins import acquisition_helpers
from rosace.plugins.sequencer_plugins.star_spectra_acquisition import star_spectra_acquisition_internal as ssa_mod
from rosace.plugins.sequencer_plugins.star_spectra_acquisition.star_spectra_acquisition_internal import (
    StarSpectraAcquisitionPlugin,
)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 7, 14, 30, 22, tzinfo=timezone.utc)


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

    async def test_produces_one_file_for_a_short_explicit_exposure_time(self, tmp_path):
        # exposure.num_exposures is not honored (same deferred limitation as the indi
        # backend) -- count comes from compute_exposure_time_and_count instead.
        exposure = ExposureConfig(exposure_time=1.0, num_exposures=3)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, target=Target(name="Vega", ra=1.0, dec=1.0))

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        raw_files = result.sequencer_data["star_spectra_images"]
        assert len(raw_files) == 1
        for f in raw_files:
            assert "/" not in f  # basename only, not a full path
            assert (Path(tmp_path) / f).is_file()  # really created on disk (simulate mode)

    async def test_raises_if_exposure_time_none_and_no_magnitude(self, tmp_path):
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=None)
        with pytest.raises(RuntimeError, match="magnitude"):
            await StarSpectraAcquisitionPlugin().execute(obs)

    async def test_raises_if_exposure_time_none_and_target_magnitude_none(self, tmp_path):
        target = Target(name="NoMagStar", ra=1.0, dec=1.0, magnitude_v=None)
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=target)
        with pytest.raises(RuntimeError, match="magnitude"):
            await StarSpectraAcquisitionPlugin().execute(obs)

    async def test_computes_exposure_time_from_magnitude_when_not_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssa_mod, "EXPOSURE_TIME_MAG0_SECONDS", 1.0)
        monkeypatch.setattr(ssa_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
        target = Target(name="MagStar", ra=1.0, dec=1.0, magnitude_v=2.5)  # -> 10s, count=1
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=target)

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        assert len(result.sequencer_data["star_spectra_images"]) == 1

    async def test_splits_into_several_exposures_when_computed_time_exceeds_max(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssa_mod, "EXPOSURE_TIME_MAG0_SECONDS", 1.0)
        monkeypatch.setattr(ssa_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
        target = Target(name="FaintStar", ra=1.0, dec=1.0, magnitude_v=7.5)  # raw 1000s -> 4x300s
        obs = _make_obs(obs_dir=tmp_path, exposure=ExposureConfig(exposure_time=None), target=target)

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        assert len(result.sequencer_data["star_spectra_images"]) == 4

    async def test_splits_explicit_exposure_time_when_it_exceeds_max(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssa_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
        exposure = ExposureConfig(exposure_time=780)  # -> ceil(780/300) = 3x300s
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, target=Target(name="Vega", ra=1.0, dec=1.0))

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        assert len(result.sequencer_data["star_spectra_images"]) == 3

    async def test_filename_follows_the_shared_fits_naming_convention(self, tmp_path, monkeypatch):
        """Regression test: this sub-plugin used to hardcode "spectrum_0001.fits"
        instead of build_fits_root_name() (the same convention the indi backend's
        star_spectra_acquisition.py already uses) -- fixed so demo-mode files look
        like real ones, not just a generic placeholder name. Timestamp frozen (same
        pattern as test_acquisition_helpers.py) since build_fits_root_name() stamps
        the current time and this test calls it a second time to build the expectation."""
        monkeypatch.setattr(acquisition_helpers, "datetime", _FrozenDatetime)
        exposure = ExposureConfig(exposure_time=1.0)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, target=Target(name="Vega", ra=1.0, dec=1.0))
        obs.scheduler_data["queried_name"] = "T CrB"

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        [filename] = result.sequencer_data["star_spectra_images"]
        assert filename == ssa_mod.build_fits_root_name(
            "STAR", target_name="T CrB", exposure_time=1.0, num_exposures=1
        ) + ".fits"

    async def test_series_of_several_files_share_one_root_name_with_index_suffix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acquisition_helpers, "datetime", _FrozenDatetime)
        monkeypatch.setattr(ssa_mod, "MAX_EXPOSURE_TIME_SECONDS", 300)
        exposure = ExposureConfig(exposure_time=780)  # -> ceil(780/300) = 3x300s
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, target=Target(name="Vega", ra=1.0, dec=1.0))
        obs.scheduler_data["queried_name"] = "Vega"

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        root_name = ssa_mod.build_fits_root_name(
            "STAR", target_name="Vega", exposure_time=300, num_exposures=3  # 780s split into 3x300s
        )
        assert result.sequencer_data["star_spectra_images"] == [
            f"{root_name}-1.fits", f"{root_name}-2.fits", f"{root_name}-3.fits",
        ]

    async def test_appends_to_star_spectra_images_instead_of_overwriting(self, tmp_path):
        """star_spectra_images is a shared key -- a value already present (e.g.
        from an earlier acquisition sub-plugin in the same program) must be
        kept, not clobbered."""
        exposure = ExposureConfig(exposure_time=1.0)
        obs = _make_obs(obs_dir=tmp_path, exposure=exposure, target=Target(name="Vega", ra=1.0, dec=1.0))
        obs.sequencer_data["star_spectra_images"] = ["earlier_image.fits"]

        result = await StarSpectraAcquisitionPlugin().execute(obs)

        assert result.sequencer_data["star_spectra_images"][0] == "earlier_image.fits"
        assert len(result.sequencer_data["star_spectra_images"]) == 2

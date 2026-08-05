"""
Tests des helpers d'acquisition partagés (acquisition_helpers.py).
Lancement : pytest plugins/sequencer_plugins/test_acquisition_helpers.py -v
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from rosace.plugins.sequencer_plugins.acquisition_helpers import capture_guide_image, compute_exposure_plan


class TestCaptureGuideImage:

    async def test_exposes_camera_for_given_duration(self, tmp_path):
        camera = AsyncMock()
        camera.expose.return_value = MagicMock()

        await capture_guide_image(camera, 0.3, str(tmp_path))

        camera.expose.assert_awaited_once_with(0.3)

    async def test_writes_fits_and_returns_path(self, tmp_path):
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image

        result = await capture_guide_image(camera, 0.3, str(tmp_path))

        expected_path = tmp_path / "guide_0001.fits"
        fake_image.writeto.assert_called_once_with(expected_path, overwrite=True)
        assert result == expected_path

    async def test_uses_custom_filename(self, tmp_path):
        fake_image = MagicMock()
        camera = AsyncMock()
        camera.expose.return_value = fake_image

        result = await capture_guide_image(camera, 0.3, str(tmp_path), filename="check.fits")

        assert result == tmp_path / "check.fits"


class TestComputeExposurePlan:

    def test_magnitude_zero_returns_mag0_exposure_time(self):
        exposure_time, count = compute_exposure_plan(magnitude=0.0, exposure_time_mag0=1.0, max_exposure_time=300)

        assert exposure_time == pytest.approx(1.0)
        assert count == 1

    def test_matches_user_provided_example(self):
        # mag 2.5 -> 1.0 * 10**(2.5/2.5) = 10s ; mag 5 -> 1.0 * 10**(5/2.5) = 100s
        assert compute_exposure_plan(2.5, 1.0, 300) == (pytest.approx(10.0), 1)
        assert compute_exposure_plan(5.0, 1.0, 300) == (pytest.approx(100.0), 1)

    def test_splits_into_several_max_duration_exposures_once_over_max(self):
        # raw = 1.0 * 10**(7.5/2.5) = 1000s ; 1000/300 -> ceil = 4
        exposure_time, count = compute_exposure_plan(magnitude=7.5, exposure_time_mag0=1.0, max_exposure_time=300)

        assert exposure_time == pytest.approx(300.0)
        assert count == 4

    def test_exactly_at_max_is_not_split(self):
        exposure_time, count = compute_exposure_plan(magnitude=0.0, exposure_time_mag0=300.0, max_exposure_time=300)

        assert exposure_time == pytest.approx(300.0)
        assert count == 1

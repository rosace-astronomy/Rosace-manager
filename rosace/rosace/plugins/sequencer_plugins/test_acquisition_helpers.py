"""
Tests des helpers d'acquisition partagés (acquisition_helpers.py).
Lancement : pytest plugins/sequencer_plugins/test_acquisition_helpers.py -v
"""

from unittest.mock import AsyncMock, MagicMock

from rosace.plugins.sequencer_plugins.acquisition_helpers import capture_guide_image


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

"""
Tests for the SLIT_XY sub-plugin (SlitXyPlugin), indi backend.
capture_guide_image() is replaced by a stand-in writing a synthetic frame
to disk (a bright, lamp-lit-like field with a dark, tilted slit line), and
publish_guide_preview() by a mock -- their own behavior is covered by
test_acquisition_helpers.py. find_axes() is the real one.
Launch: pytest plugins/sequencer_plugins/slit_xy/test_slit_xy.py -v
"""

import math
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import numpy as np
import pytest
from astropy.io import fits

from rosace.errors import SessionFatalError
from rosace.hardware.errors import DeviceCommandError
from rosace.hardware.spectroscope import LightSource
from rosace.models import Observation
from rosace.plugins.sequencer_plugins.slit_xy import slit_xy
from rosace.plugins.sequencer_plugins.slit_xy.slit_xy import SlitXyPlugin

WIDTH, HEIGHT = 400, 200
DEFAULT_X, DEFAULT_Y = 200, 97
TRUE_Y_AT_DEFAULT_X = 100.3
TILT_DEG = 1.5
IMAGE_NAME = "GUIDING_RAW_Vega_0_10s_20260926_210000.fits"


def _slit_frame(depth: float = 15000.0, seed: int = 1, slit_x_start: int = 0) -> np.ndarray:
    """Bright field (with a 15 % illumination gradient along X) crossed by a
    dark Gaussian slit line, tilted TILT_DEG, at TRUE_Y_AT_DEFAULT_X on column DEFAULT_X.
    slit_x_start > 0 leaves the columns before it without slit, which moves
    the slit's own centre away from DEFAULT_X."""
    rng = np.random.default_rng(seed)
    ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float64)
    field = 20000.0 * (1.0 + 0.15 * xs / WIDTH)
    line_y = TRUE_Y_AT_DEFAULT_X + (xs - DEFAULT_X) * math.tan(math.radians(TILT_DEG))
    frame = field - depth * np.exp(-0.5 * ((ys - line_y) / 2.5) ** 2) * (xs >= slit_x_start)
    frame += rng.normal(0.0, 100.0, frame.shape)
    return np.clip(frame, 0, 65535).astype(np.uint16)


def _make_obs(tmp_path) -> Observation:
    obs = Observation(
        id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(tmp_path),
    )
    obs.scheduler_data["queried_name"] = "Vega"
    obs.hardware["spectroscope"] = AsyncMock()
    obs.hardware["guiding_camera"] = AsyncMock()
    return obs


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Returns a dict: 'frame' is the data the stand-in capture writes (tests
    may replace it), 'capture'/'publish' the mocks, 'params' the slit_xy config."""
    state = {"frame": _slit_frame(), "params": {
        "light_source": "CALIB", "exposure_seconds": 0.1, "light_settle_seconds": 1.0,
        "slit_x_area": 300, "slit_y_area": 60,
    }}

    async def _capture(obs, exposure, **kwargs):
        path = tmp_path / IMAGE_NAME
        fits.PrimaryHDU(state["frame"]).writeto(path, overwrite=True)
        return path

    state["capture"] = AsyncMock(side_effect=_capture)
    state["publish"] = AsyncMock()
    state["sleep"] = AsyncMock()
    monkeypatch.setattr(slit_xy, "capture_guide_image", state["capture"])
    monkeypatch.setattr(slit_xy, "publish_guide_preview", state["publish"])
    monkeypatch.setattr(slit_xy.asyncio, "sleep", state["sleep"])
    monkeypatch.setattr(slit_xy, "SUBPLUGIN_PARAMS", {
        "guiding": {"slit_x": DEFAULT_X, "slit_y": DEFAULT_Y},
        "slit_xy": state["params"],
    })
    return state


class TestSlitXyMeasurement:

    async def test_finds_slit_height_at_default_column(self, tmp_path, setup):
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        data = obs.sequencer_data["slit_xy"]
        assert data["status"] == "calculated"
        assert data["slit_x"] == DEFAULT_X
        assert data["slit_y"] == pytest.approx(TRUE_Y_AT_DEFAULT_X, abs=0.05)
        assert data["image"] == IMAGE_NAME
        assert set(data) == {"status", "slit_x", "slit_y", "angle_deg", "segment", "image"}

    async def test_y_is_taken_at_default_column_not_at_slit_centre(self, tmp_path, setup):
        """The slit only starts at column 120, so within the 50-349 zone its
        centre sits near x = 235, 35 px from DEFAULT_X: keeping the centre's
        own Y would be ~0.9 px off (35 * tan 1.5 deg). The line must be
        followed back to DEFAULT_X."""
        setup["frame"] = _slit_frame(slit_x_start=120)
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        data = obs.sequencer_data["slit_xy"]
        assert data["slit_x"] == DEFAULT_X
        assert data["slit_y"] == pytest.approx(TRUE_Y_AT_DEFAULT_X, abs=0.05)
        centre_y = TRUE_Y_AT_DEFAULT_X + 35 * math.tan(math.radians(TILT_DEG))
        assert abs(data["slit_y"] - centre_y) > 0.5  # sanity: the test really separates the two

    async def test_offset_y_is_added_to_the_measured_y(self, tmp_path, setup):
        setup["params"]["offset_y"] = -1.0
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        assert obs.sequencer_data["slit_xy"]["slit_y"] == pytest.approx(TRUE_Y_AT_DEFAULT_X - 1.0, abs=0.05)

    async def test_invalid_offset_y_keeps_default_without_capturing(self, tmp_path, setup):
        setup["params"]["offset_y"] = "one pixel"
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        setup["capture"].assert_not_awaited()
        assert obs.sequencer_data["slit_xy"] == {"status": "default", "image": None}

    async def test_values_rounded_to_two_decimals(self, tmp_path, setup):
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        slit_y = obs.sequencer_data["slit_xy"]["slit_y"]
        assert slit_y == round(slit_y, 2)

    async def test_slit_angle_and_segment_recorded(self, tmp_path, setup):
        setup["params"]["slit_x_area"] = None
        setup["params"]["slit_y_area"] = None
        setup["frame"] = _slit_frame(slit_x_start=120)
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        casier = obs.sequencer_data["slit_xy"]
        assert casier["angle_deg"] == pytest.approx(TILT_DEG, abs=0.1)
        xa, ya, xb, yb = casier["segment"]
        assert xa == pytest.approx(120, abs=3) and xb == pytest.approx(WIDTH - 1, abs=3)
        slope = math.tan(math.radians(TILT_DEG))
        assert ya == pytest.approx(TRUE_Y_AT_DEFAULT_X + (xa - DEFAULT_X) * slope, abs=0.3)
        assert yb == pytest.approx(TRUE_Y_AT_DEFAULT_X + (xb - DEFAULT_X) * slope, abs=0.3)

    @pytest.mark.parametrize("area", [None, "none", "None"])
    async def test_whole_frame_when_areas_are_none(self, tmp_path, setup, area):
        setup["params"]["slit_x_area"] = area
        setup["params"]["slit_y_area"] = area
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        assert obs.sequencer_data["slit_xy"]["slit_y"] == pytest.approx(TRUE_Y_AT_DEFAULT_X, abs=0.05)
        assert obs.measurements["slit_y_px"] == obs.sequencer_data["slit_xy"]["slit_y"]
        overlay = setup["publish"].await_args.kwargs["overlay"]
        assert overlay["zone"] == [-0.5, -0.5, WIDTH - 0.5, HEIGHT - 0.5]

    async def test_whole_frame_when_areas_absent(self, tmp_path, setup):
        del setup["params"]["slit_x_area"], setup["params"]["slit_y_area"]
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        assert obs.sequencer_data["slit_xy"]["status"] == "calculated"

    async def test_slit_outside_the_zone_falls_back_to_default(self, tmp_path, setup):
        setup["params"]["slit_y_area"] = 4  # rows 95-98, the slit is at 100.3
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        assert obs.sequencer_data["slit_xy"]["status"] == "default"

    async def test_uniform_frame_falls_back_to_default(self, tmp_path, setup):
        setup["frame"] = np.random.default_rng(2).normal(20000, 100, (HEIGHT, WIDTH)).astype(np.uint16)
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        assert obs.sequencer_data["slit_xy"] == {"status": "default", "image": IMAGE_NAME}
        assert "slit_y_px" not in obs.measurements

    async def test_round_dark_spot_falls_back_to_default(self, tmp_path, setup):
        ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
        frame = 20000.0 - 15000.0 * np.exp(-0.5 * ((xs - DEFAULT_X) ** 2 + (ys - DEFAULT_Y) ** 2) / 8.0 ** 2)
        setup["frame"] = (frame + np.random.default_rng(3).normal(0, 100, frame.shape)).astype(np.uint16)
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        assert obs.sequencer_data["slit_xy"]["status"] == "default"


class TestSlitXyCapture:

    async def test_capture_uses_configured_light_exposure_and_settle(self, tmp_path, setup):
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        call = setup["capture"].await_args
        assert call.args == (obs, 0.1)
        assert call.kwargs["light_source"] is LightSource.CALIB
        assert call.kwargs["light_settle_seconds"] == 1.0
        assert call.kwargs["subtract_dark"] is False
        assert call.kwargs.get("image_to_gui", False) is False

    @pytest.mark.parametrize("name, expected", [("FLAT", LightSource.FLAT), ("sky", LightSource.SKY)])
    async def test_other_usable_light_sources(self, tmp_path, setup, name, expected):
        setup["params"]["light_source"] = name
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        assert setup["capture"].await_args.kwargs["light_source"] is expected

    @pytest.mark.parametrize("name", ["DARK", "LASER"])
    async def test_unusable_light_source_keeps_default_without_capturing(self, tmp_path, setup, name):
        setup["params"]["light_source"] = name
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        setup["capture"].assert_not_awaited()
        assert obs.sequencer_data["slit_xy"] == {"status": "default", "image": None}

    async def test_capture_failure_keeps_default(self, tmp_path, setup):
        setup["capture"].side_effect = DeviceCommandError("exposure failed")
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        assert obs.sequencer_data["slit_xy"] == {"status": "default", "image": None}
        setup["publish"].assert_not_awaited()

    async def test_session_fatal_error_propagates(self, tmp_path, setup):
        setup["capture"].side_effect = SessionFatalError("guiding camera gone")
        obs = _make_obs(tmp_path)

        with pytest.raises(SessionFatalError):
            await SlitXyPlugin().execute(obs)

    async def test_spectroscope_back_to_sky_after_capture(self, tmp_path, setup):
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        obs.hardware["spectroscope"].set_light_source.assert_awaited_with(LightSource.SKY)

    async def test_spectroscope_back_to_sky_even_when_capture_fails(self, tmp_path, setup):
        setup["capture"].side_effect = DeviceCommandError("exposure failed")
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        obs.hardware["spectroscope"].set_light_source.assert_awaited_with(LightSource.SKY)

    async def test_waits_for_the_lamp_to_go_out_after_switching_back_to_sky(self, tmp_path, setup):
        obs = _make_obs(tmp_path)
        events = []
        obs.hardware["spectroscope"].set_light_source.side_effect = lambda source: events.append(("light", source))
        setup["sleep"].side_effect = lambda seconds: events.append(("sleep", seconds))

        await SlitXyPlugin().execute(obs)

        assert events[-2:] == [("light", LightSource.SKY), ("sleep", 1.0)]


class TestSlitXyGuiOverlay:

    async def test_overlay_has_zone_line_and_point(self, tmp_path, setup):
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        call = setup["publish"].await_args
        assert call.args[0].name == IMAGE_NAME
        assert call.kwargs["source"] == "slit_xy"
        overlay = call.kwargs["overlay"]
        # 300 x 60 zone centred on (200, 97): columns 50-349, rows 67-126
        assert overlay["zone"] == [49.5, 66.5, 349.5, 126.5]
        xa, ya, xb, yb = overlay["line"]
        assert (xa, xb) == (49.5, 349.5)
        assert yb - ya == pytest.approx(300 * math.tan(math.radians(TILT_DEG)), abs=0.3)
        assert overlay["point"] == [DEFAULT_X, obs.sequencer_data["slit_xy"]["slit_y"]]

    async def test_overlay_point_includes_offset_but_line_stays_measured(self, tmp_path, setup):
        setup["params"]["offset_y"] = -1.0
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        overlay = setup["publish"].await_args.kwargs["overlay"]
        xa, ya, xb, yb = overlay["line"]
        line_y_at_default_x = ya + (DEFAULT_X - xa) * (yb - ya) / (xb - xa)
        assert line_y_at_default_x == pytest.approx(TRUE_Y_AT_DEFAULT_X, abs=0.05)
        assert overlay["point"] == [DEFAULT_X, obs.sequencer_data["slit_xy"]["slit_y"]]
        assert overlay["point"][1] == pytest.approx(line_y_at_default_x - 1.0, abs=0.02)

    async def test_overlay_shows_only_the_zone_on_failure(self, tmp_path, setup):
        setup["frame"] = np.random.default_rng(2).normal(20000, 100, (HEIGHT, WIDTH)).astype(np.uint16)
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        overlay = setup["publish"].await_args.kwargs["overlay"]
        assert overlay["line"] is None and overlay["point"] is None
        assert overlay["zone"] == [49.5, 66.5, 349.5, 126.5]

    async def test_gui_failure_does_not_affect_the_measurement(self, tmp_path, setup):
        setup["publish"].side_effect = OSError("disk full")
        obs = _make_obs(tmp_path)

        await SlitXyPlugin().execute(obs)

        assert obs.sequencer_data["slit_xy"]["status"] == "calculated"

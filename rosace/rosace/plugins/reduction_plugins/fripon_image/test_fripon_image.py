"""
Tests for the FRIPON sky image sub-plugin (FriponImagePlugin), FRIPON itself
replaced by fakes: the capture list and the image download.
Run: pytest plugins/reduction_plugins/fripon_image/test_fripon_image.py -v
"""

import io
import logging
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pytest
from astropy.io import fits
from matplotlib.image import imsave

from rosace.models import Observation, Target
from rosace.plugins.reduction_plugins.fripon_image import fripon_image as module
from rosace.plugins.reduction_plugins.fripon_image.fripon_image import Capture, FriponImagePlugin, parse_capture_list
from rosace.plugins.reduction_plugins.fripon_image.test_fisheye import LAT, LON, TRUE, WHEN, synthetic_sky

# Mid-observation: the two frames below start 4 min before WHEN and end 4 min after
FRAME_STARTS = [WHEN - timedelta(minutes=4), WHEN + timedelta(seconds=60)]
FRAME_SECONDS = 180


def _jpeg(image) -> bytes:
    buffer = io.BytesIO()
    imsave(buffer, image, format="jpeg")
    return buffer.getvalue()


def _capture(when: datetime) -> Capture:
    return Capture(when, f"capture/FRRA12/{when:%Y%m}/FRRA12_{when:%Y%m%dT%H%M%S}_UT-0.jpg")


class FakeFripon:
    def __init__(self, captures, image=None, unreachable=False):
        self.captures, self.image, self.unreachable, self.downloads, self.lists = captures, image, unreachable, 0, 0

    async def fetch(self, client, station):
        self.lists += 1
        if self.unreachable:
            raise httpx.ConnectError("no network")
        return sorted(self.captures, key=lambda c: c.time, reverse=True)

    async def download(self, client, capture):
        self.downloads += 1
        if self.image is None:
            raise ValueError("not a JPEG image (text/html, 0 bytes)")
        return _jpeg(self.image)


@pytest.fixture
def fripon(monkeypatch):
    def install(fake: FakeFripon, **params):
        monkeypatch.setattr(module, "fetch_captures", fake.fetch)
        monkeypatch.setattr(module, "download_capture", fake.download)
        monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {"fripon": {"station": "FRRA12", "latitude_deg": LAT,
                                                                    "longitude_deg": LON, **params}})
        return fake
    return install


def _obs(tmp_path) -> Observation:
    names = []
    for i, start in enumerate(FRAME_STARTS):
        header = fits.Header()
        header["DATE-OBS"] = start.strftime("%Y-%m-%dT%H:%M:%S")
        header["EXPTIME"] = FRAME_SECONDS
        fits.writeto(tmp_path / f"HD_45910-{i + 1}.fits", np.zeros((4, 4), np.float32), header, overwrite=True)
        names.append(f"HD_45910-{i + 1}.fits")
    obs = Observation(id="0bs00001", created_at=WHEN, session_id="s", obs_dir=str(tmp_path))
    obs.scheduler_data = {"target": Target(name="HD  45910", ra=97.63724, dec=5.86700), "queried_name": "HD  45910"}
    obs.sequencer_data = {"star_spectra_images": names}
    return obs


def test_parse_capture_list_keeps_the_station():
    row = lambda station, t: [1, station, "StPancrasse", t,
                              f'<img src="./protect.php?img=capture/{station}/202610/{station}_x_UT-0-500x500.jpg" />']
    captures = parse_capture_list({"data": [row("FRAU02", "2026-10-18 07:15:07"), row("FRRA12", "2026-10-18 07:05:17"),
                                            row("FRRA12", "2026-10-18 07:15:17")]}, "FRRA12")
    assert [c.time.minute for c in captures] == [15, 5]                      # most recent first
    assert captures[0].path == "capture/FRRA12/202610/FRRA12_x_UT-0.jpg"     # the full image, not the thumbnail
    assert captures[0].file_name == "FRRA12_x_UT.jpg"
    with pytest.raises(ValueError, match="SQL"):
        parse_capture_list({"error": "An SQL error occurred"}, "FRRA12")


async def test_disabled_without_station(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {})
    caplog.set_level(logging.INFO)
    obs = await FriponImagePlugin().execute(_obs(tmp_path))
    assert "sky_camera" not in obs.reduction_data
    assert "Not used" in caplog.text


async def test_nearest_capture_annotated_with_the_stored_lens(tmp_path, fripon):
    fake = fripon(FakeFripon([_capture(WHEN - timedelta(minutes=12)), _capture(WHEN + timedelta(minutes=2))],
                             image=synthetic_sky(when=WHEN + timedelta(minutes=2))), lens=TRUE.to_config())
    obs = await FriponImagePlugin().execute(_obs(tmp_path))
    sky = obs.reduction_data["sky_camera"]
    assert sky["capture_time"] == (WHEN + timedelta(minutes=2)).isoformat()
    assert sky["offset_s"] == 120                            # mid-observation is WHEN
    assert sky["model"] == "fitted" and sky["matched_stars"] > 50
    assert (tmp_path / sky["file"]).read_bytes()[:2] == b"\xff\xd8"
    assert (tmp_path / sky["annotated_file"]).read_bytes()[:4] == b"\x89PNG"
    assert sky["annotated_file"].endswith("_0bs00001_fripon_annotated.png")
    # the target where the lens puts it, at the capture's time
    from rosace.astro_math import altitude_deg, azimuth_deg
    t = WHEN + timedelta(minutes=2)
    (x,), (y,) = TRUE.project([altitude_deg(97.63724, 5.86700, LAT, LON, t)], [azimuth_deg(97.63724, 5.86700, LAT, LON, t)])
    assert sky["target_pixel"] == pytest.approx([x, y], abs=0.5)


async def test_recent_local_capture_skips_fripon(tmp_path, fripon):
    """A capture already in the session directory within 5 min of
    mid-observation: neither the list nor a download."""
    capture = _capture(WHEN + timedelta(minutes=3))
    (tmp_path / capture.file_name).write_bytes(_jpeg(synthetic_sky(stars=False)))
    fake = fripon(FakeFripon([capture], image=None, unreachable=True))     # any request would fail
    obs = await FriponImagePlugin().execute(_obs(tmp_path))
    assert fake.lists == 0 and fake.downloads == 0
    assert obs.reduction_data["sky_camera"]["file"] == capture.file_name
    assert obs.reduction_data["sky_camera"]["model"] is None      # clouds and no stored lens: image alone


async def test_fitted_live_without_a_stored_lens(tmp_path, fripon):
    """Nothing configured but the station: the lens is searched on the image itself."""
    fripon(FakeFripon([_capture(WHEN)], image=synthetic_sky()))
    sky = (await FriponImagePlugin().execute(_obs(tmp_path))).reduction_data["sky_camera"]
    assert sky["model"] == "fitted" and sky["matched_stars"] > 50 and sky["rms_px"] < 1.0
    from rosace.astro_math import altitude_deg, azimuth_deg
    (x,), (y,) = TRUE.project([altitude_deg(97.63724, 5.86700, LAT, LON, WHEN)], [azimuth_deg(97.63724, 5.86700, LAT, LON, WHEN)])
    assert sky["target_pixel"] == pytest.approx([x, y], abs=0.5)


async def test_poor_fit_is_not_trusted(tmp_path, fripon, monkeypatch, caplog):
    """A fit off by more than MAX_RMS_PX: the stored lens as is (no stored lens: no marks)."""
    real_fit = module.fit

    def poor(*args, **kwargs):
        result = real_fit(*args, **kwargs)
        result.rms_px = module.MAX_RMS_PX + 1
        return result
    monkeypatch.setattr(module, "fit", poor)
    caplog.set_level(logging.INFO)
    fripon(FakeFripon([_capture(WHEN)], image=synthetic_sky()), lens=TRUE.to_config())
    sky = (await FriponImagePlugin().execute(_obs(tmp_path))).reduction_data["sky_camera"]
    assert sky["model"] == "stored" and "not trusted" in caplog.text


async def test_older_local_capture_reads_the_list(tmp_path, fripon):
    """7 min away: a nearer capture may exist, so the list is read; the one
    found there is downloaded, the local one left as it is."""
    old = _capture(WHEN - timedelta(minutes=7))
    (tmp_path / old.file_name).write_bytes(_jpeg(synthetic_sky()))
    fake = fripon(FakeFripon([old, _capture(WHEN + timedelta(minutes=3))], image=synthetic_sky()))
    obs = await FriponImagePlugin().execute(_obs(tmp_path))
    assert fake.lists == 1 and fake.downloads == 1
    assert obs.reduction_data["sky_camera"]["offset_s"] == 180


async def test_listed_capture_already_downloaded_is_reused(tmp_path, fripon):
    capture = _capture(WHEN - timedelta(minutes=6))
    (tmp_path / capture.file_name).write_bytes(_jpeg(synthetic_sky()))
    fake = fripon(FakeFripon([capture], image=None))         # a download would fail
    obs = await FriponImagePlugin().execute(_obs(tmp_path))
    assert fake.lists == 1 and fake.downloads == 0
    assert obs.reduction_data["sky_camera"]["file"] == capture.file_name


async def test_lens_fit_kept_for_the_next_observation(tmp_path, fripon, monkeypatch):
    fripon(FakeFripon([_capture(WHEN)], image=synthetic_sky()), lens=TRUE.to_config())
    calls = []
    real_fit = module.fit
    monkeypatch.setattr(module, "fit", lambda *a, **k: calls.append(1) or real_fit(*a, **k))
    first = (await FriponImagePlugin().execute(_obs(tmp_path))).reduction_data["sky_camera"]
    second_obs = _obs(tmp_path)
    second_obs.id = "0bs00002"
    second = (await FriponImagePlugin().execute(second_obs)).reduction_data["sky_camera"]
    assert len(calls) == 1                                   # fitted once, read back the second time
    assert (tmp_path / "FRRA12_20261018T010919_UT.json").exists()
    assert second["model"] == "fitted" and second["matched_stars"] == first["matched_stars"]
    assert second["target_pixel"] == first["target_pixel"]
    assert second["annotated_file"] != first["annotated_file"]   # drawn again, per observation

    # a new stored lens in the configuration: fitted again
    fripon(FakeFripon([_capture(WHEN)], image=synthetic_sky()), lens={**TRUE.to_config(), "cx": TRUE.cx + 1})
    await FriponImagePlugin().execute(_obs(tmp_path))
    assert len(calls) == 2


async def test_clouds_use_the_stored_lens_as_is(tmp_path, fripon):
    fripon(FakeFripon([_capture(WHEN)], image=synthetic_sky(stars=False)), lens=TRUE.to_config())
    obs = await FriponImagePlugin().execute(_obs(tmp_path))
    sky = obs.reduction_data["sky_camera"]
    assert sky["model"] == "stored" and sky["matched_stars"] == 0
    assert sky["target_pixel"] is not None


@pytest.mark.parametrize("captures, expected", [
    ([WHEN + timedelta(minutes=20)], "from mid-observation, more than 15 min"),
    ([WHEN - timedelta(hours=2, minutes=40)], "station stopped?"),
    ([], "station stopped?"),
])
async def test_no_capture_close_enough(tmp_path, fripon, caplog, captures, expected):
    fripon(FakeFripon([_capture(t) for t in captures], image=synthetic_sky()))
    obs = await FriponImagePlugin().execute(_obs(tmp_path))
    assert "sky_camera" not in obs.reduction_data
    assert expected in caplog.text


async def test_failures_never_raise(tmp_path, fripon, caplog):
    fripon(FakeFripon([_capture(WHEN)], unreachable=True))
    obs = await FriponImagePlugin().execute(_obs(tmp_path))
    assert "sky_camera" not in obs.reduction_data and "FRIPON unreachable" in caplog.text

    fripon(FakeFripon([_capture(WHEN)], image=None))
    obs = await FriponImagePlugin().execute(_obs(tmp_path))
    assert "sky_camera" not in obs.reduction_data and "not a JPEG image" in caplog.text

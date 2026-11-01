"""
Device classes of the salon mode (salon.py, conf/hardware_salon.yaml): the
real instrument, driven by day, indoors, while the light of the sky is
replayed from a recorded night through the shared bench (bench.py).

- ShiftedSkyMount: the real INDI mount, the right ascension it receives
  shifted so that it takes the hour angle -- hence the altitude, azimuth and
  pier side -- the recorded observation had on its night. A night target is
  below the horizon by day: unshifted, the mount would aim at the floor.
  Rosace itself only ever sees the original coordinates.
- HybridCamera / HybridASICamera: a real INDI camera that really exposes
  (at most max_wait_seconds) but hands back the recorded frame instead of
  the one it took, so every file of the observation stays consistent with
  the recorded night and the reduction is the replay's.
- BenchSpectroscope: the real Alpy600 light source, which also tells the
  bench which frames the cameras serve.

A camera can also be left unplugged: the profile then declares the demo's
ReplayCamera for it (devices.py). Constructor signature as every device
class: (config, logger, connect_on_create).
"""

from __future__ import annotations

from datetime import datetime, timezone

from astropy import units as u
from astropy.coordinates import ICRS, SkyCoord
from astropy.io import fits

from ..astro_math import local_sidereal_time_deg
from ..hardware.spectroscope import LightSource
from ..hardware_indi.devices.asi_camera import IndiASICamera
from ..hardware_indi.devices.camera import IndiCamera
from ..hardware_indi.devices.mount import IndiMount
from ..hardware_indi.devices.spectro_Alpy600 import IndiSpectroAlpy600
from .bench import get_bench
from .devices import REPLAY_FRAMES, recorded_frame, recorded_roi


def sky_shift_deg(recorded_at: datetime, now: datetime) -> float:
    """Sidereal time elapsed since `recorded_at` (degrees, 0-360): added to a
    right ascension, it gives the position that has now the hour angle the
    original position had then. The site's longitude cancels out."""
    return (local_sidereal_time_deg(0.0, now) - local_sidereal_time_deg(0.0, recorded_at)) % 360.0


def _parse_utc(value) -> datetime:
    when = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


class ShiftedSkyMount(IndiMount):
    """The real mount, its sky shifted by the sidereal time elapsed since the
    recorded observation nearest to the position asked for (its manifest
    "recorded_at"). The shift is recomputed at every coordinate sent, and the
    position read back is shifted back by the same amount, so the slew wait's
    on-target check (IndiMount._wait_for_slew) compares like with like."""

    def __init__(self, config=None, logger=None, connect_on_create=False):
        super().__init__(config=config, logger=logger, connect_on_create=connect_on_create)
        self._shift_deg = 0.0

    def connect(self, connect_device=True):
        get_bench()   # fails here, at INIT_SESSION, when the dataset is missing
        super().connect(connect_device=connect_device)

    def slew_to_coord_and_track(self, coord):
        icrs = coord.transform_to(ICRS())
        get_bench().point_at(float(icrs.ra.deg), float(icrs.dec.deg))
        super().slew_to_coord_and_track(icrs)

    def slew_to_coord_and_stop(self, coord):
        icrs = coord.transform_to(ICRS())
        get_bench().point_at(float(icrs.ra.deg), float(icrs.dec.deg))
        super().slew_to_coord_and_stop(icrs)

    def set_coord(self, coord, wait=True):
        icrs = coord.transform_to(ICRS())
        star = get_bench().nearest_star(float(icrs.ra.deg), float(icrs.dec.deg))
        recorded_at = _parse_utc(star["recorded_at"])
        self._shift_deg = sky_shift_deg(recorded_at, datetime.now(timezone.utc))
        shifted = SkyCoord(ra=(icrs.ra.deg + self._shift_deg) % 360.0 * u.deg, dec=icrs.dec, frame="icrs")
        self.logger.info(f"[SALON] {star['queried_name']}: RA shifted by {self._shift_deg:+.2f} deg, "
                         f"same hour angle as at {recorded_at:%Y-%m-%d %H:%M} UTC")
        super().set_coord(shifted, wait=wait)

    def get_current_coordinates(self):
        sky = super().get_current_coordinates()
        return SkyCoord(ra=(sky.ra.deg - self._shift_deg) % 360.0 * u.deg, dec=sky.dec, frame="icrs")


class HybridCameraMixin:
    """Put before an INDI camera class. Device params: replay_frames
    (science, guiding or wide_field: which recorded frames it hands back) and
    max_wait_seconds (longest real exposure)."""

    def __init__(self, config=None, logger=None, connect_on_create=False):
        super().__init__(config=config, logger=logger, connect_on_create=connect_on_create)
        config = config or {}
        self.frames = config.get("replay_frames", "science")
        if self.frames not in REPLAY_FRAMES:
            raise ValueError(f"replay_frames must be one of {', '.join(REPLAY_FRAMES)}, got {self.frames!r}")
        self.max_wait_seconds = float(config.get("max_wait_seconds", 5.0))
        self._requested_s = 0.0
        self._frame = None

    def connect(self, connect_device=True):
        get_bench()
        super().connect(connect_device=connect_device)

    def setExpTimeSec(self, exp_time_sec):
        """The exposure asked for chooses the recorded frame (a bias for 0 s);
        the real camera exposes at most max_wait_seconds."""
        self._requested_s = float(exp_time_sec)
        super().setExpTimeSec(min(self._requested_s, self.max_wait_seconds))

    def shoot_async(self):
        self._frame = recorded_frame(self.frames, self._requested_s)
        super().shoot_async()

    def get_received_image(self):
        super().get_received_image()   # the real exposure, received then set aside
        self.logger.debug(f"Real exposure replaced by {self._frame.name} (salon)")
        with fits.open(self._frame) as recorded:
            return fits.HDUList([fits.PrimaryHDU(recorded[0].data.copy(), recorded[0].header.copy())])

    def get_roi(self):
        """Size of the recorded frames handed back (their reuse checks compare
        against it), whatever the real camera's own frame."""
        return recorded_roi(self.frames)


class HybridCamera(HybridCameraMixin, IndiCamera):
    pass


class HybridASICamera(HybridCameraMixin, IndiASICamera):
    pass


class BenchSpectroscope(IndiSpectroAlpy600):
    """The real Alpy600 light source; the bench follows it."""

    def connect(self, connect_device=True):
        get_bench()
        super().connect(connect_device=connect_device)

    def set_light_source(self, source: LightSource) -> None:
        super().set_light_source(source)
        get_bench().light_source = source

"""
Replay device classes, declared in a hardware profile like the INDI ones and
wrapped in the same hardware_indi/adapters/: each offers the method names
its adapter calls on the INDI device class (IndiMount, IndiCamera,
IndiFocuser, a spectroscope device). No hardware, no INDI: the cameras serve
recorded frames chosen by the shared bench (bench.py), and waits are
shortened so a demo shows a whole observation in a few minutes.

Constructor signature as every device class: (config, logger,
connect_on_create), config being the device's hardware YAML entry. Device
params read here: ReplayCamera's replay_frames (science or guiding) and
max_wait_seconds, ReplayMount's slew_seconds.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from astropy import units as u
from astropy.coordinates import ICRS, SkyCoord
from astropy.io import fits

from ..astro_math import hour_angle_deg, local_sidereal_time_deg
from ..config import SITE_LONGITUDE_DEG
from ..hardware.spectroscope import LightSource
from .bench import get_bench


class _ReplayDevice:
    def __init__(self, config: dict | None = None, logger=None, connect_on_create: bool = False) -> None:
        self.config = config or {}
        self.logger = logger or logging.getLogger(f"spectro.replay.{type(self).__name__}")
        self.timeout = None
        if connect_on_create:
            self.connect()

    def connect(self) -> None:
        get_bench()   # fails here, at INIT_SESSION, when the dataset is missing
        self.logger.debug(f"{type(self).__name__}: connected (replay)")

    def disconnect(self) -> None:
        self.logger.debug(f"{type(self).__name__}: disconnected (replay)")


class ReplayMount(_ReplayDevice):
    """Points instantly (after slew_seconds), wherever it is asked; the bench
    picks the recorded observation nearest to that position."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.slew_seconds = float(self.config.get("slew_seconds", 2.0))
        self.is_parked = False
        self._coord: SkyCoord | None = None

    def slew_to_coord_and_track(self, coord: SkyCoord) -> None:
        icrs = coord.transform_to(ICRS())
        time.sleep(self.slew_seconds)
        self._coord = icrs
        get_bench().point_at(float(icrs.ra.deg), float(icrs.dec.deg))
        self.logger.debug(f"Slewed to RA={icrs.ra.deg:.4f} Dec={icrs.dec.deg:.4f} (replay)")

    def get_current_coordinates(self) -> SkyCoord:
        if self._coord is None:
            raise RuntimeError("replay mount: no position yet (never slewed)")
        return self._coord

    def get_pier_side(self) -> dict[str, bool]:
        """East of the pier (pointing west) after the meridian, as on a real
        German-equatorial mount."""
        if self._coord is None:
            return {"PIER_EAST": False, "PIER_WEST": False}
        east = hour_angle_deg(float(self._coord.ra.deg), SITE_LONGITUDE_DEG) > 0
        return {"PIER_EAST": east, "PIER_WEST": not east}

    def park(self) -> None:
        self.is_parked = True

    def unpark(self) -> None:
        self.is_parked = False

    def sync_to_coord(self, coord: SkyCoord) -> None:
        self.logger.debug(f"Sync to RA={coord.ra.deg:.4f} Dec={coord.dec.deg:.4f} ignored (replay)")

    def get_home_position_icrs(self, longitude_deg: float) -> SkyCoord:
        return SkyCoord(ra=local_sidereal_time_deg(longitude_deg) * u.deg, dec=90.0 * u.deg, frame="icrs")


class ReplayCamera(_ReplayDevice):
    """Serves recorded frames: the science series or the guide images
    (replay_frames: science|guiding), chosen when the exposure starts. The
    wait is the exposure time, at most max_wait_seconds; the frame keeps its
    recorded header, so its EXPTIME is the real one."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.frames = self.config.get("replay_frames", "science")
        if self.frames not in ("science", "guiding"):
            raise ValueError(f"replay_frames must be 'science' or 'guiding', got {self.frames!r}")
        self.max_wait_seconds = float(self.config.get("max_wait_seconds", 5.0))
        self.exp_time_sec = 0.0
        self._setpoint_c: float | None = None
        self._frame = None

    def prepare_shoot(self) -> None:
        pass

    def setExpTimeSec(self, exp_time_sec: float) -> None:
        self.exp_time_sec = float(exp_time_sec)

    def shoot_async(self) -> None:
        bench = get_bench()
        self._frame = bench.science_frame(self.exp_time_sec) if self.frames == "science" else bench.guide_frame()
        self.logger.debug(f"Exposure of {self.exp_time_sec}s -> {self._frame.name} (replay)")

    def synchronize_with_image_reception(self, exp_time_sec: float | None = None) -> None:
        exposure = self.exp_time_sec if exp_time_sec is None else exp_time_sec
        time.sleep(min(float(exposure), self.max_wait_seconds))

    def get_received_image(self) -> fits.HDUList:
        with fits.open(self._frame) as recorded:
            return fits.HDUList([fits.PrimaryHDU(recorded[0].data.copy(), recorded[0].header.copy())])

    def get_roi(self) -> dict[str, Any]:
        """Size of the frames this camera serves."""
        bench = get_bench()
        sample = bench.path(bench.stars[0]["science"][0] if self.frames == "science" else bench.manifest["homing"])
        header = fits.getheader(sample)
        return {"X": 0, "Y": 0, "WIDTH": header["NAXIS1"], "HEIGHT": header["NAXIS2"]}

    def set_temperature(self, temperature_deg_c: float) -> None:
        self._setpoint_c = float(temperature_deg_c)

    def get_temperature(self) -> float:
        """The setpoint, reached at once; the ambient temperature before any."""
        return self._setpoint_c if self._setpoint_c is not None else get_bench().ambient_temp_c()

    def set_cooling_on(self) -> None:
        pass

    def set_cooling_off(self) -> None:
        pass


class ReplayFocuser(_ReplayDevice):
    """Ambient temperature of the recorded observation pointed at (the
    night's first one before any slew)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._position = 0

    def move_to(self, position: int) -> None:
        self._position = int(position)

    def get_position(self) -> int:
        return self._position

    def get_temperature(self) -> float:
        return get_bench().ambient_temp_c()


class ReplaySpectroscope(_ReplayDevice):
    """Light-source selector: tells the bench which frames the cameras serve."""

    def set_light_source(self, source: LightSource) -> None:
        get_bench().light_source = source
        self.logger.debug(f"Light source -> {source.value} (replay)")

    def get_light_source(self) -> LightSource:
        return get_bench().light_source

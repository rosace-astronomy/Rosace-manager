"""
The replay bench: the recorded dataset, and what the replayed instrument is
doing right now.

The dataset (scripts/build_demo_dataset.py) holds, for each observation of a
real night, its raw science frames, its guide images by role and PHD2's lock
position, plus the calibration series of that night. manifest.yaml describes
it; paths in it are relative to the dataset directory.

The devices share one bench, so that a camera knows what the mount points at
and which light source the spectroscope selects -- that is how a frame is
chosen:
- the mount points at the dataset observation nearest to its commanded
  position, or at none farther than HOMING_DISTANCE_DEG (the mount's homing:
  the homing guide image is served then);
- the science camera serves, with the spectroscope on SKY, the observation's
  next science frame; on DARK, a bias for a 0 s exposure, a dark otherwise;
  on CALIB the lamp; on FLAT the flat;
- the guiding camera serves, on SKY, the observation's guide images in the
  order the real night took them (GUIDE_ORDER, the last one repeated); on
  DARK a guiding dark; with a lamp on, the slit image.
Pointing at another observation starts its frames from the beginning again;
a position far from every observation in between (slew_72ed's approach
point, offset by backlash_offset_deg) does not count as leaving it, so the
guide images keep the night's order.

Optional, for the salon mode (salon.py), absent from the demo's dataset:
- an observation's own calibration series (the "calibrations" key of its
  manifest entry, when the night had several sessions), served instead of
  the night's;
- its wide-field image (the "wide_field" key), served by the pointing camera;
- its own guide image order (the "guide_order" key), for a program that
  takes fewer guide images than the recorded one (no focus step);
- the profile's failure_every: every N-th observation pointed at, the first
  guide image is the manifest's "cloudy" frame (sky background, no star), so
  slew_72ed's real plate solve fails as under a cloud.

Device methods run in worker threads (the adapters use asyncio.to_thread),
hence the lock. The dataset directory is the hardware profile's top-level
replay_dataset_dir key, read once.
"""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path
from typing import Any

import yaml

from ..config import HARDWARE_CONFIG_PATH
from ..hardware.spectroscope import LightSource

log = logging.getLogger("spectro.replay.bench")

REPLAY_DATASET_KEY = "replay_dataset_dir"
FAILURE_EVERY_KEY = "failure_every"
HOMING_DISTANCE_DEG = 2.0
GUIDE_ORDER = ("slew", "pointing", "focus", "stop")


def _separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    ra1, dec1, ra2, dec2 = map(math.radians, (ra1, dec1, ra2, dec2))
    cos_sep = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(dec2) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


class ReplayBench:
    def __init__(self, dataset_dir: Path, failure_every: int = 0) -> None:
        self.dataset_dir = Path(dataset_dir).expanduser()
        manifest_path = self.dataset_dir / "manifest.yaml"
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"Replay dataset not found: {manifest_path} -- build it with scripts/build_demo_dataset.py"
            )
        self.manifest: dict[str, Any] = yaml.safe_load(manifest_path.read_text())
        self.stars: list[dict[str, Any]] = self.manifest["stars"]
        self.light_source = LightSource.SKY
        self.ra_deg: float | None = None
        self.dec_deg: float | None = None
        self._star: dict[str, Any] | None = None
        self._last_star: dict[str, Any] | None = None
        self._next: dict[str, int] = {}
        self._lock = threading.Lock()
        self.failure_every = int(failure_every or 0)
        self._observations = 0
        self._cloudy = False

    def path(self, relative: str) -> Path:
        return self.dataset_dir / relative

    @property
    def star(self) -> dict[str, Any] | None:
        """The dataset observation the mount points at, None when far from all."""
        return self._star

    def nearest_star(self, ra_deg: float, dec_deg: float) -> dict[str, Any]:
        """The dataset observation nearest to a position, however far."""
        return min(self.stars, key=lambda s: _separation_deg(ra_deg, dec_deg, s["ra_deg"], s["dec_deg"]))

    def point_at(self, ra_deg: float, dec_deg: float) -> None:
        with self._lock:
            self.ra_deg, self.dec_deg = ra_deg, dec_deg
            nearest = self.nearest_star(ra_deg, dec_deg)
            if _separation_deg(ra_deg, dec_deg, nearest["ra_deg"], nearest["dec_deg"]) > HOMING_DISTANCE_DEG:
                nearest = None
            self._star = nearest
            if nearest is not None and nearest is not self._last_star:
                self._last_star = nearest
                self._next = {}
                self._observations += 1
                self._cloudy = (self.failure_every > 0 and "cloudy" in self.manifest
                                and self._observations % self.failure_every == 0)
                log.info(f"[REPLAY] Pointing at {nearest['queried_name']}: replaying the frames of "
                         f"observation {nearest['observation_id']} ({self.manifest['source'].split(',')[0]})")
                if self._cloudy:
                    log.info(f"[REPLAY] A cloud passes over {nearest['queried_name']} "
                             f"(failure_every: {self.failure_every}): its first guide image shows no star")

    def _cycle(self, key: str, files: list[str], repeat_last: bool = False) -> Path:
        index = self._next.get(key, 0)
        self._next[key] = index + 1
        if repeat_last:
            index = min(index, len(files) - 1)
        return self.path(files[index % len(files)])

    def science_frame(self, exposure_s: float) -> Path:
        with self._lock:
            calibrations = (self._star or {}).get("calibrations") or self.manifest["calibrations"]
            source = self.light_source
            if source is LightSource.DARK:
                kind = "bias" if exposure_s == 0 else "dark"
                return self._cycle(kind, calibrations[kind])
            if source is LightSource.CALIB:
                return self._cycle("calib", calibrations["calib"])
            if source is LightSource.FLAT:
                return self._cycle("flat", calibrations["flat"])
            star = self._star or self.stars[0]
            return self._cycle("science", star["science"])

    def guide_frame(self) -> Path:
        with self._lock:
            if self.light_source is LightSource.DARK:
                return self._cycle("guiding_dark", self.manifest["calibrations"]["guiding_dark"])
            if self._star is None:
                return self.path(self.manifest["homing"])
            guiding = self._star["guiding"]
            if self.light_source is not LightSource.SKY:
                return self.path(guiding["slit"])
            if self._cloudy and self._next.get("guide", 0) == 0:
                self._next["guide"] = 1
                return self.path(self.manifest["cloudy"])
            order = self._star.get("guide_order") or GUIDE_ORDER
            return self._cycle("guide", [guiding[role] for role in order], repeat_last=True)

    def wide_field_frame(self) -> Path:
        """The wide-field image recorded for the observation pointed at."""
        with self._lock:
            star = self._star
            if star is None or not star.get("wide_field"):
                name = star["queried_name"] if star is not None else "this position"
                raise RuntimeError(f"no wide-field image recorded for {name}")
            return self.path(star["wide_field"])

    def ambient_temp_c(self) -> float:
        star = self._star
        if star is not None and star.get("ambient_temp_c") is not None:
            return float(star["ambient_temp_c"])
        return float(self.manifest["ambient_temp_c"])

    def lock_position(self) -> tuple[float, float] | None:
        star = self._star
        return tuple(star["lock_position"]) if star is not None and star.get("lock_position") else None

    def guide_log(self) -> Path | None:
        """The recorded PHD2 guide log section of the observation pointed at
        (None without one: a dataset built without --guidelog)."""
        star = self._star
        return self.path(star["guide_log"]) if star is not None and star.get("guide_log") else None


_bench: ReplayBench | None = None
_bench_lock = threading.Lock()


def _profile() -> dict[str, Any]:
    with open(HARDWARE_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def dataset_dir_from_profile() -> Path | None:
    """The active hardware profile's replay_dataset_dir, None if it has none
    (not a replay profile)."""
    value = _profile().get(REPLAY_DATASET_KEY)
    return Path(value).expanduser() if value else None


def get_bench() -> ReplayBench:
    """The bench shared by every replay device and the fake PHD2, loaded on
    first use from the active hardware profile."""
    global _bench
    with _bench_lock:
        if _bench is None:
            dataset_dir = dataset_dir_from_profile()
            if dataset_dir is None:
                raise RuntimeError(f"{HARDWARE_CONFIG_PATH} declares no {REPLAY_DATASET_KEY}")
            _bench = ReplayBench(dataset_dir, failure_every=_profile().get(FAILURE_EVERY_KEY, 0))
        return _bench


def set_bench(bench: ReplayBench | None) -> None:
    """Replaces the shared bench (tests), or forgets it (None)."""
    global _bench
    with _bench_lock:
        _bench = bench

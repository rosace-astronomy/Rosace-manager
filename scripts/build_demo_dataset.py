"""
Builds the replay dataset of the demo mode from real observations.

The demo mode (`python -m rosace.main --demo-mode`, and the Docker image)
runs the real Be_stars acquisition and reduction against replay devices
(rosace/hardware_replay/), which hand back these recorded frames instead of
driving a telescope. This script collects, from one observation night, the
files those devices need and describes them in manifest.yaml:

- per observation: the raw science frames, the raw guide images by role
  (slew, slit, pointing, focus, stop), PHD2's lock position (read from
  rosace.log) and the ambient temperature;
- the calibration series shared by the night (bias, dark, lamp, flat,
  guiding dark), and the guide image of the mount's homing;
- the reduction's reference files (lamp lines, instrumental response);
- the astrometry.net index tiles covering the demo fields.

Everything is read-only on the source side: files are copied, never moved
or modified. Run by hand, once per new dataset, then again only to change
the observations:

    python scripts/build_demo_dataset.py [--output DIR]

DIR defaults to docker/replay_data/ (ignored by git), where the Docker
build finds it; for --demo-mode without Docker, use
--output ~/.rosace-demo/replay.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path.home() / ".rosace"
NIGHT_DIR = DATA_DIR / "observations" / "2026-10-14_St-Pancrasse"
LOG_DIR = DATA_DIR / "log"
TARGETS_DB = DATA_DIR / "databases" / "targets.db"

# Observations replayed, in the order the demo observes them. All from the
# night of 2026-10-14 (session cd096639, except M 57: session 7ccb6279), so
# they share one set of calibration frames. M 57 is replayed through the
# Be_stars chain, like the stars.
OBSERVATION_IDS = [
    "6a794eb7",  # HD 7636
    "8150f3db",  # psi Per
    "1f60c4b7",  # eps Cas
    "0ce4b604",  # M 57
    "16b63f3c",  # pi02 Ori
    "824bf7ac",  # ome Ori
    "af2590fb",  # eta Ori
    "5270a894",  # HD 50820
    "e164cccb",  # HD 29866
]
DEMO_PROGRAM = "Be_stars"

# Calibration series of the session cd096639 (camera at -5 degC)
CALIBRATION_ROOTS = {
    "bias": "BIAS_-5deg_5x0_00s_20261015_003402",
    "dark": "DARK_-5deg_5x180s_20261012_183653",
    "calib": "CALIB_3x3s_20261014_181500",
    "flat": "FLAT_5x3s_20261014_181516",
    "guiding_dark": "GUIDINGDARK_7x10s_20261014_181013",
}

REFERENCE_LINES = DATA_DIR / "reference_lines" / "Alpy600_ArNe.txt"
RESPONSE = DATA_DIR / "response" / "Vega_Object_C_RI.fit"

# Index tiles that solve every guide image of the observations above,
# homing included (checked 2026-10-16: 37 images, about 2 s each, 43 MB).
INDEX_DIR = Path("/usr/share/astrometry-5200")
INDEX_TILES = [f"index-5206-{hp}.fits" for hp in ("01", "02", "03", "13", "21", "22", "23")]

LOCK_LINE = re.compile(
    r"^(\S+ \S+) - \S+ - \[POINTING_72ED\] PHD2 lock position for (.+): \(([-\d.]+), ([-\d.]+)\)$"
)


def _raw_guide_name(name: str) -> str:
    return name.replace("GUIDING_", "GUIDING_RAW_", 1)


def _timestamp(name: str) -> str:
    return re.search(r"(\d{8}_\d{6})", name).group(1)


def _series(root: str) -> list[str]:
    files = sorted(NIGHT_DIR.glob(f"{root}-*.fits"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
    if not files:
        raise SystemExit(f"No file for series {root} in {NIGHT_DIR}")
    return [p.name for p in files]


def _lock_positions() -> list[tuple[datetime, str, float, float]]:
    """(UTC time, queried name, x, y) of every PHD2 lock position logged."""
    found = []
    for log in sorted(LOG_DIR.glob("rosace.log*")):
        for line in log.read_text(errors="replace").splitlines():
            match = LOCK_LINE.match(line)
            if match:
                when = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                found.append((when, match.group(2), float(match.group(3)), float(match.group(4))))
    return sorted(found)


def _spectral_type(name: str) -> str | None:
    connection = sqlite3.connect(f"file:{TARGETS_DB}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT spectral_type FROM stars WHERE name = ?", (name,)).fetchone()
    finally:
        connection.close()
    return row[0] if row else None


def _observation_dossiers() -> dict[str, dict]:
    dossiers = {}
    for path in NIGHT_DIR.glob("*.yaml"):
        dossier = yaml.safe_load(path.read_text())
        if isinstance(dossier, dict) and dossier.get("id") in OBSERVATION_IDS:
            dossiers[dossier["id"]] = dossier
    missing = set(OBSERVATION_IDS) - dossiers.keys()
    if missing:
        raise SystemExit(f"Observation dossier(s) not found in {NIGHT_DIR}: {sorted(missing)}")
    return dossiers


def _star_entry(dossier: dict, locks: list) -> tuple[dict, list[str]]:
    """The manifest entry of one observation, and the files it needs."""
    scheduler = dossier["scheduler_data"]
    sequencer = dossier["sequencer_data"]
    target = scheduler["target"]
    queried_name = scheduler["queried_name"]

    guiding = {
        "slew": _raw_guide_name(sequencer["slew_72ed"]["guide_image"]),
        "slit": sequencer["slit_xy"]["image"],
        "pointing": _raw_guide_name(sequencer["pointing_72ed"]["guide_image"]),
        "stop": _raw_guide_name(sequencer["stop_phd2_guiding"]["guide_image"]),
    }
    # focus writes its guide image without a target name, between pointing and stop
    start, end = _timestamp(guiding["pointing"]), _timestamp(guiding["stop"])
    focus = sorted(p.name for p in NIGHT_DIR.glob("GUIDING_RAW_10s_*.fits") if start < _timestamp(p.name) < end)
    if not focus:
        raise SystemExit(f"No focus guide image for {queried_name} between {start} and {end}")
    guiding["focus"] = focus[0]

    created = dossier["created_at"]   # YAML may already have parsed it
    if not isinstance(created, datetime):
        created = datetime.fromisoformat(created)
    lock = next(((x, y) for when, name, x, y in locks if name == queried_name and when >= created), None)
    if lock is None:
        raise SystemExit(f"No PHD2 lock position logged for {queried_name} after {created}")

    exposure = scheduler.get("exposure") or {}
    entry = {
        "observation_id": dossier["id"],
        "name": target["name"],
        "queried_name": queried_name,
        "ra_deg": target["ra"],
        "dec_deg": target["dec"],
        "magnitude_v": target.get("magnitude_v"),
        "spectral_type": _spectral_type(target["name"]),
        "program": DEMO_PROGRAM,
        "exposure_time": exposure.get("exposure_time"),
        "ambient_temp_c": dossier.get("measurements", {}).get("ambient_temp_c"),
        "lock_position": [lock[0], lock[1]],
        "science": [f"science/{name}" for name in sequencer["star_spectra_images"]],
        "guiding": {role: f"guiding/{name}" for role, name in guiding.items()},
    }
    files = [f"science/{name}" for name in sequencer["star_spectra_images"]]
    files += [f"guiding/{name}" for name in guiding.values()]
    return entry, files


def _homing_image(dossiers: dict[str, dict]) -> str:
    for observation_id in OBSERVATION_IDS:
        homing = dossiers[observation_id]["sequencer_data"].get("home_sync_am3")
        if homing and homing.get("guide_image"):
            return _raw_guide_name(homing["guide_image"])
    raise SystemExit("No homing guide image among the observations")


def build(output: Path) -> None:
    dossiers = _observation_dossiers()
    locks = _lock_positions()

    stars, files = [], []
    for observation_id in OBSERVATION_IDS:
        entry, needed = _star_entry(dossiers[observation_id], locks)
        stars.append(entry)
        files += needed

    calibrations = {}
    for kind, root in CALIBRATION_ROOTS.items():
        calibrations[kind] = [f"calibrations/{name}" for name in _series(root)]
        files += calibrations[kind]

    homing = f"guiding/{_homing_image(dossiers)}"
    files.append(homing)

    first = dossiers[OBSERVATION_IDS[0]]
    manifest = {
        "source": f"{NIGHT_DIR.name}, observations {', '.join(OBSERVATION_IDS)}",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # The first observation's ambient temperature sets the session's camera
        # target temperature, and so the temperature in the frames' names
        "ambient_temp_c": first["measurements"]["ambient_temp_c"],
        "camera_temperature_c": first["session_data"]["camera_target_temperature_deg_c"],
        "homing": homing,
        "calibrations": calibrations,
        "reduction": {
            "reference_lines": f"reduction/{REFERENCE_LINES.name}",
            "response": f"reduction/{RESPONSE.name}",
        },
        "astrometry": [f"astrometry/{tile}" for tile in INDEX_TILES],
        "stars": stars,
    }

    copies = [(NIGHT_DIR / Path(name).name, output / name) for name in files]
    copies += [(REFERENCE_LINES, output / manifest["reduction"]["reference_lines"]),
               (RESPONSE, output / manifest["reduction"]["response"])]
    copies += [(INDEX_DIR / tile, output / "astrometry" / tile) for tile in INDEX_TILES]
    missing = [str(source) for source, _ in copies if not source.is_file()]
    if missing:
        raise SystemExit("Missing source file(s):\n  " + "\n  ".join(missing))

    total = 0
    for source, destination in copies:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or destination.stat().st_size != source.stat().st_size:
            shutil.copy2(source, destination)
        total += destination.stat().st_size
    (output / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
    print(f"{len(stars)} observations, {len(copies)} files, {total / 1e6:.0f} MB -> {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "docker" / "replay_data",
                        help="dataset directory (default: docker/replay_data/)")
    args = parser.parse_args()
    build(args.output.expanduser())


if __name__ == "__main__":
    main()

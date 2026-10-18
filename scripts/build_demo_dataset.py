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
- the astrometry.net index tiles covering the demo fields;
- with --guidelog, PHD2's own GuideLog of that night (copied by hand from
  the guiding computer): each observation's guiding section, the first one
  begun after the observation started and before the next one, which the
  fake PHD2 replays so the demo writes a real guide log.

Everything is read-only on the source side: files are copied, never moved
or modified. The copy is then anonymized (anonymize_dataset()), so that the
dataset -- and the Docker image built with it -- names neither the observer
nor the site: the site's coordinates in the FITS headers are rounded to the
degree, the observer becomes "rosace", any mention of the site's name
becomes "Rosace-Demo"; check_anonymized() then refuses a dataset where one
is left. Run by hand, once per new dataset, then again only to change the
observations:

    python scripts/build_demo_dataset.py [--output DIR] [--guidelog FILE]
    python scripts/build_demo_dataset.py --anonymize-only [--output DIR]

--anonymize-only anonymizes an existing dataset without rebuilding it.

DIR defaults to docker/replay_data/ (ignored by git), where the Docker
build finds it; for --demo-mode without Docker, use
--output ~/.rosace-demo/replay.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
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
    "8150f3db",  # psi Per
    "1f60c4b7",  # eps Cas
    "0ce4b604",  # M 57
    "af2590fb",  # eta Ori
]
DEMO_PROGRAM = "Be_stars"
# The night's homing guide image, taken before its first observation (HD 7636,
# not replayed): served when the mount points away from every observation
HOMING_OBSERVATION_ID = "6a794eb7"

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

# Index tiles that solve every guide image of the observations above, with
# Rosace's scale bounds (checked 2026-10-18, 16 images: psi Per needs 02 and
# 03, eps Cas 03, M 57 13, eta Ori 21)
INDEX_DIR = Path("/usr/share/astrometry-5200")
INDEX_TILES = [f"index-5206-{hp}.fits" for hp in ("02", "03", "13", "21")]

# Anonymization: header cards holding the site's position, the observer, and
# the private strings the finished dataset must not contain (site name,
# observer name, the site's exact coordinates)
POSITION_CARDS = ("SITELAT", "SITELONG", "LAT-OBS", "LONG-OBS")
ELEVATION_CARDS = ("SITEELEV", "ALT-OBS")
DEMO_ELEVATION_M = 1000
DEMO_OBSERVER = "rosace"
DEMO_SITE = "Rosace-Demo"
SITE_NAME = re.compile(r"\bSt[-_ ]?Pan(crasse)?\b", re.IGNORECASE)
PRIVATE = [re.compile(p, re.IGNORECASE) for p in
           (r"Cochard", r"Pancrasse", r"\bSt[-_ ]?Pan\b", r"(?<![\d.])45\.289", r"(?<![\d.])5\.855")]

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


GUIDE_BEGINS = "Guiding Begins at "
GUIDE_ENDS = "Guiding Ends at "


def _guide_sections(guidelog: Path) -> list[tuple[datetime, str]]:
    """(UTC start, text) of every guiding section of a PHD2 GuideLog (local times)."""
    sections, current = [], None
    for line in guidelog.read_text(errors="replace").splitlines():
        if line.startswith(GUIDE_BEGINS):
            start = datetime.strptime(line[len(GUIDE_BEGINS):].strip(), "%Y-%m-%d %H:%M:%S").astimezone(timezone.utc)
            current = [line]
            sections.append((start, current))
        elif current is not None:
            current.append(line)
            if line.startswith(GUIDE_ENDS):
                current = None
    return [(start, "\n".join(lines) + "\n") for start, lines in sections]


def _created(dossier: dict) -> datetime:
    created = dossier["created_at"]
    return created if isinstance(created, datetime) else datetime.fromisoformat(created)


def _guide_logs(dossiers: dict[str, dict], guidelog: Path) -> dict[str, str]:
    """Observation ID -> its guiding section's text, for the observations that have one."""
    sections = _guide_sections(guidelog)
    starts = sorted(_created(d) for d in dossiers.values())
    found = {}
    for observation_id, dossier in dossiers.items():
        begin = _created(dossier)
        end = next((s for s in starts if s > begin), begin + timedelta(hours=3))
        match = next((text for start, text in sections if begin <= start < end), None)
        if match is None:
            print(f"No guiding section for observation {observation_id} in {guidelog.name}")
        else:
            found[observation_id] = match
    return found


def _homing_image() -> str:
    for path in NIGHT_DIR.glob("*.yaml"):
        dossier = yaml.safe_load(path.read_text())
        if isinstance(dossier, dict) and dossier.get("id") == HOMING_OBSERVATION_ID:
            homing = dossier["sequencer_data"].get("home_sync_am3") or {}
            if homing.get("guide_image"):
                return _raw_guide_name(homing["guide_image"])
    raise SystemExit(f"No homing guide image in observation {HOMING_OBSERVATION_ID}")


def build(output: Path, guidelog: Path | None = None) -> None:
    dossiers = _observation_dossiers()
    locks = _lock_positions()
    guide_logs = _guide_logs(dossiers, guidelog) if guidelog else {}

    stars, files = [], []
    for observation_id in OBSERVATION_IDS:
        entry, needed = _star_entry(dossiers[observation_id], locks)
        if observation_id in guide_logs:
            entry["guide_log"] = f"guiding/{observation_id}_PHD2_GuideLog.txt"
        stars.append(entry)
        files += needed

    calibrations = {}
    for kind, root in CALIBRATION_ROOTS.items():
        calibrations[kind] = [f"calibrations/{name}" for name in _series(root)]
        files += calibrations[kind]

    homing = f"guiding/{_homing_image()}"
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

    _compress_frame_paths(manifest)
    copies = [(NIGHT_DIR / Path(name).name, output / f"{name}.gz") for name in files]
    copies += [(REFERENCE_LINES, output / manifest["reduction"]["reference_lines"]),
               (RESPONSE, output / manifest["reduction"]["response"])]
    copies += [(INDEX_DIR / tile, output / "astrometry" / tile) for tile in INDEX_TILES]
    missing = [str(source) for source, _ in copies if not source.is_file()]
    if missing:
        raise SystemExit("Missing source file(s):\n  " + "\n  ".join(missing))

    total = 0
    for source, destination in copies:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.suffix == ".gz":
            if not destination.exists():
                with source.open("rb") as raw, gzip.open(destination, "wb", compresslevel=6) as packed:
                    shutil.copyfileobj(raw, packed)
        elif not destination.exists() or destination.stat().st_size != source.stat().st_size:
            shutil.copy2(source, destination)
        total += destination.stat().st_size
    for star in stars:
        if star.get("guide_log"):
            (output / star["guide_log"]).write_text(guide_logs[star["observation_id"]])
    (output / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
    print(f"{len(stars)} observations, {len(copies)} files, {total / 1e6:.0f} MB -> {output}")
    anonymize_dataset(output)


def _compress_frame_paths(manifest: dict) -> None:
    """The recorded frames are stored gzip-compressed (a guide image 4.6 ->
    1.1 MB, a science frame 2.9 -> 1.6 MB; astropy reads them as they are);
    the index tiles and the reduction's reference files stay plain."""
    manifest["homing"] += ".gz"
    for kind, names in manifest["calibrations"].items():
        manifest["calibrations"][kind] = [f"{name}.gz" for name in names]
    for star in manifest["stars"]:
        star["science"] = [f"{name}.gz" for name in star["science"]]
        star["guiding"] = {role: f"{name}.gz" if name else name for role, name in star["guiding"].items()}


def _is_fits(path: Path) -> bool:
    return path.name.lower().endswith((".fits", ".fit", ".fts", ".fits.gz"))


def _anonymized_value(key: str, value):
    """The card's anonymized value, or None when it needs no change."""
    if key in POSITION_CARDS:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        rounded = float(round(number))
        return None if number == rounded else (rounded if not isinstance(value, str) else f"{rounded:g}")
    if key in ELEVATION_CARDS:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return None if number in (0, DEMO_ELEVATION_M) else DEMO_ELEVATION_M
    if key == "OBSERVER":
        return None if str(value).strip() == DEMO_OBSERVER else DEMO_OBSERVER
    if isinstance(value, str) and SITE_NAME.search(value):
        return SITE_NAME.sub(DEMO_SITE, value)
    return None


def anonymize_dataset(output: Path) -> None:
    """Rounds the site's coordinates to the degree, replaces the observer by
    "rosace" and the site's name by "Rosace-Demo", in every FITS header and
    text file of the dataset (in place, only where needed), then checks
    nothing private is left."""
    changed = 0
    for path in sorted(p for p in output.rglob("*") if p.is_file()):
        if _is_fits(path):
            from astropy.io import fits
            with fits.open(path, memmap=False) as hdul:
                hdul.readall()
                before = changed
                for hdu in hdul:
                    for key in list(hdu.header.keys()):
                        if not key or key in ("COMMENT", "HISTORY"):
                            continue
                        new = _anonymized_value(key, hdu.header[key])
                        if new is not None:
                            hdu.header[key] = new
                            changed += 1
                    for kind in ("COMMENT", "HISTORY"):
                        if kind in hdu.header and any(SITE_NAME.search(str(c)) for c in hdu.header[kind]):
                            lines = [SITE_NAME.sub(DEMO_SITE, str(c)) for c in hdu.header[kind]]
                            del hdu.header[kind]
                            add = hdu.header.add_comment if kind == "COMMENT" else hdu.header.add_history
                            for line in lines:
                                add(line)
                            changed += 1
                if changed != before:     # written back, gzip-compressed again for a .gz name
                    temporary = path.with_name(f".{path.name}")
                    hdul.writeto(temporary, overwrite=True)
                    os.replace(temporary, path)
        elif path.suffix in (".yaml", ".txt"):
            text = path.read_text()
            new = SITE_NAME.sub(DEMO_SITE, text)
            if path.name == "manifest.yaml":
                new = re.sub(r"^source: (\d{4}-\d{2}-\d{2})_\S+?,", r"source: night \1,", new, flags=re.MULTILINE)
            if new != text:
                path.write_text(new)
                changed += 1
    print(f"Anonymized: {changed} change(s) in {output}")
    check_anonymized(output)


def check_anonymized(output: Path) -> None:
    """Refuses (SystemExit) a dataset where the observer's name, the site's
    name or its exact coordinates are left, in a FITS header or a text file."""
    leftovers = []
    for path in sorted(p for p in output.rglob("*") if p.is_file()):
        if _is_fits(path):
            from astropy.io import fits
            with fits.open(path, memmap=False) as hdul:
                text = "\n".join(repr(hdu.header) for hdu in hdul)
        elif path.suffix in (".yaml", ".txt"):
            text = path.read_text()
        else:
            continue
        for pattern in PRIVATE:
            match = pattern.search(text)
            if match:
                leftovers.append(f"{path.relative_to(output)}: {match.group(0)!r}")
    if leftovers:
        raise SystemExit("Private data left in the dataset:\n  " + "\n  ".join(leftovers))
    print("Anonymization check: nothing private left")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "docker" / "replay_data",
                        help="dataset directory (default: docker/replay_data/)")
    parser.add_argument("--guidelog", type=Path,
                        help="PHD2's GuideLog of the same night, replayed by the fake PHD2 (optional)")
    parser.add_argument("--anonymize-only", action="store_true",
                        help="anonymize the existing dataset in --output, without rebuilding it")
    args = parser.parse_args()
    if args.anonymize_only:
        anonymize_dataset(args.output.expanduser())
        return
    build(args.output.expanduser(), args.guidelog.expanduser() if args.guidelog else None)


if __name__ == "__main__":
    main()

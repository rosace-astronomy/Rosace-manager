# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Prepares the salon mode (`rosace --salon-mode <night>`, conf/config_salon.yaml):
the real instrument driven by day, indoors, the light of the sky replayed
from one real night (hardware_replay/salon_devices.py). Called by cli.py
before the server starts, with the salon config already active.

From the night's directory, under subplugin_params.salon.source_data_dir
(the real installation's data, ~/.rosace -- only ever read), and its
rosace.db (which observations succeeded), it writes the salon's replay
manifest (manifest.yaml in the replay profile's replay_dataset_dir): every
successful observation of the night with its recorded frames, named by
absolute path -- nothing is copied. Each observation is given the salon
program its recorded program maps to (subplugin_params.salon.programs,
Be_stars by default), so every program shows its own steps.

Then, under the salon's own data directory: the fixed admin account
(rosace/rosace, as the demo: the salon runs on an isolated network), the
reduction's reference files, the GUI's first images, the night's targets in
their salon programs, and the observation list asking for them in the
night's order, `cycles` times over (a whole day of fair). A "cloudy" guide
image (sky background, no star) is written next to the manifest, for the
profile's failure_every (hardware_replay/bench.py).

Refused while a session of the real installation runs: both would drive the
same instrument -- except with every device replayed (`--no-hardware`,
conf/hardware_salon_replay.yaml), which drives none.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from astropy.io import fits

from .config import BASE_DATA_DIR, GUI_PREVIEW_DIR, SUBPLUGIN_PARAMS
from .demo_seed import _copy_if_missing, _seed_admin, _write_preview_if_missing
from .hardware_replay.bench import dataset_dir_from_profile
from .simulated_data import RUNNING_MAX_AGE_HOURS
from .targets import (
    ProgramCreate, StarCreate, program_add_star, program_get_by_name, program_insert,
    star_add_alias, star_get_by_name, star_insert, targets_db_init,
)

PROGRAMS_DIR = Path(__file__).parent / "programs"
DEFAULT_PROGRAM = "Be_stars"
DEFAULT_CYCLES = 20
LIST_PRIORITY = 3
CLOUDY_FILE = "cloudy.fits"
GUIDE_ROLES = ("slew", "pointing", "focus", "stop")

LOCK_LINE = re.compile(
    r"^(\S+ \S+) - \S+ - \[POINTING_72ED\] PHD2 lock position for (.+): \(([-\d.]+), ([-\d.]+)\)$"
)


class SalonError(RuntimeError):
    """The salon cannot be prepared: the message says why."""


@dataclass
class SalonNight:
    night_dir: Path
    manifest: dict[str, Any]
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (observation, reason)
    warnings: list[str] = field(default_factory=list)


def _params() -> dict[str, Any]:
    return SUBPLUGIN_PARAMS.get("salon", {})


def _source_dir() -> Path:
    return Path(_params().get("source_data_dir", "~/.rosace")).expanduser()


def salon_program(recorded_program: str | None) -> str:
    """The salon program replaying an observation recorded under `recorded_program`."""
    return (_params().get("programs") or {}).get(recorded_program, DEFAULT_PROGRAM)


def _parse_utc(value) -> datetime:
    when = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise SalonError(f"database not found: {db_path}")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def check_no_session_running(db_path: Path, now: datetime | None = None) -> None:
    """Raises SalonError when the real installation has a session running
    (marked running, started less than RUNNING_MAX_AGE_HOURS ago)."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=RUNNING_MAX_AGE_HOURS)).isoformat()
    db = _connect_ro(db_path)
    try:
        if db.execute("SELECT 1 FROM sessions WHERE status = 'running' AND started_at > ?", (since,)).fetchone():
            raise SalonError(f"a session of the real installation is running ({db_path}): "
                             f"both would drive the same instrument")
    finally:
        db.close()


def _successful(db: sqlite3.Connection, night_dir: Path) -> list[tuple[str, str]]:
    """(observation id, YAML file name) of the night's successful
    acquisitions, oldest first."""
    rows = db.execute(
        "SELECT o.id, o.yaml_file, s.session_dir FROM observations o JOIN sessions s ON s.id = o.session_id "
        "WHERE o.sequencer_status = 'done' ORDER BY o.created_at"
    ).fetchall()
    return [(oid, yaml_file) for oid, yaml_file, session_dir in rows
            if session_dir and Path(session_dir).name == night_dir.name and yaml_file]


def find_night_dir(night: str, observations_dir: Path, db_path: Path) -> Path:
    """The night's session directory: `night` is its date (YYYY-MM-DD) or its
    full name. Several directories of that date: the one holding successful
    observations, an error naming them when several do."""
    exact = observations_dir / night
    if exact.is_dir():
        return exact
    candidates = sorted(p for p in observations_dir.glob(f"{night}_*") if p.is_dir())
    if not candidates:
        raise SalonError(f"no night directory {night}_* in {observations_dir}")
    if len(candidates) > 1:
        db = _connect_ro(db_path)
        try:
            candidates = [p for p in candidates if _successful(db, p)] or candidates
        finally:
            db.close()
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        raise SalonError(f"several night directories for {night}: {names} -- give the full name")
    return candidates[0]


def _lock_positions(night_dir: Path) -> list[tuple[datetime, str, float, float]]:
    """(UTC time, queried name, x, y) of every PHD2 lock position in the
    night's session logs."""
    found = []
    for path in sorted(night_dir.glob("*_session.log")):
        for line in path.read_text(errors="replace").splitlines():
            match = LOCK_LINE.match(line)
            if match:
                when = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                found.append((when, match.group(2), float(match.group(3)), float(match.group(4))))
    return sorted(found)


def _spectral_types(targets_db: Path) -> dict[str, str]:
    if not targets_db.is_file():
        return {}
    db = _connect_ro(targets_db)
    try:
        return {name: sptype for name, sptype in
                db.execute("SELECT name, spectral_type FROM stars WHERE spectral_type IS NOT NULL")}
    finally:
        db.close()


def _raw(name: str) -> str:
    return name.replace("GUIDING_", "GUIDING_RAW_", 1)


def _timestamp(name: str) -> str | None:
    match = re.search(r"(\d{8}_\d{6})", name)
    return match.group(1) if match else None


def _program_steps(program: str) -> list[str]:
    path = PROGRAMS_DIR / f"{program}.yaml"
    if not path.is_file():
        raise SalonError(f"salon program {program} not found: {path}")
    return list((yaml.safe_load(path.read_text()) or {}).get("sequencer") or [])


def _star_entry(night_dir: Path, dossier: dict, locks: list, sptypes: dict[str, str],
                warnings: list[str]) -> dict[str, Any]:
    """The manifest entry of one observation; raises ValueError naming what is missing."""
    scheduler = dossier.get("scheduler_data") or {}
    sequencer = dossier.get("sequencer_data") or {}
    target = scheduler.get("target") or {}
    queried_name = scheduler.get("queried_name") or target.get("name")

    def existing(name: str | None, what: str) -> Path:
        if not name:
            raise ValueError(f"no {what} recorded")
        path = night_dir / name
        if not path.is_file():
            raise ValueError(f"{what} missing: {name}")
        return path

    science = [existing(name, "science frame") for name in sequencer.get("star_spectra_images") or []]
    if not science:
        raise ValueError("no science frame recorded")
    guiding = {
        "slew": existing(_raw((sequencer.get("slew_72ed") or {}).get("guide_image") or ""), "slew guide image"),
        "pointing": existing(_raw((sequencer.get("pointing_72ed") or {}).get("guide_image") or ""),
                             "pointing guide image"),
        "stop": existing(_raw((sequencer.get("stop_phd2_guiding") or {}).get("guide_image") or ""),
                         "end-of-guiding guide image"),
    }
    # focus writes its guide image without a target name, between pointing and stop
    start, end = _timestamp(guiding["pointing"].name), _timestamp(guiding["stop"].name)
    focus = sorted(p for p in night_dir.glob("GUIDING_RAW_10s_*.fits")
                   if start and end and start < (_timestamp(p.name) or "") < end)
    guiding["focus"] = focus[0] if focus else guiding["pointing"]
    slit = (sequencer.get("slit_xy") or {}).get("image")
    guiding["slit"] = night_dir / slit if slit and (night_dir / slit).is_file() else guiding["pointing"]

    calibrations = {}
    for kind in ("bias", "dark", "calib", "flat"):
        calibrations[kind] = [str(existing(name, f"{kind} frame"))
                              for name in sequencer.get(f"{kind}_images") or []]
        if not calibrations[kind]:
            raise ValueError(f"no {kind} series recorded")

    created = _parse_utc(dossier["created_at"])
    lock = next(((x, y) for when, name, x, y in locks if name == queried_name and when >= created), None)
    if lock is None:
        warnings.append(f"{queried_name}: no PHD2 lock position in the session logs, the fake PHD2's default is used")

    recorded_program = scheduler.get("program")
    program = salon_program(recorded_program)
    order = [role for role in GUIDE_ROLES if role != "focus" or "focus" in _program_steps(program)]
    exposure = scheduler.get("exposure") or {}
    entry: dict[str, Any] = {
        "observation_id": dossier["id"],
        "name": target.get("name"),
        "queried_name": queried_name,
        "ra_deg": target.get("ra"),
        "dec_deg": target.get("dec"),
        "magnitude_v": target.get("magnitude_v"),
        "spectral_type": sptypes.get(target.get("name")),
        "program": program,
        "recorded_program": recorded_program,
        "recorded_at": created.isoformat(),
        "exposure_time": exposure.get("exposure_time"),
        "ambient_temp_c": (dossier.get("measurements") or {}).get("ambient_temp_c"),
        "camera_temperature_c": (dossier.get("session_data") or {}).get("camera_target_temperature_deg_c"),
        "science": [str(p) for p in science],
        "guiding": {role: str(p) for role, p in guiding.items()},
        "guide_order": order,
        "calibrations": calibrations,
    }
    if lock is not None:
        entry["lock_position"] = [lock[0], lock[1]]
    wide = (sequencer.get("wide_field_images") or [None])[0]
    if wide and (night_dir / wide).is_file():
        entry["wide_field"] = str(night_dir / wide)
    guide_log = sequencer.get("guide_log_file")
    if guide_log and (night_dir / guide_log).is_file():
        entry["guide_log"] = str(night_dir / guide_log)
    if None in (entry["name"], entry["ra_deg"], entry["dec_deg"]):
        raise ValueError("target incomplete in the observation file")
    return entry


def _guiding_darks(night_dir: Path, dossiers: list[dict]) -> list[str]:
    """The night's guiding dark frames: a raw series when one was shot, else
    the median (served for every frame of the series)."""
    median = None
    for dossier in dossiers:
        casier = (dossier.get("sequencer_data") or {}).get("guiding_dark_acquisition") or {}
        files = [night_dir / name for name in casier.get("dark_files") or []]
        if files and all(p.is_file() for p in files):
            return [str(p) for p in files]
        if median is None and casier.get("median_dark_file") and (night_dir / casier["median_dark_file"]).is_file():
            median = str(night_dir / casier["median_dark_file"])
    if median is None:
        raise SalonError(f"no guiding dark recorded in {night_dir}")
    return [median]


def build_night(night: str, source_dir: Path | None = None) -> SalonNight:
    """The salon manifest of one night (nothing written)."""
    source_dir = source_dir or _source_dir()
    db_path = source_dir / "databases" / "rosace.db"
    night_dir = find_night_dir(night, source_dir / "observations", db_path)
    db = _connect_ro(db_path)
    try:
        rows = _successful(db, night_dir)
    finally:
        db.close()
    if not rows:
        raise SalonError(f"no successful observation in {night_dir}")

    locks = _lock_positions(night_dir)
    sptypes = _spectral_types(source_dir / "databases" / "targets.db")
    result = SalonNight(night_dir, {})
    stars, dossiers = [], []
    for oid, yaml_file in rows:
        path = night_dir / yaml_file
        try:
            dossier = yaml.safe_load(path.read_text())
            stars.append(_star_entry(night_dir, dossier, locks, sptypes, result.warnings))
            dossiers.append(dossier)
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as e:
            result.skipped.append((f"{oid} ({yaml_file})", str(e)))
    if not stars:
        raise SalonError(f"no replayable observation in {night_dir} ({len(result.skipped)} skipped)")

    first = stars[0]
    result.manifest = {
        "source": f"{night_dir.name}, salon",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ambient_temp_c": first["ambient_temp_c"] if first["ambient_temp_c"] is not None else 15.0,
        "camera_temperature_c": first["camera_temperature_c"],
        "homing": first["guiding"]["slew"],
        "calibrations": {**first["calibrations"], "guiding_dark": _guiding_darks(night_dir, dossiers)},
        "reduction": {},
        "astrometry": [],
        "stars": stars,
    }
    return result


def write_cloudy_frame(source: Path, destination: Path, seed: int = 0) -> None:
    """A guide image under a cloud: the source frame's header, its sky
    background and noise, no star -- a plate solve finds nothing in it."""
    with fits.open(source) as hdul:
        data = hdul[0].data.astype(np.float64)
        header = hdul[0].header.copy()
        dtype = hdul[0].data.dtype
    median = float(np.median(data))
    noise = 1.4826 * float(np.median(np.abs(data - median)))
    cloudy = np.random.default_rng(seed).normal(median, max(noise, 1.0), data.shape)
    if np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        cloudy = np.clip(np.rint(cloudy), info.min, info.max)
    header["COMMENT"] = "Salon mode: a cloud, sky background and noise of the recorded frame, no star"
    destination.parent.mkdir(parents=True, exist_ok=True)
    fits.PrimaryHDU(cloudy.astype(dtype), header).writeto(destination, overwrite=True)


async def _seed_catalogue(stars: list[dict[str, Any]], cycles: int) -> Path:
    """The night's targets in their salon programs, and the observation list.
    The list names each target as the recorded night did (its queried name,
    e.g. Vega rather than * alf Lyr), declared as an alias of the star when
    it is not its canonical name: the scheduler shows that name, as it did
    that night."""
    await targets_db_init()
    for star in stars:
        program = await program_get_by_name(star["program"])
        program_id = program.id if program else await program_insert(
            ProgramCreate(name=star["program"], description="Salon: replay of real observations"))
        existing = await star_get_by_name(star["name"])
        star_id = existing.id if existing else await star_insert(StarCreate(
            name=star["name"], ra=star["ra_deg"], dec=star["dec_deg"],
            magnitude_v=star.get("magnitude_v"), spectral_type=star.get("spectral_type"),
        ))
        await program_add_star(program_id, star_id, star.get("exposure_time"))
        queried = star.get("queried_name") or star["name"]
        if queried != star["name"]:
            known = await star_get_by_name(queried)
            if known is None:
                await star_add_alias(star_id, queried)
    lines = ["Priority, Program, Star, commentaire"]
    for cycle in range(1, cycles + 1):
        lines += [f"{LIST_PRIORITY}, {s['program']}, {s.get('queried_name') or s['name']}, "
                  f"cycle {cycle}: replay of observation "
                  f"{s['observation_id']}" for s in stars]
    list_path = Path(SUBPLUGIN_PARAMS["observation_list_path"]).expanduser()
    list_path.parent.mkdir(parents=True, exist_ok=True)
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


def _install_reference_files(source_dir: Path) -> None:
    lines = (SUBPLUGIN_PARAMS.get("dispersion_law") or {}).get("reference_lines")
    if lines and not Path(lines).is_absolute():
        source = source_dir / "reference_lines" / lines
        if source.is_file():
            _copy_if_missing(source, BASE_DATA_DIR / "reference_lines" / lines)
    response = (SUBPLUGIN_PARAMS.get("response") or {}).get("file")
    if response and not Path(response).is_absolute():
        source = source_dir / "response" / response
        if source.is_file():
            _copy_if_missing(source, BASE_DATA_DIR / "response" / response)


def prepare_salon(night: str, check_running: bool = True) -> str:
    """Prepares the salon for `night`; returns the report to print. Raises
    SalonError when it cannot. check_running=False (--no-hardware: no
    instrument driven) skips the refusal while a real session runs."""
    source_dir = _source_dir()
    if check_running:
        check_no_session_running(source_dir / "databases" / "rosace.db")
    dataset_dir = dataset_dir_from_profile()
    if dataset_dir is None:
        raise SalonError("the active hardware profile declares no replay_dataset_dir")
    salon = build_night(night, source_dir)
    manifest = salon.manifest
    cloudy = dataset_dir / CLOUDY_FILE
    write_cloudy_frame(Path(manifest["homing"]), cloudy)
    manifest["cloudy"] = str(cloudy)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))

    cycles = int(_params().get("cycles", DEFAULT_CYCLES))
    asyncio.run(_seed_admin())
    list_path = asyncio.run(_seed_catalogue(manifest["stars"], cycles))
    _install_reference_files(source_dir)
    first = manifest["stars"][0]
    _write_preview_if_missing(Path(first["guiding"]["slew"]), GUI_PREVIEW_DIR / "guidage.fits")
    _write_preview_if_missing(Path(first["science"][0]), GUI_PREVIEW_DIR / "spectre.fits")

    report = [f"Night {salon.night_dir.name}: {len(manifest['stars'])} observations replayed"]
    for star in manifest["stars"]:
        report.append(f"  {star['recorded_at'][11:16]} UTC  {star['queried_name']:<20} "
                      f"{star['recorded_program']} -> {star['program']}")
    for observation, reason in salon.skipped:
        report.append(f"  skipped {observation}: {reason}")
    report += [f"  warning: {w}" for w in salon.warnings]
    report.append(f"Manifest: {dataset_dir / 'manifest.yaml'}")
    report.append(f"Observation list: {list_path} ({cycles} cycles)")
    return "\n".join(report)

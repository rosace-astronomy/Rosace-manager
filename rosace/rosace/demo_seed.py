# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Prepares the demo (conf/config_demo.yaml, IS_DEMO_MODE) from the replay
dataset the active hardware profile names (hardware_replay/bench.py; built by
scripts/build_demo_dataset.py). Called from main.py's lifespan().

- a fixed demo admin account (rosace/rosace) — a deliberate demo-only
  convenience, never do this for a real installation;
- the reduction's reference files (lamp lines, instrumental response) copied
  from the dataset to where the configuration expects them, when missing;
- the GUI's guide and 2D panels showing the dataset's first observation,
  when they show nothing yet;
- once, on an empty targets.db: the dataset's targets in its programs, with
  their recorded exposure time, and the observation list
  (observation_list_path) asking for them in the dataset's order.

Idempotent: what already exists is never overwritten, so restarting the
server keeps manual edits. Without a dataset, only the account is created
and a WARNING says how to build it.
"""

import logging
import shutil
from pathlib import Path

from astropy.io import fits

from .auth import auth_db_init, ensure_first_admin
from .config import BASE_DATA_DIR, GUI_PREVIEW_DIR, SUBPLUGIN_PARAMS
from .hardware_replay.bench import ReplayBench, dataset_dir_from_profile
from .targets import (
    program_add_star, program_insert, program_list, ProgramCreate,
    star_insert, StarCreate,
)

DEMO_ADMIN_USERNAME = "rosace"
DEMO_ADMIN_PASSWORD = "rosace"

log = logging.getLogger("spectro.demo_seed")


async def _seed_admin() -> None:
    await auth_db_init()
    try:
        await ensure_first_admin(DEMO_ADMIN_USERNAME, DEMO_ADMIN_PASSWORD)
        log.info(f"Demo admin account created: {DEMO_ADMIN_USERNAME}/{DEMO_ADMIN_PASSWORD}")
    except RuntimeError:
        log.info("An account already exists — skipping demo admin bootstrap.")


def _copy_if_missing(source: Path, destination: Path) -> None:
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _write_preview_if_missing(source: Path, destination: Path) -> None:
    """A recorded frame (gzip-compressed in the dataset) as the plain FITS
    file the GUI's panels read."""
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with fits.open(source) as frame:
            frame.writeto(destination)


def _install_files(bench: ReplayBench) -> None:
    reduction = bench.manifest["reduction"]
    if "reference_lines" in reduction:
        source = bench.path(reduction["reference_lines"])
        _copy_if_missing(source, BASE_DATA_DIR / "reference_lines" / source.name)
    if "response" in reduction:
        source = bench.path(reduction["response"])
        _copy_if_missing(source, BASE_DATA_DIR / "response" / source.name)
    first = bench.stars[0]
    _write_preview_if_missing(bench.path(first["guiding"]["slew"]), GUI_PREVIEW_DIR / "guidage.fits")
    _write_preview_if_missing(bench.path(first["science"][0]), GUI_PREVIEW_DIR / "spectre.fits")


async def _seed_catalogue(bench: ReplayBench) -> None:
    programs: dict[str, int] = {}
    lines = ["Priority, Program, Star, commentaire"]
    for priority, star in enumerate(bench.stars, start=1):
        program = star["program"]
        if program not in programs:
            programs[program] = await program_insert(
                ProgramCreate(name=program, description="Demo: replay of real observations")
            )
        star_id = await star_insert(StarCreate(
            name=star["name"], ra=star["ra_deg"], dec=star["dec_deg"],
            magnitude_v=star.get("magnitude_v"), spectral_type=star.get("spectral_type"),
        ))
        await program_add_star(programs[program], star_id, star.get("exposure_time"))
        lines.append(f"{priority}, {program}, {star['name']}, replay of observation {star['observation_id']}")

    list_path = Path(SUBPLUGIN_PARAMS["observation_list_path"]).expanduser()
    list_path.parent.mkdir(parents=True, exist_ok=True)
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info(f"Seeded the demo catalogue: {len(bench.stars)} targets in {', '.join(programs)}, wrote {list_path}")


async def seed_demo_data() -> None:
    await _seed_admin()

    dataset_dir = dataset_dir_from_profile()
    try:
        bench = ReplayBench(dataset_dir) if dataset_dir is not None else None
    except FileNotFoundError:
        bench = None
    if bench is None:
        log.warning(
            f"Replay dataset not found ({dataset_dir}): the demo cannot observe. Build it with "
            f"`python scripts/build_demo_dataset.py --output {dataset_dir}`, then restart the server."
        )
        return

    _install_files(bench)
    if await program_list():
        log.info("targets.db already populated — skipping demo catalogue seed.")
        return
    await _seed_catalogue(bench)

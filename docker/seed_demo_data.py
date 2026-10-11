"""
Demo-only: seeds a small illustrative catalogue (stars, programs, and the
observation plan scheduler_rosace_2026 reads) into a fresh targets.db, plus a
fixed rosace/rosace admin account, on first container start. Skipped once
anything already exists (program_list() non-empty / an account already
exists), so restarting the container — or reusing the /data volume — never
overwrites the catalogue, including any manual edits made through the GUI or
by hand-editing ObservationList.txt.

Goes through targets.py's own insert functions rather than raw SQL, so it
stays correct across schema changes the same way the REST API does.

The rosace/rosace credential is a deliberate demo-only convenience — this
image is for evaluation, not a real deployment (see Dockerfile) — so a
predictable login beats a --create-admin step someone has to run by hand
before they can even open the GUI. Never do this for a real installation.

Also copies real sample images (docker/preview/: a guide image, a raw 2D
frame and the final 1D spectrum of one real observation, 16 Peg on
2026-09-25) into the GUI's preview directory, so the three image panels are
not empty: the internal backend's simulated frames are empty placeholders
that are never shown. A panel file that already exists is never overwritten.
"""

import asyncio
import gzip
import logging
import shutil
from pathlib import Path

from rosace.config import GUI_PREVIEW_DIR
from rosace.database import db_init
from rosace.plugins.schedulers.scheduler_rosace_2026.scheduler import OBSERVATION_LIST_PATH
from rosace.auth import auth_db_init, ensure_first_admin
from rosace.targets import (
    targets_db_init, program_list,
    star_insert, StarCreate, star_add_alias,
    program_insert, ProgramCreate, program_add_star,
)

DEMO_ADMIN_USERNAME = "rosace"
DEMO_ADMIN_PASSWORD = "rosace"

PREVIEW_SOURCE_DIR = Path(__file__).parent / "preview"

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_demo_data")

# name, ra (deg), dec (deg), magnitude_v, spectral_type, aliases (first = used
# in the generated ObservationList.txt, for readability)
STARS = [
    ("* alf Lyr", 279.234734787025, 38.783688956244, 0.03, "A0V", ["Vega"]),
    ("* alf Boo", 213.915300294925, 19.1824091615312, -0.05, "K1.5IIIFe-0.5", ["Arcturus"]),
    ("* bet Lyr", 282.519979767648, 33.3626667697778, 3.42, "B8.5Ib-II", ["Sheliak"]),
    ("V* T CrB", 239.87567594413, 25.92017038415, 10.247, "M3IIIe_sh", ["T CrB"]),
    ("* bet Peg", 345.943572745207, 28.0827871246067, 2.42, "M2.5II-III", ["bet Peg"]),
    ("* bet CMi", 111.787673905417, 8.2893157625, 2.89, "B8Ve", ["bet CMi"]),
    ("* alf CMa", 101.287155333333, -16.7161158611111, -1.46, "A0mA1Va", ["Sirius"]),
    ("* alf Gem", 113.649471639766, 31.8882822164633, 1.58, "A1V+A2Vm", ["Castor"]),
    ("* bet Gem", 116.328957774379, 28.0261988900936, 1.14, "K0IIIb", ["Pollux"]),
    ("HD  90966", 157.07607979472, -63.16456766073, 6.426, "B2/3(III)ne", ["V507 Car"]),
]

PROGRAMS = {
    "Be_stars": "BeSS program (Observatoire de Paris)",
    "PN": "Planetary Nebulae",
}

# (program name, star canonical name) — priority in ObservationList.txt is
# just this list's order, 1-based.
PROGRAM_STARS = [
    ("Be_stars", "* alf Lyr"),
    ("PN", "* alf Boo"),
    ("Be_stars", "* bet Lyr"),
    ("Be_stars", "V* T CrB"),
    ("Be_stars", "* bet Peg"),
    ("Be_stars", "* bet CMi"),
    ("Be_stars", "* alf CMa"),
    ("Be_stars", "* alf Gem"),
    ("Be_stars", "* bet Gem"),
    ("Be_stars", "HD  90966"),
]


async def _seed_admin() -> None:
    await auth_db_init()
    try:
        await ensure_first_admin(DEMO_ADMIN_USERNAME, DEMO_ADMIN_PASSWORD)
        log.info(f"Created demo admin account: {DEMO_ADMIN_USERNAME}/{DEMO_ADMIN_PASSWORD}")
    except RuntimeError:
        log.info("An account already exists — skipping demo admin bootstrap.")


def _seed_preview_images() -> None:
    GUI_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    for source in sorted(PREVIEW_SOURCE_DIR.iterdir()):
        target = GUI_PREVIEW_DIR / source.name.removesuffix(".gz")
        if target.exists():
            continue
        if source.suffix == ".gz":
            with gzip.open(source, "rb") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        else:
            shutil.copyfile(source, target)
        log.info(f"Sample preview image copied: {target}")


async def main() -> None:
    await db_init()
    await targets_db_init()

    await _seed_admin()
    _seed_preview_images()

    if await program_list():
        log.info("targets.db already populated — skipping demo seed.")
        return

    log.info("Seeding demo catalogue (stars, programs, observation plan)...")

    display_name = {name: aliases[0] for name, *_rest, aliases in STARS}
    star_ids = {}
    for name, ra, dec, mag, sp_type, aliases in STARS:
        star_ids[name] = await star_insert(
            StarCreate(name=name, ra=ra, dec=dec, magnitude_v=mag, spectral_type=sp_type)
        )
        for alias in aliases:
            await star_add_alias(star_ids[name], alias)

    program_ids = {
        name: await program_insert(ProgramCreate(name=name, description=description))
        for name, description in PROGRAMS.items()
    }

    for program_name, star_name in PROGRAM_STARS:
        await program_add_star(program_ids[program_name], star_ids[star_name])

    lines = ["Priority, Program, Star, commentaire"]
    for i, (program_name, star_name) in enumerate(PROGRAM_STARS, start=1):
        lines.append(f"{i}, {program_name}, {display_name[star_name]}, demo seed")

    obs_list_path = Path(OBSERVATION_LIST_PATH).expanduser()
    obs_list_path.parent.mkdir(parents=True, exist_ok=True)
    obs_list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    log.info(f"Seeded {len(STARS)} stars, {len(PROGRAMS)} programs, wrote {obs_list_path}")


if __name__ == "__main__":
    asyncio.run(main())

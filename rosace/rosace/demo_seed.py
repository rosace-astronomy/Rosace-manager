# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Seeds a minimal illustrative catalogue (2 stars, 1 program, an observation
plan) into a fresh targets.db, plus a fixed demo admin account, the first
time the demo config (conf/config_demo.yaml, IS_DEMO_MODE) starts against an
empty database. Called from main.py's lifespan(). Skipped once anything
already exists, so restarting the server never overwrites manual edits made
through the GUI.

The program is deliberately named "demo" — the one program YAML
(programs/demo.yaml) that's backend-agnostic, so it needs no shadow-copy
trick the way the Docker demo's richer catalogue does (see
docker/seed_demo_data.py's own docstring).

The rosace/rosace credential is a deliberate demo-only convenience, same
rationale and same pair as the Docker demo's own (docker/seed_demo_data.py)
— never do this for a real installation.
"""

import logging

from .auth import auth_db_init, ensure_first_admin
from .plugins.schedulers.scheduler_rosace_2026.scheduler import OBSERVATION_LIST_PATH
from .targets import (
    program_add_star, program_insert, program_list, ProgramCreate,
    star_add_alias, star_insert, StarCreate,
)

DEMO_ADMIN_USERNAME = "rosace"
DEMO_ADMIN_PASSWORD = "rosace"

log = logging.getLogger("spectro.demo_seed")

# name, ra (deg), dec (deg), magnitude_v — same real coordinates already
# used by plugins/schedulers/blank/scheduler.py, kept consistent rather than
# inventing new example data.
_STARS = [
    ("Vega", 279.234735, 38.783611, 0.03),
    ("Arcturus", 213.915300, 19.182410, -0.05),
]

_PROGRAM_NAME = "demo"


async def _seed_admin() -> None:
    await auth_db_init()
    try:
        await ensure_first_admin(DEMO_ADMIN_USERNAME, DEMO_ADMIN_PASSWORD)
        log.info(f"Demo admin account created: {DEMO_ADMIN_USERNAME}/{DEMO_ADMIN_PASSWORD}")
    except RuntimeError:
        log.info("An account already exists — skipping demo admin bootstrap.")


async def seed_demo_data() -> None:
    """Idempotent: no-op once targets.db already has at least one program."""
    await _seed_admin()

    if await program_list():
        log.info("targets.db already populated — skipping demo catalogue seed.")
        return

    log.info("Seeding demo catalogue (2 stars, 1 program, observation plan)...")

    star_ids = {}
    for name, ra, dec, mag in _STARS:
        star_ids[name] = await star_insert(StarCreate(name=name, ra=ra, dec=dec, magnitude_v=mag))
        await star_add_alias(star_ids[name], name)

    program_id = await program_insert(
        ProgramCreate(name=_PROGRAM_NAME, description="Minimal demo catalogue (--demo-mode)")
    )
    for name, *_rest in _STARS:
        await program_add_star(program_id, star_ids[name])

    lines = ["Priority, Program, Star, commentaire"]
    for i, (name, *_rest) in enumerate(_STARS, start=1):
        lines.append(f"{i}, {_PROGRAM_NAME}, {name}, demo seed")

    OBSERVATION_LIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    OBSERVATION_LIST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    log.info(f"Seeded {len(_STARS)} stars, 1 program, wrote {OBSERVATION_LIST_PATH}")

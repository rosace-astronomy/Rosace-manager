"""
Base de données des cibles (étoiles) et programmes scientifiques.
Fichier : ~/.rosace/databases/targets.db
"""

import logging
from typing import Optional

import aiosqlite
from pydantic import BaseModel, Field

from .config import DATABASES_DIR

log = logging.getLogger("spectro.targets")

TARGETS_DB_PATH = DATABASES_DIR / "targets.db"

# ─────────────────────────────────────────────────────────────
# Modèles
# ─────────────────────────────────────────────────────────────

class StarCreate(BaseModel):
    """Champs fournis par l'utilisateur à la création d'une étoile."""
    name: str
    ra: float = Field(..., ge=0, lt=360, description="Ascension droite en degrés")
    dec: float = Field(..., ge=-90, le=90, description="Déclinaison en degrés")
    magnitude: Optional[float] = None
    spectral_type: Optional[str] = None
    exposure_time: Optional[float] = Field(default=None, gt=0, description="Temps d'exposition en secondes")
    notes: Optional[str] = None

class Star(StarCreate):
    """Étoile telle que retournée par l'API (avec id généré par SQLite)."""
    id: int


class ProgramCreate(BaseModel):
    """Champs fournis par l'utilisateur à la création d'un programme."""
    name: str
    description: Optional[str] = None

class Program(ProgramCreate):
    """Programme tel que retourné par l'API (avec id généré par SQLite)."""
    id: int


# ─────────────────────────────────────────────────────────────
# Initialisation
# ─────────────────────────────────────────────────────────────

async def targets_db_init() -> None:
    """Crée les tables si elles n'existent pas."""
    DATABASES_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stars (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                ra            REAL NOT NULL,
                dec           REAL NOT NULL,
                magnitude     REAL,
                spectral_type TEXT,
                exposure_time REAL,
                notes         TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS programs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS program_stars (
                program_id INTEGER NOT NULL REFERENCES programs(id),
                star_id    INTEGER NOT NULL REFERENCES stars(id),
                PRIMARY KEY (program_id, star_id)
            )
        """)
        await db.commit()
    log.info(f"[TARGETS DB] Base initialisée : {TARGETS_DB_PATH}")


# ─────────────────────────────────────────────────────────────
# Stars — CRUD
# ─────────────────────────────────────────────────────────────

async def star_insert(star: StarCreate) -> int:
    """Insère une étoile et retourne son id."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO stars (name, ra, dec, magnitude, spectral_type, exposure_time, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (star.name, star.ra, star.dec, star.magnitude,
             star.spectral_type, star.exposure_time, star.notes)
        )
        await db.commit()
        log.info(f"[TARGETS DB] Étoile insérée : {star.name} (id={cursor.lastrowid})")
        return cursor.lastrowid


async def star_get(star_id: int) -> Optional[Star]:
    """Récupère une étoile par son id."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM stars WHERE id = ?", (star_id,)) as cursor:
            row = await cursor.fetchone()
            return Star(**dict(row)) if row else None


async def star_get_by_name(name: str) -> Optional[Star]:
    """Récupère une étoile par son nom (insensible à la casse)."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM stars WHERE name LIKE ?", (name,)
        ) as cursor:
            row = await cursor.fetchone()
            return Star(**dict(row)) if row else None


async def star_list() -> list[Star]:
    """Retourne toutes les étoiles."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM stars ORDER BY name") as cursor:
            rows = await cursor.fetchall()
            return [Star(**dict(row)) for row in rows]


async def star_update(star_id: int, star: StarCreate) -> None:
    """Met à jour une étoile existante."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        await db.execute(
            """UPDATE stars
               SET name=?, ra=?, dec=?, magnitude=?, spectral_type=?, exposure_time=?, notes=?
               WHERE id=?""",
            (star.name, star.ra, star.dec, star.magnitude,
             star.spectral_type, star.exposure_time, star.notes, star_id)
        )
        await db.commit()
    log.info(f"[TARGETS DB] Étoile mise à jour : {star.name} (id={star_id})")


async def star_delete(star_id: int) -> None:
    """Supprime une étoile et ses liens avec les programmes."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        await db.execute("DELETE FROM program_stars WHERE star_id = ?", (star_id,))
        await db.execute("DELETE FROM stars WHERE id = ?", (star_id,))
        await db.commit()
    log.info(f"[TARGETS DB] Étoile supprimée : id={star_id}")


# ─────────────────────────────────────────────────────────────
# Programs — CRUD
# ─────────────────────────────────────────────────────────────

async def program_insert(program: ProgramCreate) -> int:
    """Insère un programme et retourne son id."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO programs (name, description) VALUES (?, ?)",
            (program.name, program.description)
        )
        await db.commit()
        log.info(f"[TARGETS DB] Programme inséré : {program.name} (id={cursor.lastrowid})")
        return cursor.lastrowid


async def program_get(program_id: int) -> Optional[Program]:
    """Récupère un programme par son id."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM programs WHERE id = ?", (program_id,)) as cursor:
            row = await cursor.fetchone()
            return Program(**dict(row)) if row else None


async def program_list() -> list[Program]:
    """Retourne tous les programmes."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM programs ORDER BY name") as cursor:
            rows = await cursor.fetchall()
            return [Program(**dict(row)) for row in rows]


async def program_delete(program_id: int) -> None:
    """Supprime un programme et ses liens avec les étoiles."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        await db.execute("DELETE FROM program_stars WHERE program_id = ?", (program_id,))
        await db.execute("DELETE FROM programs WHERE id = ?", (program_id,))
        await db.commit()
    log.info(f"[TARGETS DB] Programme supprimé : id={program_id}")


# ─────────────────────────────────────────────────────────────
# Liens program ↔ stars
# ─────────────────────────────────────────────────────────────

async def program_add_star(program_id: int, star_id: int) -> None:
    """Associe une étoile à un programme."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO program_stars (program_id, star_id) VALUES (?, ?)",
            (program_id, star_id)
        )
        await db.commit()
    log.info(f"[TARGETS DB] Étoile {star_id} ajoutée au programme {program_id}")


async def program_remove_star(program_id: int, star_id: int) -> None:
    """Retire une étoile d'un programme."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        await db.execute(
            "DELETE FROM program_stars WHERE program_id = ? AND star_id = ?",
            (program_id, star_id)
        )
        await db.commit()
    log.info(f"[TARGETS DB] Étoile {star_id} retirée du programme {program_id}")


async def program_get_stars(program_id: int) -> list[Star]:
    """Retourne toutes les étoiles d'un programme."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT s.* FROM stars s
               JOIN program_stars ps ON ps.star_id = s.id
               WHERE ps.program_id = ?
               ORDER BY s.name""",
            (program_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [Star(**dict(row)) for row in rows]


async def star_get_programs(star_id: int) -> list[Program]:
    """Retourne tous les programmes auxquels appartient une étoile."""
    async with aiosqlite.connect(TARGETS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT p.* FROM programs p
               JOIN program_stars ps ON ps.program_id = p.id
               WHERE ps.star_id = ?
               ORDER BY p.name""",
            (star_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [Program(**dict(row)) for row in rows]

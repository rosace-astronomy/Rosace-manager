import csv
import logging

from pydantic import ValidationError

from ....astro_math import altitude_deg
from ....config import (
    OBSERVATION_LIST_PATH, SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG,
    MIN_TARGET_ALTITUDE_DEG,
)
from ....database import db_count_observations_for_target
from ....models import Observation, Target, ExposureConfig
from ....targets import program_get_by_name, star_get_by_name, program_get_stars
from .. import SchedulerFatalError

log = logging.getLogger("spectro.plugin.scheduler")


def _target_altitude_deg(ra_deg: float, dec_deg: float) -> float:
    """Current altitude (degrees) of an ICRS (RA, Dec) coordinate above the
    site's horizon, right now. See astro_math.py for why this is plain
    trigonometry rather than an AstroPy/IERS-backed transform."""
    return altitude_deg(ra_deg, dec_deg, SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG)


def _read_observation_list(path) -> list[dict]:
    """Reads the observation plan (CSV: Priority, Program, Star, comment)."""
    try:
        f = open(path, encoding="utf-8", newline="")
    except FileNotFoundError as e:
        raise SchedulerFatalError(
            f"Fichier de plan d'observation introuvable : {path} "
            f"(voir observation_list_path dans conf/config.yaml)"
        ) from e

    with f:
        reader = csv.DictReader(f, skipinitialspace=True)
        rows = []
        for row in reader:
            try:
                rows.append({
                    "priority": int(row["Priority"].strip()),
                    "program": row["Program"].strip(),
                    "star": row["Star"].strip(),
                })
            except KeyError as e:
                raise SchedulerFatalError(
                    f"{path} : colonne manquante {e} — colonnes attendues : "
                    f"Priority, Program, Star (colonnes trouvées : {list(row.keys())})"
                ) from e
            except ValueError as e:
                raise SchedulerFatalError(f"{path} : ligne invalide {row} — {e}") from e
        return rows


class SchedulerPlugin:
    """
    Selects the next target to observe — the only plugin that writes to
    scheduler_data, and the only one that reads targets.db (targets.db is
    only ever populated by main.py's REST API, never by this plugin).

    Must always answer quickly (a few seconds): if there's nothing to
    observe right now, it sets target=None rather than waiting internally —
    it's the orchestrator's job to decide the delay before asking again
    (see SCHEDULER_RETRY_SECONDS in config.py).

    Reads OBSERVATION_LIST_PATH (conf/config.yaml) on every call — a
    read-only text file, never modified by this plugin. Each line (priority,
    program, star) is resolved against targets.db; invalid lines (unknown
    program/star, or a star not linked to the program) are skipped with a
    log, without failing the call. Valid lines are sorted by priority
    ascending (1 = highest priority; ties broken by file order — stable
    sort) and walked in that order, one at a time — **not** collapsed into
    one priority per target: the same star can appear on several lines at
    different priorities (e.g. priority 2 and priority 4), and another
    target at an intermediate priority (3) must then be offered between the
    two occurrences, not after both. For each line, its "rank" among
    occurrences of the same (program, canonical star) already seen — the
    same star can be requested under several names (aliases) on different
    lines, that counts as the same target — is compared against how many
    times this target has already been observed this astronomical night
    (rosace.db, across all sessions — stopping/restarting the session
    doesn't reset the counter): if that rank has already been fulfilled, the
    line is skipped and the next one is tried. Otherwise, its current
    altitude above the horizon (astro_math.py, site defined by
    SITE_LATITUDE_DEG/SITE_LONGITUDE_DEG) is compared against
    MIN_TARGET_ALTITUDE_DEG: if it's too low, the line is skipped and the
    next one is tried (no observation "consumed" either way). Altitude check
    only — no day/night (sun below horizon) check: remains a separate future
    chantier.

    A missing/malformed plan file, or invalid data in targets.db (e.g. a
    non-numeric exposure_time), raises SchedulerFatalError rather than
    silently failing every attempt in a loop.
    """

    async def run(self, obs: Observation) -> Observation:
        log.info("[SCHEDULER] Sélection de la prochaine cible…")

        rows = _read_observation_list(OBSERVATION_LIST_PATH)

        # Resolves each line, in priority order (stable sort: ties -> file order).
        resolved: list[dict] = []
        for row in sorted(rows, key=lambda r: r["priority"]):
            program = await program_get_by_name(row["program"])
            if not program:
                log.warning(f"[SCHEDULER] Programme inconnu, ligne ignorée : {row}")
                continue

            star = await star_get_by_name(row["star"])
            if not star:
                log.warning(f"[SCHEDULER] Étoile inconnue, ligne ignorée : {row}")
                continue

            try:
                program_stars = await program_get_stars(program.id)
            except ValidationError as e:
                raise SchedulerFatalError(
                    f"Donnée invalide dans targets.db pour le programme '{program.name}' "
                    f"(table program_stars) — vérifie notamment exposure_time : doit être "
                    f"un nombre ou vide (NULL), jamais une chaîne vide (''). Détail : {e}"
                ) from e

            matching = next((s for s in program_stars if s.id == star.id), None)
            if matching is None:
                log.warning(
                    f"[SCHEDULER] Étoile '{row['star']}' non liée au programme "
                    f"'{program.name}', ligne ignorée"
                )
                continue

            resolved.append({
                "priority": row["priority"],
                "program": program,
                "star": star,
                "queried_name": row["star"],
                "exposure_time": matching.exposure_time,
            })

        # Each valid line is walked in priority order. offered_count counts, for
        # each (program, canonical star), how many lines have already been seen
        # in THIS pass — compared against how many observations were actually
        # made tonight (rosace.db), that determines whether THIS precise
        # occurrence has already been fulfilled. This is what lets a target
        # requested at several priorities let an intermediate-priority target
        # through between two occurrences, instead of collapsing them all onto
        # a single priority (bug fixed: the latest priority of a repeated target
        # must not "borrow" the earliest priority of the same group).
        offered_count: dict[tuple[str, int], int] = {}
        for item in resolved:
            key = (item["program"].name, item["star"].id)
            offered_count[key] = offered_count.get(key, 0) + 1

            observed = await db_count_observations_for_target(item["program"].name, item["star"].name)
            if observed >= offered_count[key]:
                continue  # this precise occurrence has already been fulfilled tonight

            star = item["star"]
            altitude = _target_altitude_deg(star.ra, star.dec)
            if altitude < MIN_TARGET_ALTITUDE_DEG:
                log.info(
                    f"[SCHEDULER] {item['queried_name']} sous la hauteur minimale "
                    f"({altitude:.1f}° < {MIN_TARGET_ALTITUDE_DEG}°), ligne ignorée"
                )
                continue

            obs.scheduler_data["target"] = Target(
                name=star.name, ra=star.ra, dec=star.dec, magnitude_v=star.magnitude_v,
            )
            obs.scheduler_data["queried_name"] = item["queried_name"]
            obs.scheduler_data["program"] = item["program"].name
            obs.scheduler_data["priority"] = item["priority"]
            obs.scheduler_data["exposure"] = ExposureConfig(exposure_time=item["exposure_time"])
            log.info(
                f"[SCHEDULER] Cible sélectionnée : {item['queried_name']} "
                f"({star.name}, priorité {item['priority']}) "
                f"— programme : {item['program'].name}"
            )
            return obs

        log.info("[SCHEDULER] Aucune cible disponible à observer pour l'instant")
        obs.scheduler_data["target"] = None
        return obs

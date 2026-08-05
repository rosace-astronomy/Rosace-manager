import csv
import logging

import astropy.units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.time import Time
from astropy.utils.iers import conf as _iers_conf
from pydantic import ValidationError

from ....config import (
    OBSERVATION_LIST_PATH, SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG, SITE_ELEVATION_M,
    MIN_TARGET_ALTITUDE_DEG,
)
from ....database import db_count_observations_for_target
from ....models import Observation, Target, ExposureConfig
from ....targets import program_get_by_name, star_get_by_name, program_get_stars
from .. import SchedulerFatalError

log = logging.getLogger("spectro.plugin.scheduler")

# No internet access assumed on the observing site — a fresh IERS bulletin
# download would otherwise be attempted (and could stall/fail) the first time
# an AltAz transform runs. The bundled, non-downloaded IERS-B table is precise
# to well under 1 arcsecond, irrelevant next to MIN_TARGET_ALTITUDE_DEG.
_iers_conf.auto_download = False

_SITE_LOCATION = EarthLocation.from_geodetic(
    lon=SITE_LONGITUDE_DEG * u.deg, lat=SITE_LATITUDE_DEG * u.deg, height=SITE_ELEVATION_M * u.m
)


def _target_altitude_deg(ra_deg: float, dec_deg: float) -> float:
    """Current altitude (degrees) of an ICRS (RA, Dec) coordinate above the
    site's horizon, right now."""
    target = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    altaz = target.transform_to(AltAz(obstime=Time.now(), location=_SITE_LOCATION))
    return altaz.alt.deg


def _read_observation_list(path) -> list[dict]:
    """Lit le plan d'observation (CSV : Priority, Program, Star, commentaire)."""
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
    Sélectionne la prochaine cible à observer — c'est le seul plugin qui
    écrit dans scheduler_data, et le seul à lire targets.db (targets.db
    n'est peuplée que par les API REST de main.py, jamais par ce plugin).

    Doit toujours répondre rapidement (quelques secondes) : s'il n'y a rien à
    observer dans l'immédiat, il pose target=None plutôt que d'attendre en
    interne — c'est à l'orchestrateur de décider du délai avant de redemander
    (voir SCHEDULER_RETRY_SECONDS dans config.py).

    Lit OBSERVATION_LIST_PATH (conf/config.yaml) à chaque appel — un fichier
    texte en lecture seule, jamais modifié par ce plugin. Chaque ligne
    (priorité, programme, étoile) est résolue dans targets.db ; les lignes
    invalides (programme/étoile inconnus, ou étoile non liée au programme)
    sont ignorées avec un log, sans faire échouer l'appel. Les lignes valides
    sont triées par priorité croissante (1 = prioritaire ; à égalité, ordre
    d'apparition dans le fichier — tri stable) et examinées dans cet ordre,
    une par une — **pas** regroupées par une priorité unique par cible : une
    même étoile peut apparaître sur plusieurs lignes à des priorités
    différentes (ex. priorité 2 et priorité 4), et une autre cible de
    priorité intermédiaire (3) doit alors être proposée entre les deux
    occurrences, pas après. Pour chaque ligne, son "rang" parmi les
    occurrences déjà rencontrées de la même (programme, étoile canonique) —
    une même étoile peut être demandée sous plusieurs noms (alias) sur des
    lignes différentes, ça compte comme la même cible — est comparé au nombre
    de fois où cette cible a déjà été observée cette nuit astronomique
    (rosace.db, toutes sessions confondues — un arrêt/redémarrage de session
    ne remet pas le compteur à zéro) : si ce rang a déjà été honoré, la ligne
    est ignorée et on passe à la suivante. Sinon, sa hauteur actuelle
    au-dessus de l'horizon (AstroPy, site défini par SITE_LATITUDE_DEG/
    SITE_LONGITUDE_DEG/SITE_ELEVATION_M) est comparée à MIN_TARGET_ALTITUDE_DEG :
    si elle est trop basse, la ligne est ignorée et on passe à la suivante
    (pas d'observation "consommée" pour autant). Vérification de hauteur
    uniquement — pas de contrôle jour/nuit (soleil sous l'horizon) : reste un
    chantier séparé.

    Un fichier de plan absent/mal formé, ou une donnée invalide dans
    targets.db (ex. exposure_time non numérique), lève SchedulerFatalError
    plutôt que de faire échouer silencieusement chaque tentative en boucle.
    """

    async def run(self, obs: Observation) -> Observation:
        log.info("[SCHEDULER] Sélection de la prochaine cible…")

        rows = _read_observation_list(OBSERVATION_LIST_PATH)

        # Résout chaque ligne, dans l'ordre de priorité (tri stable : égalité -> ordre du fichier).
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
                    f"[SCHEDULER] Étoile '{star.name}' non liée au programme "
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

        # Chaque ligne valide est examinée dans l'ordre de priorité. offered_count
        # compte, pour chaque (programme, étoile canonique), combien de lignes ont
        # déjà été rencontrées dans CE parcours — comparé au nombre d'observations
        # réellement faites cette nuit (rosace.db), ça détermine si CETTE occurrence
        # précise a déjà été honorée. C'est ce qui permet à une cible demandée à
        # plusieurs priorités de laisser passer une cible de priorité intermédiaire
        # entre deux occurrences, plutôt que de toutes les regrouper sur une seule
        # priorité (bug corrigé : la priorité la plus tardive d'une cible répétée
        # ne doit pas "emprunter" la priorité la plus précoce du même groupe).
        offered_count: dict[tuple[str, int], int] = {}
        for item in resolved:
            key = (item["program"].name, item["star"].id)
            offered_count[key] = offered_count.get(key, 0) + 1

            observed = await db_count_observations_for_target(item["program"].name, item["star"].name)
            if observed >= offered_count[key]:
                continue  # cette occurrence précise a déjà été honorée cette nuit

            star = item["star"]
            altitude = _target_altitude_deg(star.ra, star.dec)
            if altitude < MIN_TARGET_ALTITUDE_DEG:
                log.info(
                    f"[SCHEDULER] {star.name} sous la hauteur minimale "
                    f"({altitude:.1f}° < {MIN_TARGET_ALTITUDE_DEG}°), ligne ignorée"
                )
                continue

            obs.scheduler_data["target"] = Target(
                name=star.name, ra=star.ra, dec=star.dec, magnitude_v=star.magnitude_v,
            )
            obs.scheduler_data["queried_name"] = item["queried_name"]
            obs.scheduler_data["programme"] = item["program"].name
            obs.scheduler_data["priority"] = item["priority"]
            obs.scheduler_data["exposure"] = ExposureConfig(exposure_time=item["exposure_time"])
            log.info(
                f"[SCHEDULER] Cible sélectionnée : {star.name} "
                f"(demandée sous '{item['queried_name']}', priorité {item['priority']}) "
                f"— programme : {item['program'].name}"
            )
            return obs

        log.info("[SCHEDULER] Aucune cible disponible à observer pour l'instant")
        obs.scheduler_data["target"] = None
        return obs

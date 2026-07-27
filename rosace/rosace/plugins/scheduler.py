import csv
import logging

from ..config import OBSERVATION_LIST_PATH
from ..database import db_count_observations_for_target
from ..models import Observation, Target, ExposureConfig
from ..targets import program_get_by_name, star_get_by_name, program_get_stars

log = logging.getLogger("spectro.plugin.scheduler")


def _read_observation_list(path) -> list[dict]:
    """Lit le plan d'observation (CSV : Priority, Program, Star, commentaire)."""
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        return [
            {
                "priority": int(row["Priority"].strip()),
                "program": row["Program"].strip(),
                "star": row["Star"].strip(),
            }
            for row in reader
        ]


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
    sont regroupées par (programme, étoile canonique) — une même étoile peut
    être demandée sous plusieurs noms (alias) sur des lignes différentes, ça
    compte comme une seule cible demandée plusieurs fois. Le nombre de fois
    où ce couple a déjà été observé cette session (rosace.db) est comparé au
    nombre de lignes qui le demandent : au-delà, il est considéré consommé et
    n'est plus reproposé. Aucune visibilité astronomique réelle n'est
    vérifiée pour l'instant (hauteur, nuit en cours…) — chantier futur.
    """

    async def run(self, obs: Observation) -> Observation:
        log.info("[SCHEDULER] Sélection de la prochaine cible…")

        rows = _read_observation_list(OBSERVATION_LIST_PATH)

        # Résout chaque ligne et regroupe les lignes valides par (programme, étoile canonique).
        groups: dict[tuple[str, int], dict] = {}
        for row in rows:
            program = await program_get_by_name(row["program"])
            if not program:
                log.warning(f"[SCHEDULER] Programme inconnu, ligne ignorée : {row}")
                continue

            star = await star_get_by_name(row["star"])
            if not star:
                log.warning(f"[SCHEDULER] Étoile inconnue, ligne ignorée : {row}")
                continue

            program_stars = await program_get_stars(program.id)
            matching = next((s for s in program_stars if s.id == star.id), None)
            if matching is None:
                log.warning(
                    f"[SCHEDULER] Étoile '{star.name}' non liée au programme "
                    f"'{program.name}', ligne ignorée"
                )
                continue

            key = (program.name, star.id)
            if key not in groups:
                groups[key] = {
                    "priority": row["priority"],
                    "program": program,
                    "star": star,
                    "queried_name": row["star"],
                    "exposure_time": matching.exposure_time,
                    "count": 0,
                }
            groups[key]["count"] += 1
            groups[key]["priority"] = min(groups[key]["priority"], row["priority"])

        # Priorité croissante (1 = prioritaire) ; à égalité, ordre d'apparition dans le fichier
        # (préservé par le tri stable + l'ordre d'insertion du dict Python).
        for group in sorted(groups.values(), key=lambda g: g["priority"]):
            observed = await db_count_observations_for_target(
                obs.session_id, group["program"].name, group["star"].name
            )
            if observed >= group["count"]:
                continue  # déjà entièrement consommé cette session

            star = group["star"]
            obs.scheduler_data["target"] = Target(
                name=star.name, ra=star.ra, dec=star.dec, magnitude_v=star.magnitude_v,
            )
            obs.scheduler_data["queried_name"] = group["queried_name"]
            obs.scheduler_data["programme"] = group["program"].name
            obs.scheduler_data["exposure"] = ExposureConfig(exposure_time=group["exposure_time"])
            log.info(
                f"[SCHEDULER] Cible sélectionnée : {star.name} "
                f"(demandée sous '{group['queried_name']}') — programme : {group['program'].name}"
            )
            return obs

        log.info("[SCHEDULER] Aucune cible disponible à observer pour l'instant")
        obs.scheduler_data["target"] = None
        return obs

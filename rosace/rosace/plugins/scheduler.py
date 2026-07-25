import logging

from ..models import Observation, Target, ExposureConfig

log = logging.getLogger("spectro.plugin.scheduler")


class SchedulerPlugin:
    """
    Sélectionne la prochaine cible, le programme scientifique à appliquer et
    les conditions d'exposition — c'est le seul plugin qui écrit dans
    scheduler_data.

    Doit toujours répondre rapidement (quelques secondes) : s'il n'y a rien à
    observer dans l'immédiat, il pose target=None plutôt que d'attendre en
    interne — c'est à l'orchestrateur de décider du délai avant de redemander
    (voir SCHEDULER_RETRY_SECONDS dans config.py).

    Version minimale : retourne toujours "Vega" sur le programme etoiles_be.
    Version future : interrogera targets.db (le programme sera alors dérivé
    de la cible choisie via program_stars), et pourra retourner target=None.
    """

    async def run(self, obs: Observation) -> Observation:
        log.info("[SCHEDULER] Sélection de la prochaine cible…")

        # TODO : interroger la base de données des cibles (targets.db)
        obs.scheduler_data["target"] = Target(name="Vega", ra=0.0, dec=0.0)
        obs.scheduler_data["exposure"] = ExposureConfig(exposure_time=300.0)
        obs.scheduler_data["programme"] = "etoiles_be"
        log.info(
            f"[SCHEDULER] Cible sélectionnée : {obs.scheduler_data['target'].name} "
            f"— programme : {obs.scheduler_data['programme']}"
        )
        return obs

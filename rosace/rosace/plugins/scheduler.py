import asyncio
import logging

from ..models import Observation, Target, ExposureConfig

log = logging.getLogger("spectro.plugin.scheduler")


class SchedulerStopRequested(Exception):
    """Levée quand l'arrêt est demandé pendant l'attente du scheduler."""
    pass


class SchedulerPlugin:
    """
    Sélectionne la prochaine cible, le programme scientifique à appliquer et
    les conditions d'exposition — c'est le seul plugin qui écrit dans
    scheduler_data.
    Version minimale : retourne toujours "Vega" sur le programme etoiles_be.
    Version future : interrogera targets.db (le programme sera alors dérivé
    de la cible choisie via program_stars).
    """

    async def run(self, obs: Observation) -> Observation:
        from .. import main as _main  # import tardif pour éviter la circularité
        log.info("[SCHEDULER] Sélection de la prochaine cible…")

        while True:
            if not _main.state.is_running:
                raise SchedulerStopRequested("Arrêt demandé pendant l'attente du scheduler")

            # TODO : interroger la base de données des cibles (targets.db)
            obs.scheduler_data["target"] = Target(name="Vega", ra=0.0, dec=0.0)
            obs.scheduler_data["exposure"] = ExposureConfig(exposure_time=300.0)
            obs.scheduler_data["programme"] = "etoiles_be"
            log.info(
                f"[SCHEDULER] Cible sélectionnée : {obs.scheduler_data['target'].name} "
                f"— programme : {obs.scheduler_data['programme']}"
            )
            return obs

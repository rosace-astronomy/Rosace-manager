import asyncio
import logging

from ..models import SessionContext

log = logging.getLogger("spectro.plugin.scheduler")


class SchedulerStopRequested(Exception):
    """Levée quand l'arrêt est demandé pendant l'attente du scheduler."""
    pass


class SchedulerPlugin:
    """
    Retourne le nom de la prochaine cible à observer.
    Version minimale : retourne toujours "Vega".
    Version future : interrogera la base de données des cibles.
    """

    async def run(self, ctx: SessionContext) -> SessionContext:
        from .. import main as _main  # import tardif pour éviter la circularité
        log.info("[SCHEDULER] Sélection de la prochaine cible…")

        while True:
            if not _main.state.is_running:
                raise SchedulerStopRequested("Arrêt demandé pendant l'attente du scheduler")

            # TODO : interroger la base de données des cibles
            target_name = "Vega"
            log.info(f"[SCHEDULER] Cible sélectionnée : {target_name}")
            ctx.next_target_name = target_name
            ctx.raw_files = []
            ctx.reduced_files = []
            ctx.yaml_file = None
            return ctx

import asyncio
import logging

from ..models import SessionContext

log = logging.getLogger("spectro.plugin.reduction")


class ReductionPlugin:
    """Réduit les fichiers bruts produits par le SEQUENCER."""

    async def run(self, ctx: SessionContext) -> SessionContext:
        if not ctx.raw_files:
            raise RuntimeError("REDUCTION : aucun fichier brut dans le contexte")

        log.info(f"[REDUCTION] Réduction de {len(ctx.raw_files)} fichier(s)")
        for raw in ctx.raw_files:
            reduced = raw.replace(".fits", "_reduced.fits")
            await asyncio.sleep(0.5)  # TODO : pipeline de réduction
            ctx.reduced_files.append(reduced)
            log.info(f"[REDUCTION] {raw} → {reduced}")

        return ctx

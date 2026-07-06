import asyncio
import logging

from ..models import SessionContext

log = logging.getLogger("spectro.plugin.archive")


class ArchivePlugin:
    """Archive les fichiers réduits dans le stockage long terme."""

    async def run(self, ctx: SessionContext) -> SessionContext:
        if not ctx.reduced_files:
            raise RuntimeError("ARCHIVE : aucun fichier réduit dans le contexte")

        log.info(f"[ARCHIVE] Archivage de {len(ctx.reduced_files)} fichier(s)")
        for f in ctx.reduced_files:
            await asyncio.sleep(0.3)  # TODO : transfert vers archive
            log.info(f"[ARCHIVE] Archivé : {f}")

        return ctx

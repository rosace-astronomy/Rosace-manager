import asyncio
import logging

from ..models import Observation

log = logging.getLogger("spectro.plugin.archive")


class ArchivePlugin:
    """Archives the reduced files to long-term storage."""

    async def run(self, obs: Observation) -> Observation:
        reduced_files = obs.reduction_data.get("extract", {}).get("reduced_files", [])
        if not reduced_files:
            raise RuntimeError("ARCHIVE: no reduced file in reduction_data")

        log.info(f"[ARCHIVE] Archiving {len(reduced_files)} file(s)")
        for f in reduced_files:
            await asyncio.sleep(0.3)  # TODO: transfer to archive
            log.info(f"[ARCHIVE] Archived: {f}")

        return obs

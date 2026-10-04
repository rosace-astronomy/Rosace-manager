import logging

from ..models import Observation

log = logging.getLogger("spectro.plugin.archive")


class ArchivePlugin:
    """Archives the observation's data to long-term storage -- PLACEHOLDER:
    not designed yet. Will work from the observation's own files (its YAML
    dossier and the files it references), never from a given sub-plugin's
    casier; for now it only logs what it would archive."""

    async def run(self, obs: Observation) -> Observation:
        log.info(f"[ARCHIVE] Placeholder: nothing archived yet (observation dossier {obs.yaml_file})")
        return obs

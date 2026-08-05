"""
Plugin SHARING — Orchestrateur de diffusion.
Le sous-plugin de diffusion (un seul, pas une liste) est chargé dynamiquement
depuis le YAML du programme scientifique de l'observation
(obs.scheduler_data["program"]), posé par SCHEDULER — donc rechargé à
chaque observation, pas une fois pour toutes au démarrage.

Structure :
    programs/<program>.yaml  →  pipeline_loader  →  <UnSeulSousPlugin>
"""

import logging

from ..models import Observation
from .pipeline_loader import load_sharing_plugin

log = logging.getLogger("spectro.plugin.sharing")


class SharingPlugin:
    """
    Orchestre la diffusion des résultats en délégant au sous-plugin déclaré
    dans le YAML du programme de l'observation en cours.
    """

    async def run(self, obs: Observation) -> Observation:
        program = obs.scheduler_data.get("program")
        if not program:
            raise RuntimeError("SHARING : program manquant dans scheduler_data")

        sub = load_sharing_plugin(program)
        log.info(f"[SHARING] Diffusion via '{sub.__class__.__name__}'")
        obs = await sub.execute(obs)
        return obs

"""
Plugin REDUCTION — Orchestrateur de la chaîne de réduction.
Le pipeline (liste de sous-plugins) est chargé dynamiquement depuis le YAML
du programme scientifique de l'observation (obs.scheduler_data["programme"]),
posé par SCHEDULER — donc rechargé à chaque observation, pas une fois pour
toutes au démarrage.

Structure :
    programmes/<programme>.yaml  →  pipeline_loader  →  [Bias, Dark, Flat, Extract, …]
"""

import logging

from ..models import Observation
from .pipeline_loader import load_pipelines

log = logging.getLogger("spectro.plugin.reduction")


class ReductionPlugin:
    """
    Orchestre la chaîne de réduction en enchaînant les sous-plugins
    déclarés dans le YAML du programme de l'observation en cours.
    """

    async def run(self, obs: Observation) -> Observation:
        raw_files = obs.sequencer_data.get("raw_files")
        if not raw_files:
            raise RuntimeError("REDUCTION : aucun fichier brut dans sequencer_data (raw_files vide)")

        programme = obs.scheduler_data.get("programme")
        if not programme:
            raise RuntimeError("REDUCTION : programme manquant dans scheduler_data")

        _, red_pipeline = load_pipelines(programme)
        steps = [s.__class__.__name__ for s in red_pipeline]
        log.info(
            f"[REDUCTION] Début réduction '{programme}' — "
            f"{len(raw_files)} fichier(s) brut(s), étapes : {steps}"
        )

        # Chaque sous-plugin lit/écrit directement dans obs.reduction_data
        # (et lit obs.sequencer_data["raw_files"] posé par SEQUENCER en entrée)
        for sub in red_pipeline:
            step = sub.__class__.__name__
            log.info(f"[REDUCTION] → {step}")
            try:
                obs = await sub.execute(obs)
            except Exception as e:
                log.error(f"[REDUCTION] Échec à l'étape {step} : {e}")
                raise

        log.info(
            f"[REDUCTION] Réduction terminée — "
            f"{len(obs.reduction_data.get('reduced_files', []))} spectre(s) produit(s)"
        )
        return obs

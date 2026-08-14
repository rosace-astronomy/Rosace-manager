"""
Plugin REDUCTION — Orchestrateur de la chaîne de réduction.
Le pipeline (liste de sous-plugins) est chargé dynamiquement depuis le YAML
du programme scientifique de l'observation (obs.scheduler_data["program"]),
posé par SCHEDULER — donc rechargé à chaque observation, pas une fois pour
toutes au démarrage.

Structure :
    programs/<program>.yaml  →  pipeline_loader  →  [Bias, Dark, Flat, Extract, …]
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
        raw_files = obs.sequencer_data.get("star_spectra_images")
        if not raw_files:
            raise RuntimeError("REDUCTION : aucun fichier brut (sequencer_data['star_spectra_images'] vide)")

        program = obs.scheduler_data.get("program")
        if not program:
            raise RuntimeError("REDUCTION : program manquant dans scheduler_data")

        _, red_pipeline = load_pipelines(program)
        log.info(
            f"[REDUCTION] Début réduction '{program}' — "
            f"{len(raw_files)} fichier(s) brut(s)"
        )

        # Chaque sous-plugin lit/écrit directement dans obs.reduction_data
        # (et lit obs.sequencer_data["star_spectra_images"] posé par SEQUENCER en entrée —
        # une clé partagée, pas le nom du sous-plugin producteur)
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
            f"{len(obs.reduction_data.get('extract', {}).get('reduced_files', []))} spectre(s) produit(s)"
        )
        return obs

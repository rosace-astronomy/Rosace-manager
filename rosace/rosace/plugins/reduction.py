"""
Plugin REDUCTION — Orchestrateur de la chaîne de réduction.
Le pipeline (liste de sous-plugins) est chargé dynamiquement depuis le YAML
du programme scientifique actif défini dans config.py (ACTIVE_PROGRAMME).

Structure :
    programmes/<programme>.yaml  →  pipeline_loader  →  [Bias, Dark, Flat, Extract, …]
"""

import logging

from ..config import ACTIVE_PROGRAMME
from ..models import ObservationContext
from .pipeline_loader import load_pipelines

log = logging.getLogger("spectro.plugin.reduction")


class ReductionPlugin:
    """
    Orchestre la chaîne de réduction en enchaînant les sous-plugins
    déclarés dans le YAML du programme actif.
    """

    def __init__(self):
        _, red_pipeline = load_pipelines(ACTIVE_PROGRAMME)
        self.pipeline = red_pipeline
        steps = [s.__class__.__name__ for s in self.pipeline]
        log.info(f"[REDUCTION] Programme '{ACTIVE_PROGRAMME}' — pipeline : {steps}")

    async def run(self, obs_ctx: ObservationContext) -> ObservationContext:
        if not obs_ctx.raw_files:
            raise RuntimeError("REDUCTION : aucun fichier brut dans le contexte (raw_files vide)")

        steps = [s.__class__.__name__ for s in self.pipeline]
        log.info(
            f"[REDUCTION] Début réduction '{ACTIVE_PROGRAMME}' — "
            f"{len(obs_ctx.raw_files)} fichier(s) brut(s), étapes : {steps}"
        )

        # Initialisation du contexte de travail local
        work_ctx: dict = {
            "raw_files": list(obs_ctx.raw_files),
            "obs_dir":   obs_ctx.obs_dir,
        }

        # Exécution séquentielle
        for sub in self.pipeline:
            step = sub.__class__.__name__
            log.info(f"[REDUCTION] → {step}")
            try:
                work_ctx = await sub.execute(work_ctx)
            except Exception as e:
                log.error(f"[REDUCTION] Échec à l'étape {step} : {e}")
                raise

        # Propagation des résultats vers l'ObservationContext
        obs_ctx.reduced_files = work_ctx.get("reduced_files", [])
        log.info(f"[REDUCTION] Réduction terminée — {len(obs_ctx.reduced_files)} spectre(s) produit(s)")
        return obs_ctx

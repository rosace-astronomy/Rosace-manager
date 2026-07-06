"""
Plugin SEQUENCER — Orchestrateur d'acquisition.
Le pipeline (liste de sous-plugins) est chargé dynamiquement depuis le YAML
du programme scientifique actif défini dans config.py (ACTIVE_PROGRAMME).

Structure :
    programmes/<programme>.yaml  →  pipeline_loader  →  [Slew, Focus, Expose, …]
"""

import logging

from ..config import ACTIVE_PROGRAMME
from ..models import SessionContext
from .pipeline_loader import load_pipelines

log = logging.getLogger("spectro.plugin.sequencer")


class SequencerPlugin:
    """
    Orchestre la séquence d'acquisition en enchaînant les sous-plugins
    déclarés dans le YAML du programme actif.
    """

    def __init__(self):
        seq_pipeline, _ = load_pipelines(ACTIVE_PROGRAMME)
        self.pipeline = seq_pipeline
        steps = [s.__class__.__name__ for s in self.pipeline]
        log.info(f"[SEQUENCER] Programme '{ACTIVE_PROGRAMME}' — pipeline : {steps}")

    async def run(self, ctx: SessionContext) -> SessionContext:
        obs = ctx.current_observation
        if not obs:
            raise RuntimeError("SEQUENCER : aucune observation dans le contexte")
        if not ctx.obs_dir:
            raise RuntimeError("SEQUENCER : répertoire d'observation (obs_dir) non défini")

        steps = [s.__class__.__name__ for s in self.pipeline]
        log.info(
            f"[SEQUENCER] Début séquence '{ACTIVE_PROGRAMME}' pour {obs.request.target.name} "
            f"— étapes : {steps}"
        )

        # Contexte de travail local transmis d'un sous-plugin à l'autre
        work_ctx: dict = {
            "target_name":   obs.request.target.name,
            "ra":            obs.request.target.ra,
            "dec":           obs.request.target.dec,
            "obs_dir":       ctx.obs_dir,
            "observation":   obs,
            "exposure_time": obs.request.exposure.exposure_time,
            "num_exposures": obs.request.exposure.num_exposures,
            "binning":       obs.request.exposure.binning,
            "grating":       obs.request.exposure.grating,
        }

        # Exécution séquentielle
        for sub in self.pipeline:
            step = sub.__class__.__name__
            log.info(f"[SEQUENCER] → {step}")
            try:
                work_ctx = await sub.execute(work_ctx)
            except Exception as e:
                log.error(f"[SEQUENCER] Échec à l'étape {step} : {e}")
                raise

        # Propagation des résultats vers le SessionContext
        ctx.raw_files = work_ctx.get("raw_files", [])
        log.info(f"[SEQUENCER] Séquence terminée — {len(ctx.raw_files)} fichier(s) brut(s)")
        return ctx

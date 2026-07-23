"""
Plugin SEQUENCER — Orchestrateur d'acquisition.
Le pipeline (liste de sous-plugins) est chargé dynamiquement depuis le YAML
du programme scientifique de l'observation (obs.scheduler_data["programme"]),
posé par SCHEDULER — donc rechargé à chaque observation, pas une fois pour
toutes au démarrage.

Structure :
    programmes/<programme>.yaml  →  pipeline_loader  →  [Slew, Focus, Expose, …]
"""

import logging

from ..models import Observation
from .pipeline_loader import load_pipelines

log = logging.getLogger("spectro.plugin.sequencer")


class SequencerPlugin:
    """
    Orchestre la séquence d'acquisition en enchaînant les sous-plugins
    déclarés dans le YAML du programme de l'observation en cours.
    """

    async def run(self, obs: Observation) -> Observation:
        programme = obs.scheduler_data.get("programme")
        if not programme or "target" not in obs.scheduler_data:
            raise RuntimeError("SEQUENCER : scheduler_data incomplet (target/programme)")
        if not obs.obs_dir:
            raise RuntimeError("SEQUENCER : répertoire d'observation (obs_dir) non défini")

        seq_pipeline, _ = load_pipelines(programme)
        steps = [s.__class__.__name__ for s in seq_pipeline]
        log.info(
            f"[SEQUENCER] Début séquence '{programme}' pour {obs.scheduler_data['target'].name} "
            f"— étapes : {steps}"
        )

        # Chaque sous-plugin lit/écrit directement dans obs.sequencer_data
        for sub in seq_pipeline:
            step = sub.__class__.__name__
            log.info(f"[SEQUENCER] → {step}")
            try:
                obs = await sub.execute(obs)
            except Exception as e:
                log.error(f"[SEQUENCER] Échec à l'étape {step} : {e}")
                raise

        log.info(
            f"[SEQUENCER] Séquence terminée — "
            f"{len(obs.sequencer_data.get('raw_files', []))} fichier(s) brut(s)"
        )
        return obs

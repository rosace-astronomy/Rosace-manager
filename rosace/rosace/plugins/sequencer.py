"""
Plugin SEQUENCER — Orchestrateur d'acquisition.
Le pipeline (liste de sous-plugins) est chargé dynamiquement depuis le YAML
du programme scientifique de l'observation (obs.scheduler_data["program"]),
posé par SCHEDULER — donc rechargé à chaque observation, pas une fois pour
toutes au démarrage.

Structure :
    programs/<program>.yaml  →  pipeline_loader  →  [sous-plugin, sous-plugin, …]
"""

import logging

from ..models import Observation
from .pipeline_loader import load_pipelines, load_program_yaml

log = logging.getLogger("spectro.plugin.sequencer")


class SequencerPlugin:
    """
    Orchestre la séquence d'acquisition en enchaînant les sous-plugins
    déclarés dans le YAML du programme de l'observation en cours.
    """

    async def run(self, obs: Observation) -> Observation:
        program = obs.scheduler_data.get("program")
        if not program or "target" not in obs.scheduler_data:
            raise RuntimeError("SEQUENCER : scheduler_data incomplet (target/program)")
        if not obs.obs_dir:
            raise RuntimeError("SEQUENCER : répertoire d'observation (obs_dir) non défini")

        # Manifeste posé en tout premier, avant tout sous-plugin — survit à un
        # plantage n'importe où dans SEQUENCER, REDUCTION ou SHARING (voir models.py).
        program_data = load_program_yaml(program)
        obs.program_file = f"{program}.yaml"
        obs.program_steps = {
            "sequencer": list(program_data["sequencer"]),
            "reduction": list(program_data["reduction"]),
            "sharing": program_data["sharing"],
        }

        seq_pipeline, _ = load_pipelines(program)
        steps = [s.__class__.__name__ for s in seq_pipeline]
        log.info(
            f"[SEQUENCER] Début séquence '{program}' pour {obs.scheduler_data.get('queried_name')} "
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
            f"{len(obs.sequencer_data.get('star_spectra_images', []))} fichier(s) brut(s)"
        )
        return obs

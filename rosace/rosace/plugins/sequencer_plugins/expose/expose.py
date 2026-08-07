"""
Sous-plugin SEQUENCER — Exposition CCD
Backend "indi" — appelle réellement le hardware via obs.hardware["science_camera"]
(voir hardware/camera.py pour le contrat générique, hardware_indi/adapters/camera.py
pour l'implémentation INDI). Ne vérifie plus qu'un pointage a eu lieu (plus de
clé "slew" en dur) : si EXPOSE est appelé, c'est que le programme YAML l'a
placé après une étape de pointage qui a réussi — le YAML est déjà la source
de vérité pour l'ordre des sous-plugins, pas la peine de le re-vérifier ici.
N'honore toujours pas exposure.num_exposures explicite — à enrichir plus tard.
Vérifie obs.stop_check() entre deux sous-poses (pas pendant une pose déjà
lancée sur la caméra — pas d'abort matériel réel ici) pour qu'un arrêt
demandé pendant une exposition à plusieurs sous-poses ne force pas à
attendre la fin de toutes les sous-poses restantes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ....config import EXPOSURE_TIME_MAG0_SECONDS, MAX_EXPOSURE_TIME_SECONDS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import build_fits_root_name, compute_exposure_time_and_count

log = logging.getLogger("spectro.subplugin.expose")


class ExposePlugin(SubPlugin):
    """Réalise une exposition spectrale via le hardware INDI réel."""

    async def execute(self, obs: Observation) -> Observation:
        if not obs.obs_dir:
            raise RuntimeError("EXPOSE : répertoire d'observation (obs_dir) non défini")

        exposure = obs.scheduler_data.get("exposure")
        if not exposure:
            raise RuntimeError("EXPOSE : exposure manquant dans scheduler_data")

        if exposure.exposure_time is not None:
            exposure_time, count = exposure.exposure_time, 1
        else:
            target = obs.scheduler_data.get("target")
            if not target or target.magnitude_v is None:
                raise RuntimeError(
                    "EXPOSE : exposure_time non défini et magnitude absente "
                    "(scheduler_data['target'].magnitude_v) — impossible de le calculer"
                )
            exposure_time, count = compute_exposure_time_and_count(
                target.magnitude_v, EXPOSURE_TIME_MAG0_SECONDS, MAX_EXPOSURE_TIME_SECONDS
            )

        log.info(f"[EXPOSE] {count} exposition(s) de {exposure_time}s")
        camera = obs.hardware["science_camera"]

        queried_name = obs.scheduler_data.get("queried_name")
        root_name = build_fits_root_name(
            "STAR", target_name=queried_name, exposure_time=exposure_time, num_exposures=count
        )

        raw_files = []
        for i in range(1, count + 1):
            image = await camera.expose(exposure_time)
            suffix = f"-{i}" if count > 1 else ""
            raw_path = Path(obs.obs_dir) / f"{root_name}{suffix}.fits"
            image.writeto(raw_path, overwrite=True)
            raw_files.append(str(raw_path))

            if i < count and obs.stop_check and obs.stop_check():
                log.info(f"[EXPOSE] Arrêt demandé — {i}/{count} sous-pose(s) faite(s), interruption")
                break

        obs.sequencer_data["expose"] = {"raw_files": raw_files, "count": len(raw_files)}
        log.info(f"[EXPOSE] Exposition(s) terminée(s) — {raw_files}")
        return obs

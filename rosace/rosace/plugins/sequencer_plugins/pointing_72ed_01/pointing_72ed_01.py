"""
Sous-plugin SEQUENCER — Pointage télescope 72ED + démarrage du guidage PHD2
Backend "indi" uniquement — première version "mode simple" (voir CLAUDE.md /
memory : un mode "complexe" est prévu plus tard, sous forme d'un autre
sous-plugin, ex. pointing_72ed_02).

Pointe la monture via obs.hardware["mount"], capture une image de guidage
via obs.hardware["guiding_camera"] (fonction générique
acquisition_helpers.capture_guide_image) pour vérifier visuellement le
pointage, puis démarre le guidage PHD2 via obs.hardware["phd2"]
(phd2.Phd2Client.start_guiding()) — qui ne rend la main qu'une fois le
guidage réellement stabilisé (SettleDone), garantissant que la cible est
bien en position avant que EXPOSE ne commence les acquisitions. Le guidage
ainsi démarré continue de tourner pendant les étapes suivantes (acquisition
d'images...) — aucun sous-plugin ne l'arrête encore explicitement (chantier
futur : "stop guidage").
"""

from __future__ import annotations

import logging

from ....config import GUIDE_EXPOSURE_TIME_SECONDS
from ....models import Observation
from ...sub_plugin_base import SubPlugin
from ..acquisition_helpers import capture_guide_image

log = logging.getLogger("spectro.subplugin.pointing_72ed_01")


class Pointing72ed01Plugin(SubPlugin):
    """Pointe le télescope 72ED et capture une image de guidage pour vérification."""

    async def execute(self, obs: Observation) -> Observation:
        target = obs.scheduler_data.get("target")
        if not target:
            raise RuntimeError("POINTING_72ED_01 : target manquant dans scheduler_data")

        if not obs.obs_dir:
            raise RuntimeError("POINTING_72ED_01 : répertoire d'observation (obs_dir) non défini")

        log.info(f"[POINTING_72ED_01] Pointage vers {target.name}")
        mount = obs.hardware["mount"]
        await mount.slew_to(target.ra, target.dec)

        guiding_camera = obs.hardware["guiding_camera"]
        guide_image_path = await capture_guide_image(
            guiding_camera, GUIDE_EXPOSURE_TIME_SECONDS, obs.obs_dir,
        )

        log.info(f"[POINTING_72ED_01] Démarrage du guidage PHD2 sur {target.name}")
        phd2 = obs.hardware["phd2"]
        await phd2.start_guiding()

        obs.sequencer_data["pointing_72ed_01"] = {
            "target": target.name,
            "guide_image": str(guide_image_path),
            "guiding_started": True,
        }
        log.info(
            f"[POINTING_72ED_01] Télescope en position sur {target.name}, "
            f"image de guidage : {guide_image_path}, guidage démarré et stabilisé"
        )
        return obs

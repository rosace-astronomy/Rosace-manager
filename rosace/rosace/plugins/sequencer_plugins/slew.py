"""
Sous-plugin SEQUENCER — Étape 1 : Pointage (Slew)
Pointe le télescope vers les coordonnées RA/Dec de la cible.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.slew")


class SlewPlugin(SubPlugin):
    """Pointe le télescope vers la cible (RA/Dec)."""

    CONFIG_FILE = "config.yaml"

    def _config_relative_path(self) -> str:
        return Path(__file__).with_name("config.yaml").relative_to(
            Path(__file__).parents[2]
        ).as_posix()

    async def execute(self, context: dict) -> dict:
        target = context.get("target_name", "inconnue")
        ra = context.get("ra", 0.0)
        dec = context.get("dec", 0.0)

        slew_speed = self.config.get("slew_speed_deg_per_sec", 2.0)
        settle_time = self.config.get("settle_time_sec", 3.0)
        simulate = self.config.get("simulate", True)

        log.info(f"[SLEW] Pointage vers {target} RA={ra:.4f}° Dec={dec:.4f}°")

        if simulate:
            # Simulation : durée proportionnelle à la distance angulaire (placeholder)
            slew_duration = min(settle_time, 30.0 / slew_speed)
            await asyncio.sleep(slew_duration * 0.1)  # accéléré en simulation
            log.info(f"[SLEW] Simulation — pointage terminé en {slew_duration:.1f}s (accéléré)")
        else:
            # TODO : commande INDI/ASCOM réelle
            # await telescope.slew_to(ra=ra, dec=dec)
            await asyncio.sleep(settle_time * 0.1)

        context["slew_done"] = True
        context["slew_target"] = target
        log.info(f"[SLEW] Télescope en position sur {target}")
        return context

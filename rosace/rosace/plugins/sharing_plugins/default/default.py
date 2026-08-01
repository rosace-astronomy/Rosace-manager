"""
Sous-plugin SHARING — diffusion générique (placeholder).
PLACEHOLDER : la vraie logique de diffusion (email, FTP, API...), propre à
chaque programme, sera conçue lors d'un chantier séparé. Pour l'instant, se
contente de journaliser les fichiers réduits.
"""

from __future__ import annotations

import asyncio
import logging

from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.sharing.default")


class DefaultPlugin(SubPlugin):
    """Diffusion générique des fichiers réduits — placeholder, pas encore implémenté."""

    async def execute(self, obs: Observation) -> Observation:
        reduced_files = obs.reduction_data.get("extract", {}).get("reduced_files", [])
        if not reduced_files:
            raise RuntimeError("SHARING : aucun fichier réduit (reduction_data['extract']['reduced_files'] vide)")

        log.info(f"[SHARING] Diffusion de {len(reduced_files)} fichier(s)")
        for f in reduced_files:
            await asyncio.sleep(0.3)  # TODO : envoi email / API réel
            log.info(f"[SHARING] Diffusé : {f}")

        obs.sharing_data["default"] = {"shared_files": reduced_files}
        return obs

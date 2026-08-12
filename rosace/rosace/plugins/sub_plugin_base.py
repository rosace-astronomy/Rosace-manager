"""
Classe de base commune à tous les sous-plugins (sequencer et reduction).
Chaque sous-plugin hérite de SubPlugin et implémente `execute()`.
"""

from __future__ import annotations

import logging

from ..models import Observation


class SubPlugin:
    """
    Classe de base pour les sous-plugins de séquençage et de réduction.

    Chaque sous-plugin dispose :
    - d'un nom (utilisé dans les logs)
    - d'une méthode `execute(obs: Observation) -> Observation` à implémenter

    Le sous-plugin lit/écrit dans le casier de son plugin de stage parent
    (obs.sequencer_data ou obs.reduction_data), et peut lire celui d'une
    étape précédente si besoin (ex. bias lit obs.sequencer_data["star_spectra_images"]
    — une clé partagée, écrite sous un nom fixe indépendant du sous-plugin
    SEQUENCER qui l'a produite, pas obs.sequencer_data["<nom_du_sous_plugin>"]
    : REDUCTION ne doit jamais avoir besoin de connaître ce nom).

    Toute configuration vit dans conf/ (config.yaml, hardware_<variant>.yaml)
    comme le reste de l'application — pas de config.yaml propre au sous-plugin.
    """

    def __init__(self) -> None:
        self.name = self.__class__.__name__
        self.log = logging.getLogger(f"spectro.subplugin.{self.name.lower()}")

    async def execute(self, obs: Observation) -> Observation:
        """
        Logique du sous-plugin. Reçoit le dossier d'observation courant,
        retourne le dossier enrichi.
        Toute exception remonte à l'orchestrateur du plugin parent.
        """
        raise NotImplementedError(f"{self.name} doit implémenter execute()")

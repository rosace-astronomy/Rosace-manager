"""
Classe de base commune à tous les sous-plugins (sequencer et reduction).
Chaque sous-plugin hérite de SubPlugin et implémente `execute()`.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any

import yaml

from ..models import Observation


class SubPlugin:
    """
    Classe de base pour les sous-plugins de séquençage et de réduction.

    Chaque sous-plugin dispose :
    - d'un nom (utilisé dans les logs)
    - d'une configuration chargée depuis config.yaml au premier accès (paresseuse)
    - d'une méthode `execute(obs: Observation) -> Observation` à implémenter

    Le sous-plugin lit/écrit dans le casier de son plugin de stage parent
    (obs.sequencer_data ou obs.reduction_data), et peut lire celui d'une
    étape précédente si besoin (ex. extract lit obs.sequencer_data["raw_files"]).
    """

    #: Surcharger dans la sous-classe pour pointer vers le bon config.yaml
    CONFIG_FILE: str = "config.yaml"

    def __init__(self) -> None:
        self.name = self.__class__.__name__
        self.log = logging.getLogger(f"spectro.subplugin.{self.name.lower()}")
        self._config: dict[str, Any] | None = None

    @property
    def config(self) -> dict[str, Any]:
        """Configuration du sous-plugin, chargée depuis config.yaml au premier accès."""
        if self._config is None:
            self._config = self._load_config()
        return self._config

    def _load_config(self) -> dict[str, Any]:
        """Charge config.yaml depuis le répertoire du sous-plugin."""
        config_path = Path(inspect.getfile(type(self))).parent / self._config_relative_path()
        if not config_path.exists():
            self.log.debug(f"[{self.name}] Pas de config.yaml — valeurs par défaut utilisées")
            return {}
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        self.log.debug(f"[{self.name}] Config chargée : {data}")
        return data

    def _config_relative_path(self) -> str:
        """
        Surcharger si le config.yaml n'est pas dans le même répertoire
        que le script. Par défaut : même dossier que le fichier .py du plugin.
        """
        return self.CONFIG_FILE

    async def execute(self, obs: Observation) -> Observation:
        """
        Logique du sous-plugin. Reçoit le dossier d'observation courant,
        retourne le dossier enrichi.
        Toute exception remonte à l'orchestrateur du plugin parent.
        """
        raise NotImplementedError(f"{self.name} doit implémenter execute()")

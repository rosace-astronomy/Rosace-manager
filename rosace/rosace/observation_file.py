"""
Persistance de l'observation sur disque, au format YAML.
Joue pour le fichier YAML le même rôle que database.py pour SQLite :
un module dédié à la persistance, séparé de l'orchestrateur (main.py)
et du modèle de données pur (models.py).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .models import Observation

log = logging.getLogger("spectro.observation_file")


def create_observation_file(obs: Observation, target_dir: Path) -> Path:
    """
    Détermine le nom de fichier YAML de l'observation (obs.suggested_filename)
    et l'enregistre sur obs.yaml_file. Le contenu est écrit par
    write_observation_file(), appelée juste après par l'appelant.
    """
    filepath = target_dir / obs.suggested_filename
    obs.yaml_file = str(filepath)
    log.info(f"[OBSERVATION_FILE] Fichier d'observation créé : {filepath}")
    return filepath


def write_observation_file(obs: Observation) -> None:
    """
    Réécrit le fichier YAML de l'observation avec son état courant complet.
    Appelée après chaque étape (SCHEDULER → ARCHIVE) : le fichier ne fait que
    grandir/se compléter, jusqu'à devenir la mémoire exhaustive de
    l'observation — utile en cas de plantage pour savoir où ça a coincé.
    Ne fait rien tant que create_observation_file() n'a pas encore fixé
    obs.yaml_file.
    """
    if not obs.yaml_file:
        return
    content = obs.model_dump(mode="json")
    with open(obs.yaml_file, "w", encoding="utf-8") as f:
        yaml.dump(content, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

"""
Chargeur de pipeline dynamique.

Lit un fichier YAML de programme scientifique et instancie les sous-plugins
dans l'ordre déclaré, pour le séquenceur et pour la réduction.

Structure attendue du YAML :
    programme: nom_du_programme
    sequencer:
      - slew
      - focus
      - expose
    reduction:
      - bias
      - dark
      - flat
      - extract

Usage :
    from .pipeline_loader import load_pipelines
    seq_pipeline, red_pipeline = load_pipelines()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from ..config import SEQUENCER_BACKEND

log = logging.getLogger("spectro.pipeline_loader")

# ── Répertoire contenant les fichiers de programme ────────────────────────────
PROGRAMMES_DIR = Path(__file__).parents[1] / "programmes"

# ── Registre de tous les sous-plugins disponibles ─────────────────────────────
# Clé : nom déclaré dans le YAML  →  Valeur : classe du sous-plugin
# Ajouter ici tout nouveau sous-plugin pour le rendre utilisable depuis un YAML.
# Les sous-plugins SEQUENCER ont deux implémentations (internal/indi), choisies
# selon SEQUENCER_BACKEND — REDUCTION n'a pas de dépendance matérielle, une
# seule implémentation.

def _build_registry() -> dict[str, Any]:
    """Construit le registre en important les classes à la demande."""
    if SEQUENCER_BACKEND == "internal":
        from .sequencer_plugins.internal.slew   import SlewPlugin
        from .sequencer_plugins.internal.focus  import FocusPlugin
        from .sequencer_plugins.internal.expose import ExposePlugin
    elif SEQUENCER_BACKEND == "indi":
        from .sequencer_plugins.indi.slew   import SlewPlugin
        from .sequencer_plugins.indi.focus  import FocusPlugin
        from .sequencer_plugins.indi.expose import ExposePlugin
    else:
        raise ValueError(
            f"sequencer_backend={SEQUENCER_BACKEND!r} inconnu (attendu : 'internal' ou 'indi')"
        )

    from .reduction_plugins.bias    import BiasPlugin
    from .reduction_plugins.dark    import DarkPlugin
    from .reduction_plugins.flat    import FlatPlugin
    from .reduction_plugins.extract import ExtractPlugin

    return {
        # Séquenceur
        "slew":    SlewPlugin,
        "focus":   FocusPlugin,
        "expose":  ExposePlugin,
        # Réduction
        "bias":    BiasPlugin,
        "dark":    DarkPlugin,
        "flat":    FlatPlugin,
        "extract": ExtractPlugin,
    }


# ── Fonctions publiques ────────────────────────────────────────────────────────

def list_programmes() -> list[str]:
    """Retourne les noms des programmes disponibles (sans extension)."""
    return sorted(p.stem for p in PROGRAMMES_DIR.glob("*.yaml"))


def load_programme_yaml(programme_name: str) -> dict:
    """
    Charge et retourne le contenu brut du YAML d'un programme.
    Lève FileNotFoundError si le fichier n'existe pas.
    """
    path = PROGRAMMES_DIR / f"{programme_name}.yaml"
    if not path.exists():
        available = list_programmes()
        raise FileNotFoundError(
            f"Programme '{programme_name}' introuvable dans {PROGRAMMES_DIR}. "
            f"Programmes disponibles : {available}"
        )
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_pipeline(step_names: list[str], registry: dict) -> list:
    """
    Instancie et retourne la liste ordonnée de sous-plugins
    à partir d'une liste de noms et d'un registre de classes.

    Lève ValueError si un nom est inconnu.
    """
    pipeline = []
    for name in step_names:
        name = name.strip().lower()
        if name not in registry:
            raise ValueError(
                f"Sous-plugin inconnu : '{name}'. "
                f"Noms valides : {sorted(registry.keys())}"
            )
        plugin_instance = registry[name]()
        pipeline.append(plugin_instance)
        log.debug(f"[PIPELINE] Sous-plugin chargé : {name} → {plugin_instance.__class__.__name__}")
    return pipeline


def load_pipelines(programme_name: str) -> tuple[list, list]:
    """
    Charge le programme YAML et retourne (sequencer_pipeline, reduction_pipeline).

    :param programme_name: Nom du fichier YAML sans extension (ex. 'etoiles_be')
    :returns: (liste de sous-plugins séquenceur, liste de sous-plugins réduction)
    :raises FileNotFoundError: Programme YAML introuvable
    :raises ValueError: Sous-plugin déclaré dans le YAML mais absent du registre
    :raises KeyError: Clés 'sequencer' ou 'reduction' manquantes dans le YAML
    """
    registry = _build_registry()
    data = load_programme_yaml(programme_name)

    if "sequencer" not in data:
        raise KeyError(f"Clé 'sequencer' manquante dans le programme '{programme_name}'")
    if "reduction" not in data:
        raise KeyError(f"Clé 'reduction' manquante dans le programme '{programme_name}'")

    seq_steps = data["sequencer"]
    red_steps = data["reduction"]

    log.info(
        f"[PIPELINE] Programme '{programme_name}' chargé — "
        f"séquenceur : {seq_steps}, réduction : {red_steps}"
    )

    seq_pipeline = build_pipeline(seq_steps, registry)
    red_pipeline = build_pipeline(red_steps, registry)

    return seq_pipeline, red_pipeline

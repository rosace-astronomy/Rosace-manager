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
    sharing: default

Usage :
    from .pipeline_loader import load_pipelines, load_sharing_plugin
    seq_pipeline, red_pipeline = load_pipelines(programme)
    sharing_plugin = load_sharing_plugin(programme)

Chargement dynamique par convention de nommage — pas de registre central à
maintenir (contrairement à l'ancienne approche) : un sous-plugin déclaré
"<name>" dans le YAML vit dans son propre répertoire, un par sous-plugin
(<name>/<name>.py, classe <Name>Plugin). Seuls les sous-plugins réellement
déclarés dans le programme en cours sont importés — pas tout le catalogue.

Seul SEQUENCER a un axe backend (SEQUENCER_BACKEND, config.py) : le nom nu
(<name>.py) est la version canonique — utilisée pour REDUCTION (qui n'a pas
de dépendance matérielle) et pour SEQUENCER quand SEQUENCER_BACKEND="indi".
"internal" est une exception ponctuelle réservée à la démo (pas un backend
de plein droit) : seuls les sous-plugins réellement nécessaires à la démo
ont un fichier <name>_internal.py — son absence pour un sous-plugin donné
est normale, pas un manque.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

import yaml

from ..config import SEQUENCER_BACKEND

log = logging.getLogger("spectro.pipeline_loader")

# ── Répertoire contenant les fichiers de programme ────────────────────────────
PROGRAMMES_DIR = Path(__file__).parents[1] / "programmes"


def _resolve_module_and_class(kind: str, name: str) -> tuple[str, str]:
    """
    Détermine le chemin de module et le nom de classe attendus pour un
    sous-plugin déclaré "name" dans un programme, selon "kind" ('sequencer',
    'reduction' ou 'sharing') et, pour 'sequencer' uniquement, SEQUENCER_BACKEND.
    """
    class_name = f"{name.capitalize()}Plugin"

    if kind == "sequencer":
        if SEQUENCER_BACKEND == "internal":
            module_path = f"rosace.plugins.sequencer_plugins.{name}.{name}_internal"
        elif SEQUENCER_BACKEND == "indi":
            module_path = f"rosace.plugins.sequencer_plugins.{name}.{name}"
        else:
            raise ValueError(
                f"sequencer_backend={SEQUENCER_BACKEND!r} inconnu (attendu : 'internal' ou 'indi')"
            )
    elif kind == "reduction":
        module_path = f"rosace.plugins.reduction_plugins.{name}.{name}"
    elif kind == "sharing":
        module_path = f"rosace.plugins.sharing_plugins.{name}.{name}"
    else:
        raise ValueError(f"kind={kind!r} inconnu (attendu : 'sequencer', 'reduction' ou 'sharing')")

    return module_path, class_name


def _load_subplugin_class(kind: str, name: str) -> type:
    """Importe dynamiquement le module du sous-plugin et retourne sa classe."""
    module_path, class_name = _resolve_module_and_class(kind, name)

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ValueError(
            f"Sous-plugin {kind} '{name}' introuvable : module '{module_path}' non chargeable — {e}"
        ) from e

    try:
        return getattr(module, class_name)
    except AttributeError as e:
        raise ValueError(
            f"Sous-plugin {kind} '{name}' introuvable : classe '{class_name}' absente de '{module_path}'"
        ) from e


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


def build_pipeline(step_names: list[str], kind: str) -> list:
    """
    Instancie et retourne la liste ordonnée de sous-plugins à partir d'une
    liste de noms déclarés dans un programme et de "kind" ('sequencer' ou
    'reduction') — résolution dynamique, un import par étape déclarée
    (seules les étapes de CETTE liste sont importées, jamais le catalogue
    entier des sous-plugins existants).

    Lève ValueError si un nom déclaré n'a pas de sous-plugin correspondant.
    """
    pipeline = []
    for name in step_names:
        name = name.strip().lower()
        plugin_class = _load_subplugin_class(kind, name)
        plugin_instance = plugin_class()
        pipeline.append(plugin_instance)
        log.debug(
            f"[PIPELINE] Sous-plugin chargé : {name} → "
            f"{plugin_class.__module__}.{plugin_class.__name__}"
        )
    return pipeline


def load_pipelines(programme_name: str) -> tuple[list, list]:
    """
    Charge le programme YAML et retourne (sequencer_pipeline, reduction_pipeline).

    :param programme_name: Nom du fichier YAML sans extension (ex. 'demo')
    :returns: (liste de sous-plugins séquenceur, liste de sous-plugins réduction)
    :raises FileNotFoundError: Programme YAML introuvable
    :raises ValueError: Sous-plugin déclaré dans le YAML mais introuvable
    :raises KeyError: Clés 'sequencer' ou 'reduction' manquantes dans le YAML
    """
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

    seq_pipeline = build_pipeline(seq_steps, "sequencer")
    red_pipeline = build_pipeline(red_steps, "reduction")

    return seq_pipeline, red_pipeline


def load_sharing_plugin(programme_name: str):
    """
    Charge le programme YAML et retourne l'unique sous-plugin SHARING déclaré
    — contrairement à sequencer/reduction, la clé "sharing" est singulière
    (un seul plugin de diffusion par programme, pas une liste d'étapes) :
    un programme qui a besoin de diffuser vers plusieurs destinations gère ça
    dans son propre sous-plugin, ce n'est pas au mécanisme de le permettre.

    :raises FileNotFoundError: Programme YAML introuvable
    :raises ValueError: Sous-plugin déclaré dans le YAML mais introuvable
    :raises KeyError: Clé 'sharing' manquante dans le YAML
    """
    data = load_programme_yaml(programme_name)

    if "sharing" not in data:
        raise KeyError(f"Clé 'sharing' manquante dans le programme '{programme_name}'")

    name = data["sharing"].strip().lower()
    plugin_class = _load_subplugin_class("sharing", name)
    log.info(f"[PIPELINE] Programme '{programme_name}' — sous-plugin sharing : {name} → {plugin_class.__name__}")
    return plugin_class()

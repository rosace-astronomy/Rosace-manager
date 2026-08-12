"""
Chargeur de pipeline dynamique.

Lit un fichier YAML de programme scientifique et instancie les sous-plugins
dans l'ordre déclaré, pour le séquenceur et pour la réduction.

Structure attendue du YAML :
    program: nom_du_programme
    sequencer:
      - slew
      - focus
      - star_spectra_acquisition
    reduction:
      - bias
      - dark
      - flat
      - extract
    sharing: blank_share

Usage :
    from .pipeline_loader import load_pipelines, load_sharing_plugin
    seq_pipeline, red_pipeline = load_pipelines(program)
    sharing_plugin = load_sharing_plugin(program)

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

import ast
import importlib
import logging
import re
from collections.abc import Iterable
from pathlib import Path

import yaml

from ..config import SEQUENCER_BACKEND

log = logging.getLogger("spectro.pipeline_loader")

# ── Répertoire contenant les fichiers de programme ────────────────────────────
PROGRAMS_DIR = Path(__file__).parents[1] / "programs"

# Un nom (de programme ou de sous-plugin) finit interpolé tel quel dans un
# chemin de fichier et dans un chemin de module Python importé dynamiquement —
# restreint aux caractères qu'un composant de chemin/module Python peut
# légitimement prendre (pas de '-', invalide dans un identifiant Python), pour
# empêcher toute traversée de répertoire (ex. "../../etc").
_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _validate_name(kind: str, name: str) -> str:
    """Vérifie que "name" ne peut pas s'échapper d'un chemin de fichier/module. Lève ValueError sinon."""
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"Nom {kind} {name!r} invalide — attendu : lettres, chiffres ou '_' uniquement"
        )
    return name


def _resolve_module_and_class(kind: str, name: str) -> tuple[str, str]:
    """
    Détermine le chemin de module et le nom de classe attendus pour un
    sous-plugin déclaré "name" dans un programme, selon "kind" ('sequencer',
    'reduction' ou 'sharing') et, pour 'sequencer' uniquement, SEQUENCER_BACKEND.
    """
    class_name = "".join(word.capitalize() for word in name.split("_")) + "Plugin"

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


# ── Sub-plugin catalogue startup check ─────────────────────────────────────────
# The set of sub-plugin directories on disk (sequencer_plugins/, reduction_plugins/,
# sharing_plugins/) is never enumerated centrally — pipeline_loader.py resolves each
# one individually, by name, only when a program YAML declares it. That means two
# naming mistakes would otherwise go unnoticed until they broke something at runtime
# instead of failing clearly at startup: an uppercase directory name (never reachable,
# since build_pipeline()/load_sharing_plugin() lowercase whatever name they read from
# a program YAML before resolving a module path) and the same name reused across
# families (mechanically harmless — sequencer_data/reduction_data/sharing_data are
# separate casiers — but ambiguous to a human reading a program YAML or observation
# dossier). validate_subplugin_catalogue() checks both, once, at server startup.

_SUBPLUGIN_FAMILY_DIRS = {
    "sequencer": Path(__file__).parent / "sequencer_plugins",
    "reduction": Path(__file__).parent / "reduction_plugins",
    "sharing":   Path(__file__).parent / "sharing_plugins",
}


def _discover_subplugin_names(family_dir: Path) -> set[str]:
    """A sub-plugin is any subdirectory of family_dir containing its own __init__.py."""
    return {
        entry.name
        for entry in family_dir.iterdir()
        if entry.is_dir() and (entry / "__init__.py").is_file()
    }


def _module_docstring(family_dir: Path, name: str) -> str | None:
    """Reads name's own canonical module (<name>/<name>.py) and returns its
    module-level docstring, or None if the file is missing or has none.
    Uses ast.parse rather than importing the module -- static analysis
    only, so this never triggers a sub-plugin's own import-time side
    effects (device connections, missing optional dependencies...), safe
    to run for every sub-plugin in the catalogue regardless of backend."""
    module_path = family_dir / name / f"{name}.py"
    if not module_path.is_file():
        return None
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return ast.get_docstring(tree)


_FAMILY_ORDER = {family: i for i, family in enumerate(_SUBPLUGIN_FAMILY_DIRS)}


def list_subplugins(families: Iterable[str] | None = None) -> list[tuple[str, str, str]]:
    """Returns (family, name, one-line summary) for every discovered
    sub-plugin, sorted by family (pipeline order: sequencer, reduction,
    sharing -- not alphabetical, so sequencer naturally comes first, the
    same order it actually runs in) then name. families restricts which of
    "sequencer"/"reduction"/"sharing" to include (default: all three).
    summary is the first line of the sub-plugin's own module docstring
    (<name>/<name>.py) -- "(no docstring)" if it has none, so a missing
    docstring is visible in the listing itself, not silently skipped.
    Meant for discoverability (what already exists, before writing a new
    sub-plugin or duplicating a close one) -- see also
    TestSubpluginDocstrings, which fails the test suite if any real
    sub-plugin is missing one."""
    wanted = list(families) if families is not None else list(_SUBPLUGIN_FAMILY_DIRS)
    rows = []
    for family in wanted:
        family_dir = _SUBPLUGIN_FAMILY_DIRS[family]
        for name in _discover_subplugin_names(family_dir):
            doc = _module_docstring(family_dir, name)
            summary = doc.strip().splitlines()[0] if doc and doc.strip() else "(no docstring)"
            rows.append((family, name, summary))
    return sorted(rows, key=lambda row: (_FAMILY_ORDER[row[0]], row[1]))


def validate_subplugin_catalogue() -> None:
    """
    Raises RuntimeError if any sub-plugin directory name (under sequencer_plugins/,
    reduction_plugins/, sharing_plugins/) is not all-lowercase, or if the same name
    is used in more than one of the three families. Called once from plugins/__init__.py
    at server startup.

    Both rules are deliberate design choices, not crash-safety padding — see the
    module-level comment above for the reasoning — so both error messages spell out
    explicitly that this is expected, documented behavior, not a broken installation.
    """
    names_by_family = {
        kind: _discover_subplugin_names(family_dir)
        for kind, family_dir in _SUBPLUGIN_FAMILY_DIRS.items()
    }

    not_lowercase = sorted(
        f"{kind}/{name}"
        for kind, names in names_by_family.items()
        for name in names
        if name != name.lower()
    )
    if not_lowercase:
        raise RuntimeError(
            "Rosace startup check failed: sub-plugin directory name(s) not all-lowercase: "
            f"{', '.join(not_lowercase)}. This is NOT a sign of a broken installation — "
            "Rosace deliberately requires every sub-plugin directory to be all-lowercase "
            "(letters, digits, underscores), because build_pipeline()/load_sharing_plugin() "
            "lowercase whatever name a program YAML declares before resolving it to a "
            "module path: an uppercase directory could never be found this way, and two "
            "directories differing only by case would be silently treated as the same "
            "sub-plugin. To fix: rename the directory above (and its module/class names "
            "inside) to all-lowercase."
        )

    first_family_of: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    for kind, names in names_by_family.items():
        for name in names:
            if name in first_family_of:
                collisions.setdefault(name, [first_family_of[name]]).append(kind)
            else:
                first_family_of[name] = kind

    if collisions:
        details = "; ".join(
            f"'{name}' exists in both {' and '.join(kinds)}"
            for name, kinds in sorted(collisions.items())
        )
        raise RuntimeError(
            "Rosace startup check failed: sub-plugin name collision across families "
            f"({details}). This is NOT a sign of a broken installation — Rosace "
            "deliberately forbids reusing a sub-plugin name across sequencer_plugins/, "
            "reduction_plugins/ and sharing_plugins/, even though it would technically "
            "still run correctly (each family writes to its own casier in the observation "
            "dossier). The reason is readability: a shared name makes program YAML files "
            "and observation dossiers ambiguous to a human reader (e.g. is 'dark' the "
            "capture step or the processing step?). To fix: rename one of the conflicting "
            "sub-plugin directories listed above (and its module/class names inside) to "
            "something more specific."
        )


# ── Fonctions publiques ────────────────────────────────────────────────────────

def list_programs() -> list[str]:
    """Retourne les noms des programmes disponibles (sans extension)."""
    return sorted(p.stem for p in PROGRAMS_DIR.glob("*.yaml"))


def load_program_yaml(program_name: str) -> dict:
    """
    Charge et retourne le contenu brut du YAML d'un programme.
    Lève FileNotFoundError si le fichier n'existe pas, ValueError si program_name
    contient un caractère interdit (protection contre une traversée de répertoire).
    """
    program_name = _validate_name("program", program_name)
    path = PROGRAMS_DIR / f"{program_name}.yaml"
    if not path.exists():
        available = list_programs()
        raise FileNotFoundError(
            f"Programme '{program_name}' introuvable dans {PROGRAMS_DIR}. "
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
        name = _validate_name(kind, name.strip().lower())
        plugin_class = _load_subplugin_class(kind, name)
        plugin_instance = plugin_class()
        pipeline.append(plugin_instance)
        log.debug(
            f"[PIPELINE] Sous-plugin chargé : {name} → "
            f"{plugin_class.__module__}.{plugin_class.__name__}"
        )
    return pipeline


def load_pipelines(program_name: str) -> tuple[list, list]:
    """
    Charge le programme YAML et retourne (sequencer_pipeline, reduction_pipeline).

    :param program_name: Nom du fichier YAML sans extension (ex. 'demo')
    :returns: (liste de sous-plugins séquenceur, liste de sous-plugins réduction)
    :raises FileNotFoundError: Programme YAML introuvable
    :raises ValueError: Sous-plugin déclaré dans le YAML mais introuvable
    :raises KeyError: Clés 'sequencer' ou 'reduction' manquantes dans le YAML
    """
    data = load_program_yaml(program_name)

    if "sequencer" not in data:
        raise KeyError(f"Clé 'sequencer' manquante dans le programme '{program_name}'")
    if "reduction" not in data:
        raise KeyError(f"Clé 'reduction' manquante dans le programme '{program_name}'")

    seq_steps = data["sequencer"]
    red_steps = data["reduction"]

    log.info(
        f"[PIPELINE] Programme '{program_name}' chargé — "
        f"séquenceur : {seq_steps}, réduction : {red_steps}"
    )

    seq_pipeline = build_pipeline(seq_steps, "sequencer")
    red_pipeline = build_pipeline(red_steps, "reduction")

    return seq_pipeline, red_pipeline


def load_sharing_plugin(program_name: str):
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
    data = load_program_yaml(program_name)

    if "sharing" not in data:
        raise KeyError(f"Clé 'sharing' manquante dans le programme '{program_name}'")

    name = _validate_name("sharing", data["sharing"].strip().lower())
    plugin_class = _load_subplugin_class("sharing", name)
    log.info(f"[PIPELINE] Programme '{program_name}' — sous-plugin sharing : {name} → {plugin_class.__name__}")
    return plugin_class()

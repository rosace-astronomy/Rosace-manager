"""
Dynamic pipeline loader.

Reads a scientific program's YAML file and instantiates the sub-plugins in
the declared order, for the sequencer and for reduction.

Expected YAML structure:
    program: program_name
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

Usage:
    from .pipeline_loader import load_pipelines, load_sharing_plugin
    seq_pipeline, red_pipeline = load_pipelines(program)
    sharing_plugin = load_sharing_plugin(program)

Dynamic loading by naming convention — no central registry to maintain
(unlike the old approach): a sub-plugin declared "<name>" in the YAML lives
in its own directory, one per sub-plugin (<name>/<name>.py, class
<Name>Plugin). Only the sub-plugins actually declared in the current
program are imported — never the whole catalogue.

Only SEQUENCER has a backend axis (SEQUENCER_BACKEND, config.py): the bare
name (<name>.py) is the canonical version — used for REDUCTION (which has
no hardware dependency) and for SEQUENCER when SEQUENCER_BACKEND="indi".
"internal" is a one-off exception reserved for the demo (not a full-fledged
backend): only the sub-plugins actually needed for the demo have a
<name>_internal.py file — its absence for a given sub-plugin is normal, not
a gap.

A program's own `sequencer:` list is never the *whole* story for SEQUENCER:
the active hardware profile may declare its own `sequencer_prefix:` (a list
of step names, e.g. a calibration or homing step its equipment needs) —
steps every program run against that
equipment needs, regardless of which science it's doing. See
hardware_sequencer_prefix() below: this is what keeps equipment-specific
sub-plugins out of program YAMLs entirely.
"""

from __future__ import annotations

import ast
import functools
import importlib
import logging
import re
from collections.abc import Iterable
from pathlib import Path

import yaml

from ..config import HARDWARE_CONFIG_PATH, SEQUENCER_BACKEND

log = logging.getLogger("spectro.pipeline_loader")

# ── Directory holding the program files ────────────────────────────────────────
PROGRAMS_DIR = Path(__file__).parents[1] / "programs"

# A name (program or sub-plugin) ends up interpolated as-is into a file path
# and into a dynamically imported Python module path — restricted to the
# characters a path/module component can legitimately take (no '-', invalid
# in a Python identifier), to prevent any directory traversal (e.g. "../../etc").
_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _validate_name(kind: str, name: str) -> str:
    """Checks that "name" can't escape a file/module path. Raises ValueError otherwise."""
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"Invalid {kind} name {name!r} — expected: letters, digits or '_' only"
        )
    return name


def _resolve_module_and_class(kind: str, name: str) -> tuple[str, str]:
    """
    Determines the expected module path and class name for a sub-plugin
    declared "name" in a program, based on "kind" ('sequencer', 'reduction'
    or 'sharing') and, for 'sequencer' only, SEQUENCER_BACKEND.
    """
    class_name = "".join(word.capitalize() for word in name.split("_")) + "Plugin"

    if kind == "sequencer":
        if SEQUENCER_BACKEND == "internal":
            module_path = f"rosace.plugins.sequencer_plugins.{name}.{name}_internal"
        elif SEQUENCER_BACKEND == "indi":
            module_path = f"rosace.plugins.sequencer_plugins.{name}.{name}"
        else:
            raise ValueError(
                f"sequencer_backend={SEQUENCER_BACKEND!r} unknown (expected: 'internal' or 'indi')"
            )
    elif kind == "reduction":
        module_path = f"rosace.plugins.reduction_plugins.{name}.{name}"
    elif kind == "sharing":
        module_path = f"rosace.plugins.sharing_plugins.{name}.{name}"
    else:
        raise ValueError(f"kind={kind!r} unknown (expected: 'sequencer', 'reduction' or 'sharing')")

    return module_path, class_name


def _load_subplugin_class(kind: str, name: str) -> type:
    """Dynamically imports the sub-plugin's module and returns its class."""
    module_path, class_name = _resolve_module_and_class(kind, name)

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ValueError(
            f"{kind} sub-plugin '{name}' not found: module '{module_path}' could not be loaded — {e}"
        ) from e

    try:
        return getattr(module, class_name)
    except AttributeError as e:
        raise ValueError(
            f"{kind} sub-plugin '{name}' not found: class '{class_name}' missing from '{module_path}'"
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


def _read_str_constant(module_path: Path, constant: str) -> str | None:
    """Parses a single module file and returns its top-level string constant
    named `constant` (DISPLAY_NAME, TIME_CATEGORY), or None if the file
    doesn't exist or declares none. Uses ast.parse rather than importing --
    same reasoning as _module_docstring() above: never triggers a
    sub-plugin's own import-time side effects, safe regardless of backend."""
    if not module_path.is_file():
        return None
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == constant
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    return None


def _read_display_name_constant(module_path: Path) -> str | None:
    """Returns a module file's DISPLAY_NAME constant, or None. Exposed at
    module level (rather than inlined in _module_display_name() below) so it
    can also be pointed at a "<name>_internal.py" file, e.g. to verify both
    backend variants of the same sub-plugin declare the same label (see
    TestSubpluginDisplayNames)."""
    return _read_str_constant(module_path, "DISPLAY_NAME")


def _module_display_name(family_dir: Path, name: str) -> str | None:
    """Reads name's own canonical module (<name>/<name>.py) and returns its
    DISPLAY_NAME constant (a short, human-readable label for the GUI's
    step-progress chips), or None if the file is missing or declares none."""
    return _read_display_name_constant(family_dir / name / f"{name}.py")


_FAMILY_ORDER = {family: i for i, family in enumerate(_SUBPLUGIN_FAMILY_DIRS)}


def get_display_names(families: Iterable[str] | None = None) -> dict[str, dict[str, str]]:
    """Returns {family: {name: display_name}} for every discovered
    sub-plugin in the requested families (default: all three, same
    families argument convention as list_subplugins()). A sub-plugin
    missing DISPLAY_NAME falls back to its own raw name -- a missing label
    degrades a GUI chip, it isn't a correctness risk worth failing on (same
    posture as list_subplugins()'s "(no docstring)" fallback; see
    TestSubpluginDisplayNames for the presence check on the real
    catalogue)."""
    wanted = list(families) if families is not None else list(_SUBPLUGIN_FAMILY_DIRS)
    result: dict[str, dict[str, str]] = {}
    for family in wanted:
        family_dir = _SUBPLUGIN_FAMILY_DIRS[family]
        result[family] = {
            name: _module_display_name(family_dir, name) or name
            for name in _discover_subplugin_names(family_dir)
        }
    return result


@functools.cache
def time_category(name: str) -> str:
    """Time-budget category declared by the SEQUENCER sub-plugin `name` (its
    TIME_CATEGORY constant, read from <name>/<name>.py, or from
    <name>_internal.py when only that one exists), or "other" when it
    declares none -- a missing category only blurs the time budget, it is
    no reason to fail a step (see TestSubpluginTimeCategories for the check
    on the real catalogue). Cached: the files don't change while the server
    runs."""
    family_dir = _SUBPLUGIN_FAMILY_DIRS["sequencer"]
    for module_path in (family_dir / name / f"{name}.py", family_dir / name / f"{name}_internal.py"):
        category = _read_str_constant(module_path, "TIME_CATEGORY")
        if category:
            return category
    return "other"


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


# ── Public functions ────────────────────────────────────────────────────────

def list_programs() -> list[str]:
    """Returns the names of the available programs (without extension)."""
    return sorted(p.stem for p in PROGRAMS_DIR.glob("*.yaml"))


def load_program_yaml(program_name: str) -> dict:
    """
    Loads and returns a program's raw YAML content.
    Raises FileNotFoundError if the file doesn't exist, ValueError if
    program_name contains a forbidden character (protection against a
    directory traversal).
    """
    program_name = _validate_name("program", program_name)
    path = PROGRAMS_DIR / f"{program_name}.yaml"
    if not path.exists():
        available = list_programs()
        raise FileNotFoundError(
            f"Program '{program_name}' not found in {PROGRAMS_DIR}. "
            f"Available programs: {available}"
        )
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_pipeline(step_names: list[str], kind: str) -> list:
    """
    Instantiates and returns the ordered list of sub-plugins from a list of
    names declared in a program and a "kind" ('sequencer' or 'reduction')
    — dynamic resolution, one import per declared step (only the steps in
    THIS list are imported, never the whole catalogue of existing sub-plugins).

    Raises ValueError if a declared name has no matching sub-plugin.
    """
    pipeline = []
    for name in step_names:
        name = _validate_name(kind, name.strip().lower())
        plugin_class = _load_subplugin_class(kind, name)
        plugin_instance = plugin_class()
        pipeline.append(plugin_instance)
        log.debug(
            f"[PIPELINE] Sub-plugin loaded: {name} → "
            f"{plugin_class.__module__}.{plugin_class.__name__}"
        )
    return pipeline


def hardware_sequencer_prefix() -> list[str]:
    """
    Returns the SEQUENCER step names the active hardware profile declares as
    mandatory for every program run against it (its own `sequencer_prefix:`
    key) — [] if the key
    is absent, or if SEQUENCER_BACKEND isn't "indi" (no hardware profile is
    meaningfully "active" for the internal/demo backend).

    This is what keeps equipment-specific steps out of scientific program
    YAMLs: a program never needs to know which mount or other equipment it
    runs on, or declare anything about it — the hardware profile itself does, and every
    program run against it inherits the same prefix automatically. Both
    callers that need this (load_pipelines() below, for building the actual
    pipeline, and SequencerPlugin.run(), for obs.program_steps) read it from
    this one function, so the two can never prepend different lists and
    desynchronize the step names from the sub-plugin instances they're
    zipped against.
    """
    if SEQUENCER_BACKEND != "indi":
        return []
    with open(HARDWARE_CONFIG_PATH, encoding="utf-8") as f:
        hw_config = yaml.safe_load(f) or {}
    return list(hw_config.get("sequencer_prefix", []))


def load_pipelines(program_name: str) -> tuple[list, list]:
    """
    Loads the program YAML and returns (sequencer_pipeline, reduction_pipeline).

    :param program_name: YAML filename without extension (e.g. 'demo')
    :returns: (list of sequencer sub-plugins, list of reduction sub-plugins)
    :raises FileNotFoundError: Program YAML not found
    :raises ValueError: A sub-plugin declared in the YAML could not be found
    :raises KeyError: 'sequencer' or 'reduction' key missing from the YAML
    """
    data = load_program_yaml(program_name)

    if "sequencer" not in data:
        raise KeyError(f"Missing 'sequencer' key in program '{program_name}'")
    if "reduction" not in data:
        raise KeyError(f"Missing 'reduction' key in program '{program_name}'")

    seq_steps = hardware_sequencer_prefix() + list(data["sequencer"])
    red_steps = data["reduction"]

    log.debug(f"[PIPELINE] Program '{program_name}' loaded")

    seq_pipeline = build_pipeline(seq_steps, "sequencer")
    red_pipeline = build_pipeline(red_steps, "reduction")

    return seq_pipeline, red_pipeline


def load_sharing_plugin(program_name: str):
    """
    Loads the program YAML and returns the single declared SHARING
    sub-plugin — unlike sequencer/reduction, the "sharing" key is singular
    (one sharing plugin per program, not a list of steps): a program that
    needs to share to several destinations handles that inside its own
    sub-plugin, the mechanism itself isn't meant to support it.

    :raises FileNotFoundError: Program YAML not found
    :raises ValueError: A sub-plugin declared in the YAML could not be found
    :raises KeyError: 'sharing' key missing from the YAML
    """
    data = load_program_yaml(program_name)

    if "sharing" not in data:
        raise KeyError(f"Missing 'sharing' key in program '{program_name}'")

    name = _validate_name("sharing", data["sharing"].strip().lower())
    plugin_class = _load_subplugin_class("sharing", name)
    log.info(f"[PIPELINE] Program '{program_name}' — sharing sub-plugin: {name} → {plugin_class.__name__}")
    return plugin_class()

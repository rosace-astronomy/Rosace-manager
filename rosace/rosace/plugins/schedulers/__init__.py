"""
Registry of available SCHEDULER plugins.

Unlike SEQUENCER/REDUCTION sub-plugins (reloaded dynamically by
pipeline_loader.py from a program YAML, one reload per observation), a
SCHEDULER plugin is a full stage plugin (see plugins/__init__.py): only one
is active for the whole lifetime of the application, chosen once at startup
via active_scheduler (conf/config.yaml).

Each scheduler lives in its own subdirectory, with its own class and its own
tests. To write a new one, duplicate schedulers/blank/ (the minimal example,
which documents the scheduler_data contract every SCHEDULER plugin must
honor), adapt it, then register the class below.

`selector` is the multi-program scheduler: it registers nothing per program.
To add a scientific program to it, do not touch this registry: write a
planner (plugins/program_schedulers/, see blank_program/blank_program.py)
and declare it in the program's YAML.
"""

from ...config import ACTIVE_SCHEDULER
from ...errors import SessionFatalError


class SchedulerFatalError(SessionFatalError):
    """
    Configuration/data error (missing or malformed plan file, invalid data in
    targets.db…) — not a transient failure. As a SessionFatalError, it stops
    the session instead of restarting from SCHEDULER as an ordinary exception
    would, since retrying can't fix it until the underlying cause is
    corrected. Any SCHEDULER plugin may raise it. Kept as its own class so the
    selector can tell a planner's configuration error (that program disabled)
    apart from any other planner exception.
    """


def _build_registry() -> dict[str, type]:
    """Builds the name -> class registry, importing each scheduler on demand."""
    from .scheduler_rosace_2026.scheduler import SchedulerPlugin
    from .blank.scheduler import BlankPlugin
    from .selector.scheduler import SelectorPlugin

    return {
        "scheduler_rosace_2026": SchedulerPlugin,
        "blank": BlankPlugin,
        "selector": SelectorPlugin,
    }


def load_scheduler():
    """Instantiates the active SCHEDULER plugin (active_scheduler, conf/config.yaml)."""
    registry = _build_registry()
    if ACTIVE_SCHEDULER not in registry:
        raise ValueError(
            f"active_scheduler={ACTIVE_SCHEDULER!r} unknown "
            f"(expected one of: {sorted(registry.keys())})"
        )
    return registry[ACTIVE_SCHEDULER]()

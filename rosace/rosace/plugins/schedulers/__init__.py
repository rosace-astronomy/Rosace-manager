"""
Registry of available SCHEDULER plugins.

Unlike SEQUENCER/REDUCTION sub-plugins (reloaded dynamically by
pipeline_loader.py from a programme YAML, one reload per observation), a
SCHEDULER plugin is a full stage plugin (see plugins/__init__.py): only one
is active for the whole lifetime of the application, chosen once at startup
via active_scheduler (conf/config.yaml).

Each scheduler lives in its own subdirectory, with its own class and its own
tests. To write a new one, duplicate schedulers/blank/ (the minimal example,
which documents the scheduler_data contract every SCHEDULER plugin must
honor), adapt it, then register the class below.
"""

from ...config import ACTIVE_SCHEDULER


class SchedulerFatalError(Exception):
    """
    Configuration/data error (missing or malformed plan file, invalid data in
    targets.db…) — not a transient failure. main.py's session_runner() stops
    the session on this exception instead of restarting from SCHEDULER as it
    would for an ordinary exception, since retrying can't fix it until the
    underlying cause is corrected. Any SCHEDULER plugin may raise it.
    """


def _build_registry() -> dict[str, type]:
    """Builds the name -> class registry, importing each scheduler on demand."""
    from .scheduler_rosace_2026.scheduler import SchedulerPlugin
    from .blank.scheduler import BlankPlugin

    return {
        "scheduler_rosace_2026": SchedulerPlugin,
        "blank": BlankPlugin,
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

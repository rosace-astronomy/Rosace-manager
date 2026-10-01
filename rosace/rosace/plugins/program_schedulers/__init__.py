"""
Program schedulers ("planners"): one per scientific program, each proposing
at most one candidate observation to the selector (plugins/schedulers/selector/),
which checks the candidates and picks the next observation.

This package holds what every planner and the selector share: the request
(SchedulingContext), the answer (Candidate), the planner interface
(ProgramScheduler) and load_planner(), which resolves a planner by name. The
shared feasibility checks are in checks.py. The full developer guide is the
docstring of blank_program/blank_program.py.

A planner is declared by the `scheduler:` key of a program YAML
(programs/<program>.yaml). `scheduler.name` is a directory of this package:
<name>/<name>.py, class <PascalName>Scheduler (manual_list -> ManualListScheduler).
"""

from __future__ import annotations

import importlib
from datetime import datetime
from typing import Any, Optional, Protocol

from pydantic import BaseModel, Field

from ...models import ExposureConfig
from ..pipeline_loader import _validate_name

# Priority levels (the lower, the more urgent). Defined by what is lost if the
# observation waits — see blank_program.py for the full definitions.
PRIORITY_INTERRUPT = 0   # interrupts the running observation (not implemented yet)
PRIORITY_SERIES = 1      # next element of a series already started
PRIORITY_HIGH = 2        # the information is lost if not observed tonight
PRIORITY_ORDINARY = 3
PRIORITY_FILLER = 4      # not needed, but better than an idle telescope


class Site(BaseModel):
    latitude_deg: float
    longitude_deg: float      # east positive
    elevation_m: float


class SchedulingContext(BaseModel):
    """What the selector hands to a planner on every request."""
    now: datetime             # UTC instant of the decision, the same for every planner
    site: Site
    program: Optional[str]    # program whose YAML declares this planner
    params: dict[str, Any] = {}   # the `params` block of that YAML, free-form


class TimeWindow(BaseModel):
    """The observation must start after start_after and end before end_before (UTC)."""
    start_after: Optional[datetime] = None
    end_before: Optional[datetime] = None


class Candidate(BaseModel):
    """One proposed observation. The selector resolves queried_name in targets.db."""
    queried_name: str = Field(min_length=1)
    program: str = Field(min_length=1)       # program whose YAML will be executed
    priority: int = Field(ge=PRIORITY_INTERRUPT, le=PRIORITY_FILLER)
    exposure: ExposureConfig = Field(default_factory=ExposureConfig)
    window: Optional[TimeWindow] = None
    reason: str = Field(min_length=1)        # one readable sentence: why this target now


class ProgramScheduler(Protocol):
    async def propose(self, ctx: SchedulingContext) -> Optional[Candidate]: ...


def load_planner(name: str) -> ProgramScheduler:
    """Imports program_schedulers/<name>/<name>.py and returns an instance of
    its <PascalName>Scheduler class. Raises ValueError if it can't be found."""
    _validate_name("planner", name)
    module_path = f"rosace.plugins.program_schedulers.{name}.{name}"
    class_name = "".join(word.capitalize() for word in name.split("_")) + "Scheduler"
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ValueError(f"Planner '{name}' not found: module '{module_path}' could not be loaded — {e}") from e
    try:
        return getattr(module, class_name)()
    except AttributeError as e:
        raise ValueError(f"Planner '{name}' not found: class '{class_name}' missing from '{module_path}'") from e

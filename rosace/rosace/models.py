# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Data models and plugin interface.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, Protocol, runtime_checkable
import uuid

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────
# State machine
# ─────────────────────────────────────────────────────────────

class OrchestratorStage(str, Enum):
    IDLE          = "idle"
    INIT_SESSION  = "init_session"
    SCHEDULER     = "scheduler"
    SEQUENCER     = "sequencer"
    REDUCTION     = "reduction"
    SHARING       = "sharing"
    ARCHIVE       = "archive"
    CLOSE_SESSION = "close_session"
    ERROR         = "error"


# ─────────────────────────────────────────────────────────────
# Observation models
# ─────────────────────────────────────────────────────────────

class Target(BaseModel):
    name: str
    ra: float = Field(..., ge=0, lt=360, description="Right ascension in degrees")
    dec: float = Field(..., ge=-90, le=90, description="Declination in degrees")
    magnitude_v: Optional[float] = None


class ExposureConfig(BaseModel):
    exposure_time: Optional[float] = Field(default=None, gt=0, description="Exposure time in seconds — absent if it must be computed from magnitude (star_spectra_acquisition sub-plugin)")
    num_exposures: int = Field(default=1, ge=1)
    binning: int = Field(default=1, ge=1, le=4)
    grating: str = Field(default="R1200")
    central_wavelength: float = Field(default=6563.0, description="Central wavelength in Å")


# ─────────────────────────────────────────────────────────────
# Observation file
# Created empty by the orchestrator (id, created_at, session_id), then
# progressively filled in by the stage plugins (SCHEDULER → ARCHIVE), each
# in its own casier. This is THE object that flows from plugin to plugin,
# and that gets serialized as-is to the YAML file on disk after each
# stage — the file is the exhaustive record of the observation.
#
# The orchestrator owns this object (creates it, writes it to disk, hands it
# to each stage) and only knows the fields below; the content of each
# <stage>_data casier is an internal matter for the plugin (and its
# sub-plugins) that owns it — the orchestrator never reads it, except the
# SCHEDULER contract keys of scheduler_data (target, queried_name, program,
# priority, exposure, plus optional ones), the minimum every SCHEDULER plugin
# must provide (see plugins/schedulers/blank/scheduler.py).
# ─────────────────────────────────────────────────────────────

class Observation(BaseModel):
    id: str
    created_at: datetime
    session_id: str  # traceability: which session this observation was made in
    obs_dir: Optional[str] = None
    yaml_file: Optional[str] = None

    # Name of the YAML file (in programs/) describing this observation's
    # scientific program -- e.g. "demo.yaml". Set by SequencerPlugin at the
    # same time as program_steps below.
    program_file: Optional[str] = None

    # Expected steps, declared in the program (sequencer and reduction --
    # lists --, sharing -- a single value, see plugins/sharing.py), set by
    # SequencerPlugin as its very first action, before running any
    # sub-plugin -- lets us know what was planned even if the observation
    # crashes partway through (the casiers below only prove what succeeded,
    # not what was planned). Deliberately called "program_steps" and not
    # "pipeline" here: "pipeline" is an internal implementation detail
    # (see pipeline_loader.py), the observation file only ever talks about
    # the scientific program the observer asked for.
    program_steps: dict[str, Any] = {}

    # Session-wide temperature reference, copied from SessionContext by
    # session_runner() onto every fresh Observation (see camera_target_temperature_deg_c
    # below on SessionContext for how it's computed) -- not stage-owned, so kept
    # top-level like program_file/program_steps above, and persisted (unlike
    # hardware/hardware_config below, which are live connection handles).
    ambient_temperature_deg_c: Optional[float] = None
    camera_target_temperature_deg_c: Optional[float] = None

    # One casier per known stage plugin -- free-form content, managed by the
    # plugin and its sub-plugins (e.g. reduction_data["bias"]["corrected_files"]).
    # A sub-plugin only writes its own key when it has something descriptive
    # to add to it -- tracking execution flow (what ran, in what order) stays
    # the log file's job, not this file's (see CLAUDE.md).
    scheduler_data: dict[str, Any] = {}
    sequencer_data: dict[str, Any] = {}
    reduction_data: dict[str, Any] = {}
    sharing_data: dict[str, Any] = {}
    archive_data: dict[str, Any] = {}

    # Measurements describing this observation (temperatures, pier side, focus
    # FWHM...), under agreed names that describe the measurement, not the
    # sub-plugin that made it. Written by sub-plugins; after every stage
    # main.py copies the names declared in database.MEASUREMENT_COLUMNS into
    # the observations table, without knowing who produced them.
    measurements: dict[str, Any] = {}

    # Connected hardware adapters (see hardware/, hardware_indi/adapters/), keyed
    # by device name from the hardware YAML (e.g. "mount", "science_camera") —
    # copied from SessionContext.hardware by session_runner() before SEQUENCER
    # runs, since sub-plugins only ever see Observation, never SessionContext.
    # Not persisted: excluded from model_dump() (the YAML dossier is a record of
    # what happened, not live connection handles), and excluded from deep-copies
    # (see model_copy(deep=True, update={"hardware": {}}) in main.py — deep-copying
    # a live device connection is meaningless and likely to break).
    hardware: dict[str, Any] = Field(default_factory=dict, exclude=True)

    # Each connected device's own params, keyed the same way as hardware above
    # (e.g. "guiding_camera", "focuser") -- everything its hardware YAML entry
    # declares beyond the plumbing fields needed just to connect it (see
    # hardware_connectivity.py::_PLUMBING_KEYS/_extract_device_params()),
    # verbatim and unfiltered: cooling, cooling_capability, focus_range...
    # whatever a given device happens to declare. Unlike hardware, this is
    # plain, picklable data (no live connections) -- it's excluded from
    # model_dump() by the same policy as hardware, not by technical
    # necessity: nothing here should appear in the observation YAML
    # automatically, a sub-plugin that wants a specific value recorded
    # copies it explicitly into its own sequencer_data casier, exactly like
    # any other sub-plugin output. Copied from SessionContext.hardware_config
    # by session_runner() the same way hardware itself is.
    hardware_config: dict[str, dict[str, Any]] = Field(default_factory=dict, exclude=True)

    # Live callback that would let a sub-plugin whose own work spans several
    # internal steps (e.g. star_spectra_acquisition.py's multi-sub-exposure
    # loop) notice a stop request between two of its own steps, not just
    # between stages. session_runner() (main.py) deliberately leaves it None
    # today: a stop request lets the current observation finish every series
    # it has started (a cut-short calibration series is useless). The
    # sub-plugins' checks stay in place for a future "stop now" option. None
    # outside a real session run too — callers must check for that. Same
    # "live, not persisted" pattern as hardware above: a plain callable can't
    # be deep-copied either, so it's also cleared before session_runner()'s
    # model_copy(deep=True) handoff to the background post-obs task (which has
    # no use for it — post-obs stages don't loop against a stop request).
    # Deliberately NOT main.state passed directly: no plugin should import
    # main.py (see CLAUDE.md's "never block" section) — this is the one
    # narrow, read-only question ("should I stop now?") a plugin may ask,
    # handed to it the same way hardware is, not by reaching for the
    # orchestrator itself.
    stop_check: Optional[Callable[[], bool]] = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def suggested_filename(self) -> str:
        """
        Suggested filename for this observation's YAML: <datetime_iso>_<name>.yaml
        (falls back to "target" if the target isn't known yet, e.g. before
        SCHEDULER has run). Uses the queried name (queried_name) rather than
        the canonical Simbad name, consistent with the rest of the app —
        falls back to the canonical name if queried_name isn't set (shouldn't
        happen once SCHEDULER has run, both are always set together).
        The model's only business-logic concession: reading its own
        scheduler_data to give itself a readable name.
        """
        target = self.scheduler_data.get("target")
        name = self.scheduler_data.get("queried_name") or (target.name if target else None) or "target"
        iso_str = self.created_at.strftime("%Y%m%dT%H%M%S")
        safe_name = name.replace(" ", "_").replace("/", "-")
        return f"{iso_str}_{safe_name}.yaml"


# ─────────────────────────────────────────────────────────────
# Session context
# Passed only to INIT_SESSION and CLOSE_SESSION.
# Narrow role: name/organize the session, never modified by the
# observation-related plugins (SCHEDULER → ARCHIVE).
# ─────────────────────────────────────────────────────────────

class SessionContext(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    site_name: str = "St-Pancrasse"
    session_dir: Optional[str] = None

    # Connected hardware adapters for the whole session (see Observation.hardware
    # above for the full rationale) — set once by InitSessionPlugin, read again by
    # CloseSessionPlugin to disconnect each adapter. Not persisted (db_insert_session
    # only reads specific fields, never dumps this model wholesale).
    hardware: dict[str, Any] = Field(default_factory=dict, exclude=True)

    # Each connected device's own params (see Observation.hardware_config above
    # for the full rationale) — set once by InitSessionPlugin alongside hardware,
    # copied onto every freshly-created Observation by session_runner() since
    # sub-plugins only ever see Observation, never SessionContext.
    hardware_config: dict[str, dict[str, Any]] = Field(default_factory=dict, exclude=True)

    # Live callback, same role as Observation.stop_check above, but actually
    # set — by session_runner() right before INIT_SESSION runs (the one stage
    # that acts on a SessionContext, not yet an Observation), so a
    # multi-step wait inside INIT_SESSION (e.g. camera_cooldown.py's cooldown
    # poll loop) can notice a stop request without importing main.py itself.
    stop_check: Optional[Callable[[], bool]] = Field(default=None, exclude=True)

    # Ambient temperature (read from the focuser's probe) and the science
    # camera's resulting cooldown target, both computed once by INIT_SESSION
    # (see plugins/camera_cooldown.py) and held fixed for the whole session —
    # copied onto every freshly-created Observation by session_runner() (see
    # Observation.camera_target_temperature_deg_c above), which is what
    # actually persists them (SessionContext itself is never dumped to disk).
    ambient_temperature_deg_c: Optional[float] = None
    camera_target_temperature_deg_c: Optional[float] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


# ─────────────────────────────────────────────────────────────
# Plugin interface
# ─────────────────────────────────────────────────────────────

@runtime_checkable
class SessionStagePlugin(Protocol):
    """Interface for session-level plugins (INIT_SESSION, CLOSE_SESSION)."""

    async def run(self, ctx: SessionContext) -> SessionContext:
        """
        Executes the stage's logic.
        Receives the current session context, returns the enriched context.
        Any exception propagates to the orchestrator, which handles the fallback.
        """
        ...


@runtime_checkable
class ObservationStagePlugin(Protocol):
    """Interface for observation-level plugins (SCHEDULER → ARCHIVE)."""

    async def run(self, obs: Observation) -> Observation:
        """
        Executes the stage's logic.
        Receives the current observation file, returns the enriched file
        (the plugin only writes to its own <stage>_data casier).
        Any exception propagates to the orchestrator, which handles the fallback.
        """
        ...
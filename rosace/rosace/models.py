"""
Modèles de données et interface plugin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, Protocol, runtime_checkable
import uuid

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────
# Machine à états
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
# Modèles d'observation
# ─────────────────────────────────────────────────────────────

class Target(BaseModel):
    name: str
    ra: float = Field(..., ge=0, lt=360, description="Ascension droite en degrés")
    dec: float = Field(..., ge=-90, le=90, description="Déclinaison en degrés")
    magnitude_v: Optional[float] = None


class ExposureConfig(BaseModel):
    exposure_time: Optional[float] = Field(default=None, gt=0, description="Temps d'exposition en secondes — absent si à calculer depuis la magnitude (sous-plugin expose)")
    num_exposures: int = Field(default=1, ge=1)
    binning: int = Field(default=1, ge=1, le=4)
    grating: str = Field(default="R1200")
    central_wavelength: float = Field(default=6563.0, description="Longueur d'onde centrale en Å")


# ─────────────────────────────────────────────────────────────
# Dossier d'observation
# Créé vide par l'orchestrateur (id, created_at, session_id), puis
# rempli progressivement par les plugins de stage (SCHEDULER → ARCHIVE),
# chacun dans son propre casier. C'est CET objet qui circule de plugin
# en plugin, et qui est sérialisé tel quel dans le fichier YAML sur
# disque après chaque étape — le fichier est la mémoire exhaustive de
# l'observation.
#
# L'orchestrateur ne connaît que les champs ci-dessous ; le contenu de
# chaque casier <stage>_data est une affaire interne au plugin (et à
# ses sous-plugins) qui le possède — l'orchestrateur ne le lit jamais.
# ─────────────────────────────────────────────────────────────

class Observation(BaseModel):
    id: str
    created_at: datetime
    session_id: str  # traçabilité : dans quelle session cette observation a été réalisée
    obs_dir: Optional[str] = None
    yaml_file: Optional[str] = None

    # Nom du fichier YAML (dans programs/) qui décrit le programme scientifique
    # de cette observation — ex. "demo.yaml". Posé par SequencerPlugin en même
    # temps que program_steps ci-dessous.
    program_file: Optional[str] = None

    # Étapes attendues, déclarées dans le programme (sequencer et reduction —
    # des listes —, sharing — une seule valeur, voir plugins/sharing.py), posé
    # par SequencerPlugin en tout premier, avant d'exécuter le moindre
    # sous-plugin — permet de savoir ce qui était prévu même si l'observation
    # plante en cours de route (les casiers ci-dessous ne prouvent que ce qui
    # a réussi, pas ce qui était prévu). Volontairement appelé "program_steps"
    # et non "pipeline" ici : "pipeline" est un détail d'implémentation interne
    # (voir pipeline_loader.py), le fichier d'observation ne parle que du
    # programme scientifique demandé par l'observateur.
    program_steps: dict[str, Any] = {}

    # Un casier par plugin de stage connu — contenu libre, géré par le plugin
    # et ses sous-plugins (ex. sequencer_data["expose"]["raw_files"], posé par
    # ExposePlugin — la présence de la clé d'un sous-plugin signifie qu'il a
    # terminé avec succès, pas besoin d'un flag "done" séparé).
    scheduler_data: dict[str, Any] = {}
    sequencer_data: dict[str, Any] = {}
    reduction_data: dict[str, Any] = {}
    sharing_data: dict[str, Any] = {}
    archive_data: dict[str, Any] = {}

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
    # verbatim and unfiltered: cooling, sampling_arcsec, slit_x, focus_range...
    # whatever a given device happens to declare. Unlike hardware, this is
    # plain, picklable data (no live connections) -- it's excluded from
    # model_dump() by the same policy as hardware, not by technical
    # necessity: nothing here should appear in the observation YAML
    # automatically, a sub-plugin that wants a specific value recorded
    # copies it explicitly into its own sequencer_data casier, exactly like
    # any other sub-plugin output. Copied from SessionContext.hardware_config
    # by session_runner() the same way hardware itself is.
    hardware_config: dict[str, dict[str, Any]] = Field(default_factory=dict, exclude=True)

    # Live callback, set by session_runner() (main.py) on every freshly-created
    # Observation — lets a sub-plugin whose own work spans several internal
    # steps (e.g. expose.py's multi-sub-exposure loop) notice a stop request
    # between two of its own steps, not just between stages. None outside a
    # real session run (e.g. in tests) — callers must check for that. Same
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
        Nom de fichier suggéré pour le YAML de cette observation :
        <datetime_iso>_<target_name>.yaml (repli sur "target" si la cible
        n'est pas encore connue, ex. avant que SCHEDULER n'ait tourné).
        Seule concession business du modèle : lire son propre
        scheduler_data pour se donner un nom lisible.
        """
        target = self.scheduler_data.get("target")
        target_name = target.name if target else "target"
        iso_str = self.created_at.strftime("%Y%m%dT%H%M%S")
        safe_target = target_name.replace(" ", "_").replace("/", "-")
        return f"{iso_str}_{safe_target}.yaml"


# ─────────────────────────────────────────────────────────────
# Contexte de session
# Transmis uniquement à INIT_SESSION et CLOSE_SESSION.
# Rôle réduit : nommer/organiser la session, jamais modifié par les
# plugins liés aux observations (SCHEDULER → ARCHIVE).
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

    model_config = ConfigDict(arbitrary_types_allowed=True)


# ─────────────────────────────────────────────────────────────
# Interface plugin
# ─────────────────────────────────────────────────────────────

@runtime_checkable
class SessionStagePlugin(Protocol):
    """Interface des plugins rattachés à la session (INIT_SESSION, CLOSE_SESSION)."""

    async def run(self, ctx: SessionContext) -> SessionContext:
        """
        Exécute la logique du stage.
        Reçoit le contexte de session courant, retourne le contexte enrichi.
        Toute exception remonte à l'orchestrateur qui gère le fallback.
        """
        ...


@runtime_checkable
class ObservationStagePlugin(Protocol):
    """Interface des plugins rattachés à une observation (SCHEDULER → ARCHIVE)."""

    async def run(self, obs: Observation) -> Observation:
        """
        Exécute la logique du stage.
        Reçoit le dossier d'observation courant, retourne le dossier enrichi
        (le plugin n'écrit que dans son propre casier <stage>_data).
        Toute exception remonte à l'orchestrateur qui gère le fallback.
        """
        ...

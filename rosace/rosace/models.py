"""
Modèles de données et interface plugin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable
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
    magnitude: Optional[float] = None


class ExposureConfig(BaseModel):
    exposure_time: float = Field(..., gt=0, description="Temps d'exposition en secondes")
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

    # Un casier par plugin de stage connu — contenu libre, géré par le plugin
    # et ses sous-plugins (ex. sequencer_data["raw_files"], posé par ExposePlugin).
    scheduler_data: dict[str, Any] = {}
    sequencer_data: dict[str, Any] = {}
    reduction_data: dict[str, Any] = {}
    sharing_data: dict[str, Any] = {}
    archive_data: dict[str, Any] = {}

    model_config = ConfigDict(arbitrary_types_allowed=True)


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

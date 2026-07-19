"""
Modèles de données et interface plugin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Protocol, runtime_checkable
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


class ObservationRequest(BaseModel):
    target: Target
    exposure: ExposureConfig
    notes: Optional[str] = None


class Observation(BaseModel):
    id: str
    request: ObservationRequest
    created_at: datetime
    files: list[str] = []


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
# Contexte d'observation
# Transmis de stage en stage, de SCHEDULER à ARCHIVE ; chaque plugin
# peut l'enrichir. Un nouvel objet est créé pour chaque observation.
# ─────────────────────────────────────────────────────────────

class ObservationContext(BaseModel):
    session_id: str  # traçabilité : dans quelle session cette observation a été réalisée
    current_observation: Optional[Observation] = None
    yaml_file: Optional[str] = None
    obs_dir: Optional[str] = None
    next_target_name: Optional[str] = None
    # Fichiers produits par SEQUENCER, consommés par REDUCTION puis SHARING/ARCHIVE
    raw_files: list[str] = []
    reduced_files: list[str] = []

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

    async def run(self, ctx: ObservationContext) -> ObservationContext:
        """
        Exécute la logique du stage.
        Reçoit le contexte d'observation courant, retourne le contexte enrichi.
        Toute exception remonte à l'orchestrateur qui gère le fallback.
        """
        ...

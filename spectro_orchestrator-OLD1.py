"""
Orchestrateur minimal pour observations spectroscopiques
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime
from typing import Optional
import asyncio
import uuid

app = FastAPI(
    title="Spectro Orchestrator",
    description="Noyau minimal pour orchestration d'observations spectroscopiques",
    version="0.1.0"
)

# ─────────────────────────────────────────────────────────────
# Modèles de données
# ─────────────────────────────────────────────────────────────

class InstrumentStatus(str, Enum):
    IDLE = "idle"
    SLEWING = "slewing"
    ACQUIRING = "acquiring"
    EXPOSING = "exposing"
    READING = "reading"
    ERROR = "error"

class TargetType(str, Enum):
    SCIENCE = "science"
    CALIBRATION = "calibration"
    FLAT = "flat"
    DARK = "dark"

class Target(BaseModel):
    name: str
    ra: float = Field(..., ge=0, lt=360, description="Ascension droite en degrés")
    dec: float = Field(..., ge=-90, le=90, description="Déclinaison en degrés")
    magnitude: Optional[float] = None
    target_type: TargetType = TargetType.SCIENCE

class ExposureConfig(BaseModel):
    exposure_time: float = Field(..., gt=0, description="Temps d'exposition en secondes")
    num_exposures: int = Field(default=1, ge=1)
    binning: int = Field(default=1, ge=1, le=4)
    grating: str = Field(default="R1200")
    central_wavelength: float = Field(default=6563.0, description="Longueur d'onde centrale en Å")

class ObservationRequest(BaseModel):
    target: Target
    exposure: ExposureConfig
    priority: int = Field(default=5, ge=1, le=10)
    notes: Optional[str] = None

class Observation(BaseModel):
    id: str
    request: ObservationRequest
    status: str = "pending"
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    files: list[str] = []

# ─────────────────────────────────────────────────────────────
# État global (à remplacer par une vraie DB en production)
# ─────────────────────────────────────────────────────────────

class SystemState:
    def __init__(self):
        self.instrument_status: InstrumentStatus = InstrumentStatus.IDLE
        self.current_observation: Optional[str] = None
        self.queue: list[Observation] = []
        self.completed: list[Observation] = []
        self.telescope_position: dict = {"ra": 0.0, "dec": 0.0}
        self.is_running: bool = False

state = SystemState()

# ─────────────────────────────────────────────────────────────
# Fonctions d'instrumentation (stubs à implémenter)
# ─────────────────────────────────────────────────────────────

async def slew_telescope(ra: float, dec: float) -> bool:
    """Pointe le télescope vers les coordonnées données."""
    state.instrument_status = InstrumentStatus.SLEWING
    # Simulation du pointage
    await asyncio.sleep(2.0)
    state.telescope_position = {"ra": ra, "dec": dec}
    return True

async def configure_spectrograph(config: ExposureConfig) -> bool:
    """Configure le spectrographe (réseau, longueur d'onde centrale)."""
    state.instrument_status = InstrumentStatus.ACQUIRING
    # Simulation de la configuration
    await asyncio.sleep(1.0)
    return True

async def acquire_target() -> bool:
    """Acquisition fine de la cible sur la fente."""
    state.instrument_status = InstrumentStatus.ACQUIRING
    await asyncio.sleep(1.5)
    return True

async def take_exposure(exposure_time: float, obs_id: str, exp_num: int) -> str:
    """Prend une exposition et retourne le chemin du fichier FITS."""
    state.instrument_status = InstrumentStatus.EXPOSING
    await asyncio.sleep(exposure_time * 0.1)  # Simulation accélérée
    
    state.instrument_status = InstrumentStatus.READING
    await asyncio.sleep(0.5)
    
    filename = f"/data/{obs_id}/spectrum_{exp_num:04d}.fits"
    return filename

# ─────────────────────────────────────────────────────────────
# Logique d'orchestration
# ─────────────────────────────────────────────────────────────

async def execute_observation(obs: Observation):
    """Exécute une séquence d'observation complète."""
    obs.status = "running"
    obs.started_at = datetime.utcnow()
    state.current_observation = obs.id
    
    try:
        target = obs.request.target
        exposure = obs.request.exposure
        
        # 1. Pointage
        await slew_telescope(target.ra, target.dec)
        
        # 2. Configuration spectrographe
        await configure_spectrograph(exposure)
        
        # 3. Acquisition sur fente
        if target.target_type == TargetType.SCIENCE:
            await acquire_target()
        
        # 4. Séquence d'expositions
        for i in range(exposure.num_exposures):
            filename = await take_exposure(exposure.exposure_time, obs.id, i + 1)
            obs.files.append(filename)
        
        obs.status = "completed"
        
    except Exception as e:
        obs.status = f"error: {str(e)}"
        state.instrument_status = InstrumentStatus.ERROR
    
    finally:
        obs.completed_at = datetime.utcnow()
        state.current_observation = None
        state.instrument_status = InstrumentStatus.IDLE
        
        # Déplace vers la liste des complétées
        state.queue = [o for o in state.queue if o.id != obs.id]
        state.completed.append(obs)

async def queue_processor():
    """Boucle principale de traitement de la file d'attente."""
    while state.is_running:
        if state.queue and state.instrument_status == InstrumentStatus.IDLE:
            # Trie par priorité (plus haute d'abord)
            state.queue.sort(key=lambda x: x.request.priority, reverse=True)
            next_obs = state.queue[0]
            await execute_observation(next_obs)
        await asyncio.sleep(1.0)

# ─────────────────────────────────────────────────────────────
# Endpoints API
# ─────────────────────────────────────────────────────────────

@app.post("/observations", response_model=Observation)
async def submit_observation(request: ObservationRequest):
    """Soumet une nouvelle observation à la file d'attente."""
    obs = Observation(
        id=str(uuid.uuid4())[:8],
        request=request,
        created_at=datetime.utcnow()
    )
    state.queue.append(obs)
    return obs

@app.get("/observations")
async def list_observations():
    """Liste toutes les observations (en attente et complétées)."""
    return {
        "queue": state.queue,
        "completed": state.completed[-20:]  # 20 dernières
    }

@app.get("/observations/{obs_id}")
async def get_observation(obs_id: str):
    """Récupère les détails d'une observation."""
    for obs in state.queue + state.completed:
        if obs.id == obs_id:
            return obs
    raise HTTPException(status_code=404, detail="Observation non trouvée")

@app.delete("/observations/{obs_id}")
async def cancel_observation(obs_id: str):
    """Annule une observation en attente."""
    for i, obs in enumerate(state.queue):
        if obs.id == obs_id and obs.status == "pending":
            state.queue.pop(i)
            return {"message": "Observation annulée"}
    raise HTTPException(status_code=400, detail="Impossible d'annuler cette observation")

@app.get("/status")
async def get_status():
    """Retourne l'état actuel du système."""
    return {
        "instrument_status": state.instrument_status,
        "telescope_position": state.telescope_position,
        "current_observation": state.current_observation,
        "queue_length": len(state.queue),
        "is_running": state.is_running
    }

@app.post("/control/start")
async def start_queue(background_tasks: BackgroundTasks):
    """Démarre le traitement de la file d'attente."""
    if not state.is_running:
        state.is_running = True
        background_tasks.add_task(queue_processor)
    return {"message": "Orchestrateur démarré"}

@app.post("/control/stop")
async def stop_queue():
    """Arrête le traitement de la file d'attente."""
    state.is_running = False
    return {"message": "Orchestrateur arrêté"}

@app.post("/control/abort")
async def abort_current():
    """Interrompt l'observation en cours."""
    if state.current_observation:
        # En production : envoyer un signal d'arrêt à l'instrumentation
        state.instrument_status = InstrumentStatus.IDLE
        return {"message": f"Observation {state.current_observation} interrompue"}
    raise HTTPException(status_code=400, detail="Aucune observation en cours")

# ─────────────────────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
REST routes of the BeSS validation web page (gui/src/be_validation.ts,
served at /be/): the spectra waiting for the observer's decision before
being sent to BeSS, one spectrum's page data and files, the decision, and
the mark that the observer sent it to BeSS.
The list and the page data are written by the bess_validation REDUCTION
step (rosace.db's bess_validation table, and "<final>_validation.json" next
to the final spectrum, see file_names.validation_file_name()).
"""

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .auth import UserOut, get_current_user
from .database import db_get_bess_validation, db_get_bess_validations, db_mark_bess_sent, db_set_bess_decision
from .file_names import validation_file_name

router = APIRouter(prefix="/bess", dependencies=[Depends(get_current_user)])


class Decision(BaseModel):
    decision: Literal["accepted", "rejected"]
    comment: str | None = None


async def _row_or_404(obs_id: str) -> dict:
    row = await db_get_bess_validation(obs_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Spectre inconnu : {obs_id}")
    return row


def _page_data(row: dict) -> dict | None:
    """The page's JSON, or None when it was never written."""
    path = Path(row["session_dir"] or "") / validation_file_name(row["final_spectrum"], "data")
    return json.loads(path.read_text()) if path.is_file() else None


@router.get("/validations")
async def get_validations():
    """Spectra not validated in BeSS yet (sent ones included), most recent session first."""
    return await db_get_bess_validations()


@router.get("/validations/{obs_id}")
async def get_validation(obs_id: str):
    """One spectrum: its row and its page data (null when not prepared)."""
    row = await _row_or_404(obs_id)
    return {**row, "data": _page_data(row)}


@router.get("/validations/{obs_id}/files/{name:path}")
async def get_validation_file(obs_id: str, name: str):
    """A file of the spectrum's page -- only one its page data names, so no
    other path of the session directory is ever served."""
    row = await _row_or_404(obs_id)
    data = _page_data(row)
    if data is None or name not in data.get("files", []):
        raise HTTPException(status_code=404, detail=f"Fichier non disponible : {name}")
    path = Path(row["session_dir"]) / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {name}")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@router.post("/validations/{obs_id}/decision")
async def post_decision(obs_id: str, body: Decision, user: UserOut = Depends(get_current_user)):
    """Accepts or rejects the spectrum (a new decision replaces the last one),
    until it is sent to BeSS (409 after)."""
    await _row_or_404(obs_id)
    if await db_set_bess_decision(obs_id, body.decision, user.username, body.comment) == 0:
        raise HTTPException(status_code=409, detail="Spectre déjà envoyé à BeSS : décision figée")
    return await db_get_bess_validation(obs_id)


@router.post("/validations/{obs_id}/sent")
async def post_sent(obs_id: str):
    """Marks an accepted spectrum as sent to BeSS (sent by the observer, by
    hand); 409 when it is not accepted."""
    row = await _row_or_404(obs_id)
    if await db_mark_bess_sent(obs_id) == 0:
        raise HTTPException(status_code=409, detail=f"Seul un spectre accepté peut être marqué envoyé (état : {row['status']})")
    return await db_get_bess_validation(obs_id)

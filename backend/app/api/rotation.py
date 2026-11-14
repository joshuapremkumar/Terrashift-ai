from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
from datetime import date
from backend.app.services.rotation_engine import score_sequence, load_catalog

router = APIRouter(prefix="/api/rotation", tags=["rotation"])

class RotationRequest(BaseModel):
    sequence_ids: List[str]
    context: Dict[str, Any]

@router.post("/score")
def score_rotation_endpoint(payload: RotationRequest):
    catalog = load_catalog()["crops"]
    sequence = []
    for crop_id in payload.sequence_ids:
        if crop_id not in catalog:
            raise HTTPException(status_code=400, detail=f"Unknown crop id: {crop_id}")
        sequence.append(catalog[crop_id])
    
    if not sequence:
        raise HTTPException(status_code=400, detail="Sequence must contain at least one crop")

    result = score_sequence(sequence, payload.context)
    return result

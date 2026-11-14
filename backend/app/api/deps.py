from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models.farm import Farm


def get_farm_or_404(farm_id: str, db: Session = Depends(get_db)) -> Farm:
    farm = db.get(Farm, farm_id)
    if farm is None:
        raise HTTPException(status_code=404, detail=f"Farm '{farm_id}' not found")
    return farm

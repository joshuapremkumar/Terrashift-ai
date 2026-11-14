from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models.farm import Farm
from ..schemas.farm import FarmCreate, FarmOut, FarmUpdate
from ..services import rotation_engine
from .deps import get_farm_or_404

router = APIRouter(prefix="/api/farms", tags=["farms"])


def _validate_crops(payload: FarmCreate | FarmUpdate) -> None:
    catalog = rotation_engine.load_catalog()["crops"]
    submitted = set()
    if payload.current_crop:
        submitted.add(payload.current_crop)
    submitted.update(payload.previous_crops or [])
    submitted.update(payload.preferred_crops or [])
    unknown = sorted(crop for crop in submitted if crop not in catalog)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Unknown crop id(s). Use GET /api/crops for the supported catalogue.",
                "unknown": unknown,
            },
        )


@router.post("", response_model=FarmOut, status_code=status.HTTP_201_CREATED)
def create_farm(payload: FarmCreate, db: Session = Depends(get_db)) -> Farm:
    """Register a farm. Soil values are optional; missing values are penalised, not invented."""
    _validate_crops(payload)
    farm = Farm(**payload.model_dump())
    db.add(farm)
    db.commit()
    db.refresh(farm)
    return farm


@router.get("", response_model=list[FarmOut])
def list_farms(db: Session = Depends(get_db)) -> list[Farm]:
    return list(db.scalars(select(Farm).order_by(Farm.created_at.desc())))


@router.get("/{farm_id}", response_model=FarmOut)
def get_farm(farm: Farm = Depends(get_farm_or_404)) -> Farm:
    return farm


@router.patch("/{farm_id}", response_model=FarmOut)
def update_farm(
    payload: FarmUpdate, farm: Farm = Depends(get_farm_or_404), db: Session = Depends(get_db)
) -> Farm:
    _validate_crops(payload)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(farm, key, value)
    db.add(farm)
    db.commit()
    db.refresh(farm)
    return farm


@router.delete("/{farm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_farm(farm: Farm = Depends(get_farm_or_404), db: Session = Depends(get_db)) -> None:
    db.delete(farm)
    db.commit()

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..models.farm import Farm
from ..schemas.farm import Irrigation, Priority
from ..services import analysis_service, rotation_engine
from .deps import get_farm_or_404

router = APIRouter(prefix="/api", tags=["rotations"])


class RotationInput(BaseModel):
    label: str | None = None
    rationale: str | None = None
    sequence: list[str] = Field(min_length=2, max_length=5)


class RotationsRequest(BaseModel):
    rotations: list[RotationInput] | None = Field(
        default=None,
        description="Optional custom sequences of crop ids. Omit to let the engine generate candidates.",
    )


class SimulationRequest(BaseModel):
    rainfall_change_pct: float | None = Field(default=None, ge=-60, le=100)
    irrigation: Irrigation | None = None
    priority: Priority | None = None
    soil_ph: float | None = Field(default=None, ge=3.0, le=10.0)


def _analysis_summary(analysis: dict) -> dict:
    return {
        "data_source": analysis.get("data_source"),
        "synthetic": analysis.get("synthetic"),
        "baseline": analysis.get("baseline"),
        "anomalies": analysis.get("anomalies"),
        "stress": analysis.get("stress"),
        "trends": analysis.get("trends"),
        "forecast": analysis.get("forecast"),
        "data_quality": analysis.get("data_quality"),
        "vegetation_proxy": analysis.get("vegetation_proxy"),
        "monthly_climatology": analysis.get("monthly_climatology"),
        "annual_series": analysis.get("annual_series"),
        "windows": analysis.get("windows"),
        "baseline_windows": analysis.get("baseline_windows"),
    }


@router.post("/farms/{farm_id}/rotations")
async def compare_rotations(
    payload: RotationsRequest | None = None,
    farm: Farm = Depends(get_farm_or_404),
) -> dict:
    """Score candidate rotations with the deterministic engine.

    The response contains the full per-criterion arithmetic, assumptions, flags and
    evidence labels so every number can be traced.
    """
    farm_dict = analysis_service.farm_to_dict(farm)
    analysis = await analysis_service.build_analysis(farm_dict)
    context = rotation_engine.build_context(farm_dict, analysis)

    custom = None
    if payload and payload.rotations:
        catalog = rotation_engine.load_catalog()["crops"]
        for item in payload.rotations:
            unknown = [crop for crop in item.sequence if crop not in catalog]
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail={"message": "Unknown crop id(s) in rotation.", "unknown": unknown},
                )
        custom = [item.model_dump() for item in payload.rotations]

    result = rotation_engine.evaluate(context, custom)
    return {
        "farm_id": farm.id,
        "analysis_summary": _analysis_summary(analysis),
        **result,
    }


@router.post("/farms/{farm_id}/simulate")
async def simulate(
    payload: SimulationRequest, farm: Farm = Depends(get_farm_or_404)
) -> dict:
    """Re-run the same scoring rules under changed assumptions.

    Only declared inputs change: rainfall, irrigation availability, farmer priority or
    measured soil pH. The scoring rules themselves never change between runs.
    """
    farm_dict = analysis_service.farm_to_dict(farm)
    analysis = await analysis_service.build_analysis(farm_dict)
    context = rotation_engine.build_context(farm_dict, analysis)
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Provide at least one change to simulate.")
    return {"farm_id": farm.id, **rotation_engine.simulate(context, [], changes)}


@router.get("/crops")
def list_crops() -> dict:
    catalog = rotation_engine.load_catalog()
    return {
        "count": len(catalog["crops"]),
        "provenance": catalog["meta"]["warning"],
        "crops": rotation_engine.crop_list(),
    }

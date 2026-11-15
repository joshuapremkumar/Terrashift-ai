"""Rotation scoring, simulation and crop-catalogue routes.

Both the singular (``/api/rotation/...``) and plural (``/api/rotations/...``)
spellings resolve to the same handlers so older and newer clients keep working.
The router owns the full ``/api`` prefix; nothing here is mounted twice.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..models.farm import Farm
from ..schemas.farm import Irrigation, Priority
from ..services import analysis_service, rotation_engine
from .deps import get_farm_or_404

router = APIRouter(prefix="/api", tags=["rotations"])

CRITERIA_KEYS = {"water", "climate", "soil", "pest", "preference"}


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


class RotationScoreRequest(BaseModel):
    """Direct engine call for a single sequence (no farm record required)."""

    sequence_ids: list[str] = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


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


def _month_map(raw: Any, field: str) -> dict[int, float]:
    """Accept both {"6": 120} and {6: 120} month maps from JSON clients."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{field}' must be an object keyed by month number (1-12).",
        )
    months: dict[int, float] = {}
    for key, value in raw.items():
        try:
            month = int(key)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"'{field}' has a non-numeric month key: {key!r}.",
            ) from exc
        if not 1 <= month <= 12:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"'{field}' month keys must be between 1 and 12.",
            )
        if value is None:
            continue
        try:
            months[month] = float(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"'{field}' month {month} has a non-numeric value: {value!r}.",
            ) from exc
    return months


def _prepare_context(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Fill declared defaults and normalise types before the engine sees them.

    The engine is deliberately strict about units and ranges, so bad input is
    rejected here with 422 rather than crashing inside scoring.
    """
    context = dict(raw or {})

    try:
        start_date = rotation_engine.parse_start_date(context.get("start_date"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    irrigation = context.get("irrigation") or "rainfed"
    if irrigation not in rotation_engine.IRRIGATION_MM_PER_CYCLE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown irrigation '{irrigation}'. Use one of: "
                + ", ".join(rotation_engine.IRRIGATION_MM_PER_CYCLE)
                + "."
            ),
        )

    soil_texture = context.get("soil_texture") or "loam"
    if soil_texture not in rotation_engine.TEXTURE_ORDER:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown soil_texture '{soil_texture}'. Use one of: "
                + ", ".join(rotation_engine.TEXTURE_ORDER)
                + "."
            ),
        )

    priority = context.get("priority") or "balanced"
    if priority not in rotation_engine.WEIGHT_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown priority '{priority}'. Use one of: "
                + ", ".join(rotation_engine.WEIGHT_PRESETS)
                + "."
            ),
        )

    weights = context.get("weights")
    if not isinstance(weights, dict) or set(weights) != CRITERIA_KEYS:
        weights = rotation_engine.WEIGHT_PRESETS[priority]

    context.update(
        {
            "start_date": start_date,
            "irrigation": irrigation,
            "soil_texture": soil_texture,
            "priority": priority,
            "weights": weights,
            "monthly_rain": _month_map(context.get("monthly_rain"), "monthly_rain"),
            "monthly_temp": _month_map(context.get("monthly_temp"), "monthly_temp"),
        }
    )
    return context


def _resolve_sequence(sequence_ids: list[str]) -> list[dict]:
    catalog = rotation_engine.load_catalog()["crops"]
    unknown = sorted({crop_id for crop_id in sequence_ids if crop_id not in catalog})
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Unknown crop id(s). Use GET /api/crops for the supported catalogue.",
                "unknown": unknown,
            },
        )
    if not sequence_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Sequence must contain at least one crop.",
        )
    return [catalog[crop_id] for crop_id in sequence_ids]


@router.get("/crops", tags=["rotations"])
def list_crops() -> dict:
    """The validated crop catalogue and its provenance warning."""
    catalog = rotation_engine.load_catalog()
    return {
        "count": len(catalog["crops"]),
        "provenance": catalog["meta"]["warning"],
        "crops": rotation_engine.crop_list(),
    }


@router.post("/rotation/score", tags=["rotations"])
@router.post("/rotations/score", include_in_schema=False)
def score_rotation_endpoint(payload: RotationScoreRequest) -> dict:
    """Score one crop sequence against a caller-supplied context.

    This is the farm-free entry point used by the standalone rotation scorer.
    The same handler is also served at ``/api/rotations/score``.
    """
    sequence = _resolve_sequence(payload.sequence_ids)
    context = _prepare_context(payload.context)
    result = rotation_engine.score_sequence(sequence, context, label=payload.label)
    return {
        "context_used": {
            "start_date": context["start_date"].isoformat(),
            "irrigation": context["irrigation"],
            "soil_texture": context["soil_texture"],
            "priority": context["priority"],
        },
        **result,
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
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one change to simulate.",
        )
    return {"farm_id": farm.id, **rotation_engine.simulate(context, [], changes)}

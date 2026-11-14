from __future__ import annotations

from fastapi import APIRouter, Depends

from ..models.farm import Farm
from ..services import analysis_service
from .deps import get_farm_or_404

router = APIRouter(prefix="/api/farms/{farm_id}", tags=["analysis"])


@router.post("/analyse")
async def analyse_farm(farm: Farm = Depends(get_farm_or_404)) -> dict:
    """Run the deterministic climate-stress assessment for a farm.

    Returns observed history, anomalies against a multi-year baseline, trend slopes,
    stress flags, a clearly labelled soil-moisture proxy and data-quality notes.
    """
    farm_dict = analysis_service.farm_to_dict(farm)
    analysis = await analysis_service.build_analysis(farm_dict)
    return {"farm_id": farm.id, "analysis": analysis}

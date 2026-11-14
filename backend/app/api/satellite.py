from __future__ import annotations

from fastapi import APIRouter, Depends

from ..models.farm import Farm
from ..services import nasa_satellite
from .deps import get_farm_or_404

router = APIRouter(prefix="/api/farms/{farm_id}", tags=["satellite"])


@router.get("/satellite")
async def get_satellite(farm: Farm = Depends(get_farm_or_404)) -> dict:
    """NASA GIBS map layers plus the honest status of NDVI statistics."""
    layers = nasa_satellite.gibs_layers(farm.latitude, farm.longitude)
    ndvi = await nasa_satellite.ndvi_series(farm.latitude, farm.longitude)
    return {
        "farm_id": farm.id,
        "gibs": layers,
        "ndvi_statistics": ndvi,
        "note": (
            "Map layers are real NASA GIBS imagery. NDVI time-series statistics are a separate "
            "capability that requires Earthdata credentials; they are not simulated."
        ),
    }

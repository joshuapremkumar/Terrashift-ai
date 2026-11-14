from __future__ import annotations

from fastapi import APIRouter, Depends

from ..models.farm import Farm
from ..services import analysis_service, meteomatics
from .deps import get_farm_or_404

router = APIRouter(prefix="/api/farms/{farm_id}", tags=["weather"])


@router.get("/weather")
async def get_weather(farm: Farm = Depends(get_farm_or_404)) -> dict:
    """Recent observed weather (NASA POWER) plus a forecast from the configured provider.

    Observations and forecasts are returned in separate blocks and are never merged
    into a single unlabelled series.
    """
    farm_dict = analysis_service.farm_to_dict(farm)
    observed = await analysis_service.observed_series(farm_dict, days=45)

    forecast = None
    error = None
    try:
        forecast = await meteomatics.fetch_forecast(farm.latitude, farm.longitude)
    except meteomatics.WeatherError as exc:
        error = str(exc)

    return {
        "farm_id": farm.id,
        "observed": observed,
        "forecast": forecast,
        "forecast_error": error,
        "note": (
            "Observed data comes from a coarse gridded reanalysis product and forecast data "
            "comes from a numerical weather model. They are different kinds of evidence."
        ),
    }

"""Orchestration: one place that assembles the evidence bundle for a farm."""
from __future__ import annotations

from datetime import date, timedelta

from . import climate_analysis, meteomatics, nasa_power


def farm_to_dict(farm) -> dict:
    return {
        "id": farm.id,
        "name": farm.name,
        "latitude": farm.latitude,
        "longitude": farm.longitude,
        "area_ha": farm.area_ha,
        "country": farm.country,
        "region": farm.region,
        "soil_texture": farm.soil_texture,
        "soil_ph": farm.soil_ph,
        "organic_carbon_pct": farm.organic_carbon_pct,
        "irrigation": farm.irrigation,
        "current_crop": farm.current_crop,
        "previous_crops": list(farm.previous_crops or []),
        "preferred_crops": list(farm.preferred_crops or []),
        "priority": farm.priority,
        "created_at": farm.created_at.isoformat() if farm.created_at else None,
        "updated_at": farm.updated_at.isoformat() if farm.updated_at else None,
    }


async def build_analysis(farm: dict) -> dict:
    start, end = nasa_power.default_window()
    synthetic = False
    note = ""

    try:
        series = await nasa_power.fetch_daily(farm["latitude"], farm["longitude"], start, end)
    except nasa_power.PowerError as exc:
        series = nasa_power.synthetic_series(
            farm["latitude"],
            farm["longitude"],
            start,
            end,
            reason=f"NASA POWER unavailable: {exc}",
        )
        synthetic = True
        note = str(exc)

    forecast = None
    forecast_error = None
    try:
        forecast = await meteomatics.fetch_forecast(farm["latitude"], farm["longitude"])
    except meteomatics.WeatherError as exc:
        forecast_error = str(exc)

    analysis = climate_analysis.analyse(series, forecast)
    analysis["data_source"] = "synthetic fallback" if synthetic else "NASA POWER"
    analysis["synthetic"] = synthetic
    analysis["synthetic_reason"] = note
    analysis["forecast_error"] = forecast_error
    analysis["evidence"] = {
        "observed": {
            "source": "synthetic fallback" if synthetic else "NASA POWER",
            "kind": "observation / reanalysis",
            "window": f"{start.isoformat()} to {end.isoformat()}",
        },
        "forecast": {
            "source": (forecast or {}).get("provider", "unavailable"),
            "kind": "forecast",
            "days": len((forecast or {}).get("days", [])),
        },
        "research": {"source": "not retrieved in analysis step", "kind": "research"},
    }
    return analysis


async def observed_series(farm: dict, days: int = 60) -> dict:
    end = date.today() - timedelta(days=2)
    start = end - timedelta(days=days - 1)
    try:
        series = await nasa_power.fetch_daily(farm["latitude"], farm["longitude"], start, end)
        payload = series.to_public()
    except nasa_power.PowerError as exc:
        series = nasa_power.synthetic_series(
            farm["latitude"], farm["longitude"], start, end, reason=f"NASA POWER unavailable: {exc}"
        )
        payload = series.to_public()
    payload["days"] = payload["days"][-days:]
    return payload

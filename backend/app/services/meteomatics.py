"""Weather intelligence adapter.

Primary provider: Meteomatics (historical + real-time + forecast), enabled only
when credentials are configured. Fallback: Open-Meteo, which is keyless and is
always labelled with its true source — we never present one provider's data as
another's.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx

from ..config import settings
from . import cache


class WeatherError(RuntimeError):
    pass


def _normalise_day(
    day: date,
    tmax: float | None,
    tmin: float | None,
    precip: float | None,
    rh: float | None,
    wind: float | None,
    solar: float | None,
) -> dict:
    return {
        "date": day.isoformat(),
        "tmax_c": None if tmax is None else round(tmax, 2),
        "tmin_c": None if tmin is None else round(tmin, 2),
        "precip_mm": None if precip is None else round(max(precip, 0.0), 2),
        "rh_pct": None if rh is None else round(rh, 1),
        "wind_ms": None if wind is None else round(wind, 2),
        "solar_mj_m2": None if solar is None else round(solar, 2),
    }


async def _meteomatics_forecast(latitude: float, longitude: float, days: int) -> dict:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    parameters = ",".join(
        [
            "t_max_2m:C",
            "t_min_2m:C",
            "precip_24h:mm",
            "relative_humidity_2m:p",
            "wind_speed_10m:ms",
        ]
    )
    url = (
        f"{settings.meteomatics_base_url}/"
        f"{start:%Y-%m-%dT%H:%M:%SZ}--{end:%Y-%m-%dT%H:%M:%SZ}:P1D/"
        f"{parameters}/{latitude:.4f},{longitude:.4f}/json"
    )
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.get(
            url, auth=(settings.meteomatics_username, settings.meteomatics_password)
        )
        response.raise_for_status()
        payload = response.json()

    series: dict[str, dict[str, float]] = {}
    for block in payload.get("data", []):
        parameter = block.get("parameter", "")
        key = parameter.split(":")[0]
        for coordinate in block.get("coordinates", []):
            dates = coordinate.get("dates", [])
            values = coordinate.get("value", [])
            for stamp, value in zip(dates, values):
                if value is None:
                    continue
                day = str(stamp)[:10]
                series.setdefault(key, {})[day] = float(value)

    days_out: list[dict] = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date()
        key = day.isoformat()
        days_out.append(
            _normalise_day(
                day,
                series.get("t_max_2m", {}).get(key),
                series.get("t_min_2m", {}).get(key),
                series.get("precip_24h", {}).get(key),
                series.get("relative_humidity_2m", {}).get(key),
                series.get("wind_speed_10m", {}).get(key),
                None,
            )
        )
    return {
        "provider": "Meteomatics",
        "provider_kind": "forecast",
        "provenance": "Numerical weather prediction, not observation",
        "days": days_out,
        "degraded": False,
        "note": "Forecast values carry inherent uncertainty that grows with lead time.",
    }


async def _open_meteo_forecast(latitude: float, longitude: float, days: int) -> dict:
    params = {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "daily": ",".join(
            [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "relative_humidity_2m_mean",
                "wind_speed_10m_max",
                "shortwave_radiation_sum",
            ]
        ),
        "forecast_days": days,
        "timezone": "auto",
    }
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.get(settings.open_meteo_base_url, params=params)
        response.raise_for_status()
        payload = response.json()

    daily = payload.get("daily", {})
    stamps = daily.get("time", [])
    days_out: list[dict] = []
    for index, stamp in enumerate(stamps):
        days_out.append(
            _normalise_day(
                date.fromisoformat(stamp),
                daily.get("temperature_2m_max", [None] * len(stamps))[index],
                daily.get("temperature_2m_min", [None] * len(stamps))[index],
                daily.get("precipitation_sum", [None] * len(stamps))[index],
                daily.get("relative_humidity_2m_mean", [None] * len(stamps))[index],
                daily.get("wind_speed_10m_max", [None] * len(stamps))[index],
                daily.get("shortwave_radiation_sum", [None] * len(stamps))[index],
            )
        )
    return {
        "provider": "Open-Meteo",
        "provider_kind": "forecast",
        "provenance": "Numerical weather prediction (keyless fallback provider)",
        "days": days_out,
        "degraded": not settings.meteomatics_enabled,
        "note": (
            "Meteomatics credentials are not configured, so a keyless forecast "
            "provider is used instead. The source is labelled explicitly."
            if not settings.meteomatics_enabled
            else "Fallback provider in use."
        ),
    }


async def fetch_forecast(latitude: float, longitude: float, days: int = 14) -> dict:
    cache_key = f"forecast:{latitude:.3f}:{longitude:.3f}:{days}:{date.today().isoformat()}"
    cached = cache.get(cache_key, ttl_seconds=3 * 60 * 60)
    if cached is not None:
        return cached

    result: dict | None = None
    errors: list[str] = []
    if settings.meteomatics_enabled:
        try:
            result = await _meteomatics_forecast(latitude, longitude, days)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Meteomatics: {exc}")

    if result is None:
        try:
            result = await _open_meteo_forecast(latitude, longitude, days)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Open-Meteo: {exc}")

    if result is None:
        raise WeatherError("; ".join(errors) or "No forecast provider available")

    if errors:
        result["provider_errors"] = errors
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    cache.set(cache_key, result)
    return result

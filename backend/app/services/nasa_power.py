"""NASA POWER adapter (live, no credentials required).

NASA POWER provides daily historical meteorology on a coarse grid
(~0.5 x 0.625 degrees). It is used as the historical evidence layer:
rainfall, temperature, humidity, wind, solar radiation and root-zone wetness.

The API returns -999.0 for missing values; those are dropped, never zero-filled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx

from ..config import settings
from . import cache

POWER_PARAMETERS = [
    "PRECTOTCORR",
    "T2M",
    "T2M_MAX",
    "T2M_MIN",
    "RH2M",
    "WS2M",
    "ALLSKY_SFC_SW_DWN",
    "GWETROOT",
]

_MISSING = -999.0

_FIELD_MAP = {
    "PRECTOTCORR": "precip_mm",
    "T2M": "t2m_c",
    "T2M_MAX": "tmax_c",
    "T2M_MIN": "tmin_c",
    "RH2M": "rh_pct",
    "WS2M": "wind_ms",
    "ALLSKY_SFC_SW_DWN": "solar_mj_m2",
    "GWETROOT": "soil_moisture_root",
}


class PowerError(RuntimeError):
    pass


@dataclass
class PowerSeries:
    latitude: float
    longitude: float
    start: date
    end: date
    elevation_m: float | None = None
    days: list[dict] = field(default_factory=list)
    requested_days: int = 0
    synthetic: bool = False
    note: str = ""

    @property
    def coverage(self) -> float:
        if self.requested_days <= 0:
            return 0.0
        return round(len(self.days) / self.requested_days, 3)

    def to_public(self) -> dict:
        return {
            "source": "synthetic fallback" if self.synthetic else "NASA POWER",
            "dataset": "POWER Daily (community=AG)"
            if not self.synthetic
            else "Synthetic offline fallback",
            "provenance": (
                "Observed / reanalysis-based historical meteorology"
                if not self.synthetic
                else "SYNTHETIC. Generated locally because NASA POWER was unreachable. Not real data."
            ),
            "synthetic": self.synthetic,
            "note": self.note,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "elevation_m": self.elevation_m,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "days_returned": len(self.days),
            "data_coverage": self.coverage,
            "grid_note": (
                "POWER is a coarse gridded product, not a farm-level measurement. "
                "Treat values as regional context for the farm location."
            ),
            "days": self.days,
        }


def _parse(payload: dict) -> tuple[dict[str, dict[str, float]], float | None]:
    props = payload.get("properties", {})
    parameters = props.get("parameter", {})
    frames: dict[str, dict[str, float]] = {}
    for param, series in parameters.items():
        if not isinstance(series, dict):
            continue
        frames[param] = {
            key: float(value)
            for key, value in series.items()
            if isinstance(value, (int, float)) and float(value) != _MISSING
        }

    elevation = None
    coords = payload.get("geometry", {}).get("coordinates") or []
    if len(coords) >= 3 and isinstance(coords[2], (int, float)):
        elevation = float(coords[2])
    return frames, elevation


def _normalise(frames: dict[str, dict[str, float]], start: date, end: date) -> list[dict]:
    all_dates = sorted({day for series in frames.values() for day in series})
    days: list[dict] = []
    for stamp in all_dates:
        try:
            parsed = datetime.strptime(stamp, "%Y%m%d").date()
        except ValueError:
            continue
        if parsed < start or parsed > end:
            continue
        row: dict = {"date": parsed.isoformat()}
        complete = False
        for param, key in _FIELD_MAP.items():
            value = frames.get(param, {}).get(stamp)
            row[key] = None if value is None else round(value, 2)
            if value is not None:
                complete = True
        if complete:
            days.append(row)
    return days


async def fetch_daily(
    latitude: float, longitude: float, start: date, end: date
) -> PowerSeries:
    if start > end:
        raise PowerError("start date must not be after end date")

    cache_key = f"power:{latitude:.3f}:{longitude:.3f}:{start.isoformat()}:{end.isoformat()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return PowerSeries(
            latitude=cached["latitude"],
            longitude=cached["longitude"],
            start=date.fromisoformat(cached["start"]),
            end=date.fromisoformat(cached["end"]),
            elevation_m=cached.get("elevation_m"),
            days=cached["days"],
            requested_days=cached["requested_days"],
        )

    params = {
        "parameters": ",".join(POWER_PARAMETERS),
        "community": "AG",
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "start": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "format": "JSON",
    }

    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        for attempt in range(3):
            try:
                response = await client.get(settings.nasa_power_base_url, params=params)
                response.raise_for_status()
                payload = response.json()
                break
            except Exception as exc:  # noqa: BLE001 - surfaced as PowerError below
                last_error = exc
                if attempt == 2:
                    raise PowerError(
                        f"NASA POWER request failed after 3 attempts: {last_error}"
                    ) from last_error
    frames, elevation = _parse(payload)
    days = _normalise(frames, start, end)
    if not days:
        raise PowerError("NASA POWER returned no usable observations for this location/window")

    requested_days = (end - start).days + 1
    cache.set(
        cache_key,
        {
            "latitude": latitude,
            "longitude": longitude,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "elevation_m": elevation,
            "days": days,
            "requested_days": requested_days,
        },
    )
    return PowerSeries(
        latitude=latitude,
        longitude=longitude,
        start=start,
        end=end,
        elevation_m=elevation,
        days=days,
        requested_days=requested_days,
    )


def synthetic_series(
    latitude: float, longitude: float, start: date, end: date, reason: str = ""
) -> PowerSeries:
    """Deterministic offline fallback.

    Used only when NASA POWER cannot be reached. Every response that contains this
    data is marked ``synthetic`` so it can never be mistaken for observation.
    """
    import math

    seed = abs(math.sin(latitude * 12.9898 + longitude * 78.233)) * 43758.5453
    seed = seed - math.floor(seed)
    annual_rain = 350.0 + 1100.0 * seed
    hemisphere_peak = 7 if latitude >= 0 else 1
    mean_temp = 29.0 - 0.42 * abs(latitude)

    days: list[dict] = []
    cursor = start
    index = 0
    while cursor <= end:
        phase = math.cos(2 * math.pi * (cursor.month - hemisphere_peak) / 12)
        seasonal_rain = max(0.0, annual_rain / 365.0 * (1.0 + 1.9 * phase))
        wobble = 0.85 + 0.3 * math.sin(index * 1.7 + seed * 10)
        rain = round(max(0.0, seasonal_rain * wobble), 2)
        temp = mean_temp + 4.0 * phase + 1.2 * math.sin(index * 0.9)
        days.append(
            {
                "date": cursor.isoformat(),
                "precip_mm": rain,
                "t2m_c": round(temp, 2),
                "tmax_c": round(temp + 5.5, 2),
                "tmin_c": round(temp - 5.0, 2),
                "rh_pct": round(60 + 15 * phase, 1),
                "wind_ms": round(2.5 + 1.2 * math.sin(index * 0.4), 2),
                "solar_mj_m2": round(18 + 4 * phase, 2),
                "soil_moisture_root": round(max(0.05, min(0.95, 0.45 + 0.25 * phase)), 3),
            }
        )
        cursor += timedelta(days=1)
        index += 1

    return PowerSeries(
        latitude=latitude,
        longitude=longitude,
        start=start,
        end=end,
        elevation_m=None,
        days=days,
        requested_days=(end - start).days + 1,
        synthetic=True,
        note=reason,
    )


def default_window(today: date | None = None) -> tuple[date, date]:
    """Baseline window: full years up to two days ago (POWER lags slightly)."""
    today = today or date.today()
    end = today - timedelta(days=2)
    start = end.replace(year=end.year - settings.baseline_years) + timedelta(days=1)
    return start, end

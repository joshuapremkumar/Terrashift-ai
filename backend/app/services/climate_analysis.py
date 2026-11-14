"""Deterministic climate analysis.

Everything here is arithmetic over observed daily data. No language model is
involved, and no value is invented: if a series is too incomplete to support a
statistic, the statistic is reported as unavailable.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from .nasa_power import PowerSeries


def _num(value) -> float | None:
    return None if value is None else float(value)


def _values(rows: list[dict], key: str) -> list[float]:
    return [float(row[key]) for row in rows if row.get(key) is not None]


def _shift_year(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:  # 29 February
        return value.replace(year=value.year - years, day=28)


def _rows_between(days: list[dict], start: date, end: date) -> list[dict]:
    return [row for row in days if start.isoformat() <= row["date"] <= end.isoformat()]


def _max_dry_spell(rows: list[dict]) -> int:
    longest = 0
    current = 0
    for row in rows:
        precip = row.get("precip_mm")
        if precip is None:
            continue
        if precip < 1.0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def window_stats(rows: list[dict]) -> dict:
    rain = _values(rows, "precip_mm")
    t2m = _values(rows, "t2m_c")
    tmax = _values(rows, "tmax_c")
    moisture = _values(rows, "soil_moisture_root")
    return {
        "days": len(rows),
        "rain_mm": round(sum(rain), 1) if rain else None,
        "mean_t2m_c": round(float(np.mean(t2m)), 2) if t2m else None,
        "mean_tmax_c": round(float(np.mean(tmax)), 2) if tmax else None,
        "max_tmax_c": round(float(np.max(tmax)), 2) if tmax else None,
        "heat_days_35": sum(1 for value in tmax if value >= 35.0),
        "heat_days_40": sum(1 for value in tmax if value >= 40.0),
        "dry_spell_days": _max_dry_spell(rows),
        "soil_moisture_mean": round(float(np.mean(moisture)), 3) if moisture else None,
    }


def baseline_window(
    days: list[dict], anchor: date, window_days: int, years: int
) -> tuple[dict, list[dict]]:
    """Average the same calendar window across previous years."""
    samples: list[dict] = []
    sampled_years: list[int] = []
    for offset in range(1, years + 1):
        end = _shift_year(anchor, offset)
        start = end - timedelta(days=window_days - 1)
        rows = _rows_between(days, start, end)
        if len(rows) < window_days * 0.8:
            continue
        samples.append(window_stats(rows))
        sampled_years.append(end.year)

    if not samples:
        return {}, sampled_years

    def mean_of(key: str) -> float | None:
        values = [sample[key] for sample in samples if sample.get(key) is not None]
        return round(float(np.mean(values)), 2) if values else None

    aggregated = {
        "years_used": sampled_years,
        "rain_mm": mean_of("rain_mm"),
        "mean_t2m_c": mean_of("mean_t2m_c"),
        "mean_tmax_c": mean_of("mean_tmax_c"),
        "heat_days_35": mean_of("heat_days_35"),
        "heat_days_40": mean_of("heat_days_40"),
        "dry_spell_days": mean_of("dry_spell_days"),
        "soil_moisture_mean": mean_of("soil_moisture_mean"),
    }
    return aggregated, sampled_years


def annual_series(days: list[dict], current_year: int) -> list[dict]:
    grouped: dict[int, list[dict]] = {}
    for row in days:
        year = int(row["date"][:4])
        grouped.setdefault(year, []).append(row)

    series: list[dict] = []
    for year in sorted(grouped):
        rows = grouped[year]
        stats = window_stats(rows)
        complete = len(rows) >= 330 and year < current_year
        series.append(
            {
                "year": year,
                "rain_mm": stats["rain_mm"],
                "mean_t2m_c": stats["mean_t2m_c"],
                "heat_days_35": stats["heat_days_35"],
                "complete": complete,
                "days_observed": len(rows),
            }
        )
    return series


def monthly_climatology(days: list[dict]) -> list[dict]:
    buckets: dict[int, list[dict]] = {month: [] for month in range(1, 13)}
    for row in days:
        buckets[int(row["date"][5:7])].append(row)

    climatology: list[dict] = []
    for month, rows in buckets.items():
        if not rows:
            continue
        per_year: dict[int, float] = {}
        temps: list[float] = []
        for row in rows:
            if row.get("precip_mm") is None:
                continue
            year = int(row["date"][:4])
            per_year[year] = per_year.get(year, 0.0) + float(row["precip_mm"])
            if row.get("t2m_c") is not None:
                temps.append(float(row["t2m_c"]))
        climatology.append(
            {
                "month": month,
                "rain_mm": round(float(np.mean(list(per_year.values()))), 1)
                if per_year
                else None,
                "mean_t2m_c": round(float(np.mean(temps)), 2) if temps else None,
                "years_used": len(per_year),
            }
        )
    return climatology


def _trend(series: list[dict], key: str) -> float | None:
    points = [
        (entry["year"], entry[key])
        for entry in series
        if entry.get("complete") and entry.get(key) is not None
    ]
    if len(points) < 4:
        return None
    x = np.array([point[0] for point in points], dtype=float)
    y = np.array([point[1] for point in points], dtype=float)
    slope, _ = np.polyfit(x, y, 1)
    return float(slope)


def climate_confidence(coverage: float, baseline_years: int) -> tuple[str, list[str]]:
    notes: list[str] = []
    if coverage >= 0.95 and baseline_years >= 8:
        level = "high"
    elif coverage >= 0.85 and baseline_years >= 5:
        level = "medium"
    else:
        level = "low"
    if coverage < 0.9:
        notes.append(f"Only {coverage * 100:.0f}% of requested daily records were available.")
    if baseline_years < 5:
        notes.append("The historical baseline is shorter than five years.")
    return level, notes


def analyse(
    series: PowerSeries,
    forecast: dict | None = None,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    days = series.days
    latest = date.fromisoformat(days[-1]["date"]) if days else today

    recent_30 = window_stats(_rows_between(days, latest - timedelta(days=29), latest))
    recent_90 = window_stats(_rows_between(days, latest - timedelta(days=89), latest))

    base_30, years_30 = baseline_window(days, latest, 30, 10)
    base_90, years_90 = baseline_window(days, latest, 90, 10)

    def anomaly(recent: dict, base: dict, key: str, percent: bool = False) -> float | None:
        if recent.get(key) is None or base.get(key) is None:
            return None
        if percent:
            if base[key] == 0:
                return None
            return round((recent[key] - base[key]) / base[key] * 100, 1)
        return round(recent[key] - base[key], 2)

    annual = annual_series(days, today.year)
    complete_years = [entry for entry in annual if entry["complete"]]
    baseline_annual_rain = (
        round(float(np.mean([entry["rain_mm"] for entry in complete_years])), 1)
        if complete_years
        else None
    )
    baseline_annual_temp = (
        round(float(np.mean([entry["mean_t2m_c"] for entry in complete_years])), 2)
        if complete_years
        else None
    )

    trends = {
        "rain_mm_per_year": _trend(annual, "rain_mm"),
        "t2m_c_per_year": _trend(annual, "mean_t2m_c"),
        "heat_days_35_per_year": _trend(annual, "heat_days_35"),
        "years_used": len(complete_years),
    }
    for key in ("rain_mm_per_year", "t2m_c_per_year", "heat_days_35_per_year"):
        if trends[key] is not None:
            trends[key] = round(trends[key], 3)
    trends["rain_mm_per_decade"] = (
        None if trends["rain_mm_per_year"] is None else round(trends["rain_mm_per_year"] * 10, 1)
    )
    trends["t2m_c_per_decade"] = (
        None if trends["t2m_c_per_year"] is None else round(trends["t2m_c_per_year"] * 10, 3)
    )

    rain_30_pct = anomaly(recent_30, base_30, "rain_mm", percent=True)
    rain_90_pct = anomaly(recent_90, base_90, "rain_mm", percent=True)
    temp_90_anom = anomaly(recent_90, base_90, "mean_t2m_c")
    moisture_anom = anomaly(recent_90, base_90, "soil_moisture_mean")

    flags: list[dict] = []
    if rain_90_pct is not None and rain_90_pct <= -25:
        flags.append(
            {
                "type": "rainfall_deficit",
                "severity": "high" if rain_90_pct <= -40 else "medium",
                "observed": f"{recent_90['rain_mm']} mm in the last 90 days",
                "baseline": f"{base_90.get('rain_mm')} mm average for the same window",
                "message": f"Rainfall is {abs(rain_90_pct):.0f}% below the historical average.",
            }
        )
    if recent_90["dry_spell_days"] and recent_90["dry_spell_days"] >= 14:
        flags.append(
            {
                "type": "dry_spell",
                "severity": "high" if recent_90["dry_spell_days"] >= 25 else "medium",
                "observed": f"{recent_90['dry_spell_days']} consecutive days under 1 mm",
                "baseline": f"{base_90.get('dry_spell_days')} days typical",
                "message": "A prolonged run of near-dry days has occurred.",
            }
        )
    if recent_90["heat_days_35"] and recent_90["heat_days_35"] >= 15:
        flags.append(
            {
                "type": "heat_stress",
                "severity": "high" if recent_90["heat_days_35"] >= 35 else "medium",
                "observed": f"{recent_90['heat_days_35']} days above 35 °C",
                "baseline": f"{base_90.get('heat_days_35')} days typical",
                "message": "Repeated high-temperature days increase crop heat stress risk.",
            }
        )
    if temp_90_anom is not None and temp_90_anom >= 1.0:
        flags.append(
            {
                "type": "temperature_anomaly",
                "severity": "medium",
                "observed": f"{recent_90['mean_t2m_c']} °C mean",
                "baseline": f"{base_90.get('mean_t2m_c')} °C average",
                "message": f"The last 90 days were {temp_90_anom:.1f} °C warmer than the same window historically.",
            }
        )
    if rain_90_pct is not None and rain_90_pct >= 40:
        flags.append(
            {
                "type": "rainfall_excess",
                "severity": "medium",
                "observed": f"{recent_90['rain_mm']} mm in the last 90 days",
                "baseline": f"{base_90.get('rain_mm')} mm average",
                "message": f"Rainfall is {rain_90_pct:.0f}% above average, raising waterlogging and nutrient-leaching risk.",
            }
        )
    if moisture_anom is not None and moisture_anom <= -20:
        flags.append(
            {
                "type": "soil_moisture_deficit",
                "severity": "medium",
                "observed": f"root-zone wetness {recent_90['soil_moisture_mean']}",
                "baseline": f"{base_90.get('soil_moisture_mean')} average",
                "message": f"Root-zone wetness is {abs(moisture_anom):.0f}% below its historical level.",
            }
        )

    risk_points = 0.0
    for flag in flags:
        risk_points += {"high": 30.0, "medium": 16.0, "low": 6.0}[flag["severity"]]
    stress_score = int(max(0, min(100, round(risk_points))))

    confidence, quality_notes = climate_confidence(series.coverage, len(complete_years))

    forecast_summary = None
    if forecast and forecast.get("days"):
        f_rows = [
            {
                "date": row["date"],
                "precip_mm": row.get("precip_mm") or 0.0,
                "tmax_c": row.get("tmax_c"),
                "t2m_c": None,
                "soil_moisture_root": None,
            }
            for row in forecast["days"]
        ]
        forecast_summary = window_stats(f_rows)
        forecast_summary["provider"] = forecast.get("provider")
        forecast_summary["rain_mm_14d"] = round(
            sum(row["precip_mm"] for row in f_rows), 1
        )
        forecast_summary["provenance"] = forecast.get("provenance")

    vegetation_proxy = {
        "metric": "NASA POWER root-zone soil wetness (GWETROOT)",
        "is_ndvi": False,
        "recent_mean": recent_90["soil_moisture_mean"],
        "baseline_mean": base_90.get("soil_moisture_mean"),
        "anomaly_pct": moisture_anom,
        "note": (
            "This is a soil-moisture proxy for vegetation condition, NOT NDVI. "
            "MODIS NDVI statistics require Earthdata credentials and are not "
            "computed here; the map shows live NASA GIBS NDVI imagery instead."
        ),
    }

    return {
        "location": {
            "latitude": series.latitude,
            "longitude": series.longitude,
            "elevation_m": series.elevation_m,
        },
        "period": {
            "start": series.start.isoformat(),
            "end": series.end.isoformat(),
            "days_observed": len(days),
            "data_coverage": series.coverage,
        },
        "baseline": {
            "years": len(complete_years),
            "annual_rain_mm": baseline_annual_rain,
            "annual_mean_t2m_c": baseline_annual_temp,
            "window_years_30d": years_30,
            "window_years_90d": years_90,
        },
        "monthly_climatology": monthly_climatology(days),
        "annual_series": annual,
        "trends": trends,
        "windows": {"recent_30d": recent_30, "recent_90d": recent_90},
        "baseline_windows": {"last_30d": base_30, "last_90d": base_90},
        "anomalies": {
            "rain_30d_pct": rain_30_pct,
            "rain_90d_pct": rain_90_pct,
            "t2m_90d_c": temp_90_anom,
            "soil_moisture_90d_pct": moisture_anom,
        },
        "stress": {"score": stress_score, "flags": flags},
        "vegetation_proxy": vegetation_proxy,
        "forecast": forecast_summary,
        "data_quality": {
            "confidence": confidence,
            "notes": quality_notes
            + [
                "NASA POWER is a coarse gridded product, not a farm measurement.",
                "Observations and forecasts are reported separately.",
            ],
        },
    }

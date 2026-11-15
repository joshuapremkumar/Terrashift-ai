"""Deterministic crop-rotation scoring engine.

Design rules (deliberate, and load-bearing for the project's credibility):

* The engine decides. A language model may only explain what the engine produced.
* Every criterion score is arithmetic over declared inputs and a published
  crop-parameter table. No yield prediction, no soil-health percentage claims.
* Missing inputs reduce a criterion's *quality* and apply an explicit penalty;
  they are never silently replaced by a confident-looking number.
* Assumptions (irrigation volumes, effective-rainfall fractions) are stated in
  the response, not hidden.
"""
from __future__ import annotations

import json
from calendar import monthrange
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "crop_knowledge.json"

TEXTURE_ORDER = [
    "sand",
    "loamy sand",
    "sandy loam",
    "loam",
    "silt loam",
    "clay loam",
    "clay",
]

# Prototype weights. Explicitly adjustable, explicitly not scientifically
# validated as universal. See the note returned with every result.
WEIGHT_PRESETS: dict[str, dict[str, float]] = {
    "balanced": {"water": 0.25, "climate": 0.25, "soil": 0.25, "pest": 0.15, "preference": 0.10},
    "water": {"water": 0.40, "climate": 0.25, "soil": 0.15, "pest": 0.10, "preference": 0.10},
    "soil": {"water": 0.15, "climate": 0.20, "soil": 0.40, "pest": 0.15, "preference": 0.10},
    "profit": {"water": 0.10, "climate": 0.20, "soil": 0.15, "pest": 0.10, "preference": 0.45},
    "resilience": {"water": 0.30, "climate": 0.35, "soil": 0.15, "pest": 0.10, "preference": 0.10},
}

# Prototype irrigation assumptions, in mm of applied water per crop cycle.
IRRIGATION_MM_PER_CYCLE = {
    "rainfed": 0.0,
    "limited": 75.0,
    "supplemental": 200.0,
    "full": 450.0,
}
# Share of growing-window rainfall assumed to become plant-available water.
EFFECTIVE_RAIN_FRACTION = {
    "rainfed": 0.85,
    "limited": 0.90,
    "supplemental": 0.95,
    "full": 1.00,
}

WATER_ANCHORS = [
    (0.25, 5.0),
    (0.40, 14.0),
    (0.55, 32.0),
    (0.70, 52.0),
    (0.85, 72.0),
    (1.00, 90.0),
    (1.15, 100.0),
]

DISCLAIMER = (
    "Prototype decision support. Scores rank candidate rotations relative to each "
    "other using published crop parameters and stated assumptions. They are not "
    "yield forecasts, soil-health measurements or farm-specific agronomic advice."
)

PROVENANCE = (
    "Crop parameters are indicative reference ranges compiled from public agronomic "
    "extension literature and are not calibrated for this farm."
)


@lru_cache
def load_catalog() -> dict:
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    crops = {crop["id"]: crop for crop in payload["crops"]}
    return {"crops": crops, "meta": payload["_meta"]}


def crop_list() -> list[dict]:
    catalog = load_catalog()["crops"]
    return sorted(catalog.values(), key=lambda crop: (crop["category"], crop["name"]))


def get_crop(crop_id: str) -> dict | None:
    return load_catalog()["crops"].get(crop_id)


def _interp(value: float, anchors: list[tuple[float, float]]) -> float:
    if value <= anchors[0][0]:
        return anchors[0][1]
    if value >= anchors[-1][0]:
        return anchors[-1][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x0 <= value <= x1:
            span = x1 - x0
            if span == 0:
                return y1
            return y0 + (y1 - y0) * (value - x0) / span
    return anchors[-1][1]


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


# --------------------------------------------------------------------------
# Input coercion
# --------------------------------------------------------------------------

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
)


def parse_start_date(value: date | datetime | str | None = None, default: date | None = None) -> date:
    """Coerce an ISO date/datetime string (e.g. ``"2026-06-01"``) into a ``date``.

    Every calendar routine below relies on ``date.month``; accepting raw JSON
    strings here is what keeps that contract true for callers that have not gone
    through ``build_context``.
    """
    if value is None or value == "":
        return default or date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        try:  # Handle offsets and other ISO-8601 variants as a last resort.
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError as exc:
            raise ValueError(
                f"Invalid start_date {value!r}. Expected an ISO date such as 2026-06-01."
            ) from exc
    raise TypeError(f"start_date must be a date, datetime or ISO string, not {type(value).__name__}.")


def _normalise_context(context: dict) -> dict:
    """Return a shallow copy with a real ``date`` and integer month keys.

    JSON clients send ``{"6": 120}``; the water accounting indexes by
    ``cursor.month`` (an ``int``), so keys are coerced once at the boundary.
    """
    normalised = dict(context)
    normalised["start_date"] = parse_start_date(context.get("start_date"))

    monthly_rain = context.get("monthly_rain") or {}
    if not isinstance(monthly_rain, dict):
        raise TypeError("monthly_rain must be a mapping of month number to rainfall in mm.")
    normalised["monthly_rain"] = {
        int(month): float(value)
        for month, value in monthly_rain.items()
        if value is not None
    }

    monthly_temp = context.get("monthly_temp")
    if monthly_temp is not None:
        if not isinstance(monthly_temp, dict):
            raise TypeError("monthly_temp must be a mapping of month number to temperature in °C.")
        normalised["monthly_temp"] = {
            int(month): float(value)
            for month, value in monthly_temp.items()
            if value is not None
        }

    return normalised


# --------------------------------------------------------------------------
# Calendar and water accounting
# --------------------------------------------------------------------------


def _next_month_start(anchor: date) -> date:
    if anchor.month == 12:
        return date(anchor.year + 1, 1, 1)
    return date(anchor.year, anchor.month + 1, 1)


def _pick_planting_start(
    crop: dict, cursor: date, monthly_rain: dict[int, float] | None = None
) -> tuple[date, bool]:
    """Pick a planting month from the crop's indicative window.

    Preference order: the earliest listed month that reaches 80% of the crop's
    indicative water need from rainfall alone, then the wettest listed month, then
    the earliest listed month. This prevents a summer crop from being scheduled into
    a dry winter month merely because that month is also listed for another region.
    """
    window = crop.get("planting_months") or []
    if not window:
        return cursor, True

    candidates: list[tuple[date, float, float]] = []
    for month_offset in range(0, 12):
        year = cursor.year + (cursor.month - 1 + month_offset) // 12
        month = (cursor.month - 1 + month_offset) % 12 + 1
        candidate = date(year, month, 1)
        if candidate < cursor or month not in window:
            continue
        car = monthly_rain or {}
        end = candidate + timedelta(days=int(crop["cycle_days"]) - 1)
        rain = rain_in_window(car, candidate, end)
        need = float(crop["water_need_mm"])
        ratio = rain / need if need > 0 else 2.0
        candidates.append((candidate, ratio, rain))

    if candidates:
        adequate = [item for item in candidates if item[1] >= 0.8]
        if adequate:
            return adequate[0][0], True
        wettest = max(candidates, key=lambda item: (item[2], -item[0].toordinal()))
        return wettest[0], True

    # A listed month should always occur within 12 months; keep the old 24-month
    # scan as a defensive fallback for malformed calendars.
    for month_offset in range(0, 24):
        year = cursor.year + (cursor.month - 1 + month_offset) // 12
        month = (cursor.month - 1 + month_offset) % 12 + 1
        candidate = date(year, month, 1)
        if candidate >= cursor and month in window:
            return candidate, True
    return cursor, False


def mean_temp_in_window(
    monthly_temp: dict[int, float], start: date, end: date, fallback: float | None
) -> float | None:
    """Average the monthly temperature climatology over a crop's growing window."""
    if not monthly_temp:
        return fallback
    total = 0.0
    days_used = 0
    cursor = start
    while cursor <= end:
        days_in_month = monthrange(cursor.year, cursor.month)[1]
        last_day = min(end, date(cursor.year, cursor.month, days_in_month))
        days = (last_day - cursor).days + 1
        value = monthly_temp.get(cursor.month)
        if value is not None:
            total += value * days
            days_used += days
        cursor = last_day + timedelta(days=1)
    return round(total / days_used, 2) if days_used else fallback


def rain_in_window(monthly_rain: dict[int, float], start: date, end: date) -> float:
    total = 0.0
    cursor = start
    while cursor <= end:
        days_in_month = monthrange(cursor.year, cursor.month)[1]
        month_total = monthly_rain.get(cursor.month)
        if month_total is None:
            cursor = cursor + timedelta(days=days_in_month - cursor.day + 1)
            continue
        last_day = min(end, date(cursor.year, cursor.month, days_in_month))
        days_used = (last_day - cursor).days + 1
        total += month_total * days_used / days_in_month
        cursor = last_day + timedelta(days=1)
    return total


def build_schedule(sequence: list[dict], monthly_rain: dict[int, float], start: date | str) -> list[dict]:
    cursor = _next_month_start(parse_start_date(start))
    schedule = []
    for crop in sequence:
        planting, calendar_match = _pick_planting_start(crop, cursor, monthly_rain)
        end = planting + timedelta(days=int(crop["cycle_days"]) - 1)
        schedule.append(
            {
                "crop": crop,
                "planting": planting,
                "end": end,
                "calendar_match": calendar_match,
                "window_rain_mm": round(rain_in_window(monthly_rain, planting, end), 1),
            }
        )
        cursor = end + timedelta(days=1)
    return schedule


def _water_supply(entry: dict, irrigation: str) -> dict:
    rain = entry["window_rain_mm"] * EFFECTIVE_RAIN_FRACTION[irrigation]
    applied = IRRIGATION_MM_PER_CYCLE[irrigation]
    return {
        "effective_rain_mm": round(rain, 1),
        "irrigation_mm": applied,
        "total_mm": round(rain + applied, 1),
    }


def _water_criterion(schedule: list[dict], irrigation: str, mean_temp: float | None) -> tuple[float, float, dict, str]:
    rows = []
    weighted = 0.0
    weight_total = 0.0
    for entry in schedule:
        crop = entry["crop"]
        need = float(crop["water_need_mm"])
        if need <= 0:
            rows.append({"crop": crop["id"], "need_mm": 0, "ratio": None, "score": 100.0})
            weighted += 100.0 * crop["cycle_days"]
            weight_total += crop["cycle_days"]
            continue
        supply = _water_supply(entry, irrigation)
        ratio = supply["total_mm"] / need
        score = _interp(ratio, WATER_ANCHORS)
        if ratio < 0.95:
            deficit = min(1.0, (0.95 - ratio) / 0.95)
            score += (float(crop["drought_tolerance"]) - 3.0) * 9.0 * deficit
        score = _clamp(score)
        weighted += score * crop["cycle_days"]
        weight_total += crop["cycle_days"]
        rows.append(
            {
                "crop": crop["id"],
                "need_mm": need,
                "supply_mm": supply["total_mm"],
                "effective_rain_mm": supply["effective_rain_mm"],
                "irrigation_mm": supply["irrigation_mm"],
                "ratio": round(ratio, 2),
                "score": round(score, 1),
                "window": f"{entry['planting'].isoformat()} to {entry['end'].isoformat()}",
            }
        )

    score = weighted / weight_total if weight_total else 50.0
    tight = [row for row in rows if row.get("ratio") is not None and row["ratio"] < 0.7]
    quality = 0.80 if irrigation != "rainfed" else 0.72
    if mean_temp is None:
        quality -= 0.1

    if tight:
        worst = min(tight, key=lambda row: row["ratio"])
        crop = get_crop(worst["crop"])
        text = (
            f"{crop['name']} is expected to receive about {worst['ratio'] * 100:.0f}% of its "
            f"indicative seasonal water requirement in its planting window. "
            f"Water is the main constraint on this sequence."
        )
    else:
        text = (
            "Expected water supply in each crop's planting window is close to or above "
            "the indicative requirement for every crop in this sequence."
        )
    assumptions = (
        "Water is estimated from 10-year monthly rainfall for each crop's own planting "
        f"window, with {EFFECTIVE_RAIN_FRACTION[irrigation] * 100:.0f}% assumed effective, "
        f"plus {IRRIGATION_MM_PER_CYCLE[irrigation]:.0f} mm of applied irrigation per cycle. "
        "Stored soil moisture and groundwater are not modelled, so a rainfall-only deficit "
        "can overstate stress where crops rely on residual moisture."
    )
    return round(score, 1), quality, {"rows": rows, "assumptions": assumptions}, text


def _climate_criterion(
    schedule: list[dict], mean_temp: float | None, heat_days: float | None,
    rain_anomaly: float | None, confidence: str,
    monthly_temp: dict[int, float] | None = None,
) -> tuple[float, float, dict, str]:
    if mean_temp is None and not monthly_temp:
        base_quality = 0.5
    else:
        base_quality = {"high": 0.95, "medium": 0.80, "low": 0.62}.get(confidence, 0.7)

    rows = []
    weighted = 0.0
    weight_total = 0.0
    for entry in schedule:
        crop = entry["crop"]
        cycles = float(crop["cycle_days"])
        window_temp = mean_temp_in_window(
            monthly_temp or {}, entry["planting"], entry["end"], mean_temp
        )
        if crop["id"] == "fallow" or window_temp is None:
            weighted += 70.0 * cycles
            weight_total += cycles
            continue

        low, high = float(crop["temp_opt_min_c"]), float(crop["temp_opt_max_c"])
        if low <= window_temp <= high:
            temp_score = 100.0
        else:
            distance = low - window_temp if window_temp < low else window_temp - high
            temp_score = _clamp(100.0 - 18.0 * distance)

        heat_penalty = 0.0
        if heat_days is not None:
            allowed = float(crop["heat_tolerance"]) * 12.0
            if heat_days > allowed:
                heat_penalty = min(40.0, (heat_days - allowed) * 0.8)

        drought_penalty = 0.0
        if rain_anomaly is not None and rain_anomaly < -20:
            drought_penalty = min(30.0, (3.0 - float(crop["drought_tolerance"])) * 10.0)

        crop_score = _clamp(temp_score - heat_penalty - drought_penalty)
        weighted += crop_score * cycles
        weight_total += cycles
        rows.append(
            {
                "crop": crop["id"],
                "window_mean_temp_c": window_temp,
                "annual_mean_temp_c": mean_temp,
                "optimal_range_c": [low, high],
                "heat_penalty": round(heat_penalty, 1),
                "drought_penalty": round(drought_penalty, 1),
                "score": round(crop_score, 1),
            }
        )

    score = round(weighted / weight_total if weight_total else 50.0, 1)
    if monthly_temp:
        basis = "the monthly temperature climatology for each crop's own growing window"
    else:
        basis = f"a mean of {mean_temp} °C"
    text = (
        f"Temperature suitability is compared against each crop's published optimal range "
        f"and its heat and drought tolerance limits, using {basis} "
        f"and about {heat_days} days above 35 °C per year."
        if (mean_temp is not None or monthly_temp) and heat_days is not None
        else "Temperature history was insufficient for a full climate suitability assessment."
    )
    return score, round(base_quality, 2), {"rows": rows}, text


def _soil_criterion(
    schedule: list[dict], soil_texture: str, soil_ph: float | None,
    organic_carbon: float | None,
) -> tuple[float, float, dict, str]:
    rows = []
    weighted = 0.0
    weight_total = 0.0
    previous_family = None
    previous_fixation = 0.0

    for entry in schedule:
        crop = entry["crop"]
        cycles = float(crop["cycle_days"])
        if crop["id"] == "fallow":
            rows.append({"crop": "fallow", "score": 100.0, "note": "No nutrient removal during the rest period."})
            weighted += 100.0 * cycles
            weight_total += cycles
            previous_family, previous_fixation = None, 0.0
            continue

        texture_score = 25.0
        if soil_texture in crop["soil_textures"]:
            texture_score = 100.0
        elif soil_texture in TEXTURE_ORDER and crop["id"] != "fallow":
            if soil_texture in TEXTURE_ORDER:
                distances = [
                    abs(TEXTURE_ORDER.index(soil_texture) - TEXTURE_ORDER.index(texture))
                    for texture in crop["soil_textures"]
                    if texture in TEXTURE_ORDER
                ]
                if distances:
                    nearest = min(distances)
                    texture_score = {0: 100.0, 1: 72.0, 2: 45.0}.get(nearest, 25.0)

        ph_score = 70.0
        ph_note = "Soil pH was not supplied; a neutral placeholder was used and penalised."
        if soil_ph is not None and crop["id"] != "fallow":
            low, high = float(crop["ph_min"]), float(crop["ph_max"])
            if low <= soil_ph <= high:
                ph_score = 100.0
            else:
                distance = low - soil_ph if soil_ph < low else soil_ph - high
                ph_score = _clamp(100.0 - 28.0 * distance, 5.0)
            ph_note = f"Measured pH {soil_ph} against a {low}-{high} tolerance range."

        carbon_adjust = 0.0
        if organic_carbon is not None and crop["id"] != "fallow":
            if crop["nutrient_demand"] == "high" and organic_carbon < 0.6:
                carbon_adjust = -12.0
            elif organic_carbon >= 1.2:
                carbon_adjust = 6.0

        nitrogen_adjust = 0.0
        if previous_fixation > 0:
            nitrogen_adjust += min(12.0, previous_fixation / 8.0)
        if crop["n_fixation_kg_ha"] > 0:
            nitrogen_adjust += 6.0
        elif previous_family == crop["family"] and crop["nutrient_demand"] == "high":
            nitrogen_adjust -= 10.0

        crop_score = _clamp(0.45 * texture_score + 0.45 * ph_score + carbon_adjust + nitrogen_adjust)
        weighted += crop_score * cycles
        weight_total += cycles
        rows.append(
            {
                "crop": crop["id"],
                "texture_score": round(texture_score, 1),
                "ph_score": round(ph_score, 1),
                "nitrogen_adjust": round(nitrogen_adjust, 1),
                "carbon_adjust": round(carbon_adjust, 1),
                "score": round(crop_score, 1),
                "note": ph_note,
            }
        )
        previous_family = crop["family"]
        previous_fixation = float(crop["n_fixation_kg_ha"])

    score = round(weighted / weight_total if weight_total else 50.0, 1)
    quality = 0.95 if (soil_ph is not None and organic_carbon is not None) else (
        0.80 if soil_ph is not None else 0.62
    )
    fixers = [entry["crop"]["name"] for entry in schedule if entry["crop"]["n_fixation_kg_ha"] > 0]
    if fixers:
        text = (
            "Soil and nutrient compatibility includes texture and pH fit, plus a nitrogen "
            f"credit for the legume phase ({', '.join(fixers)}). Legume nitrogen credits are "
            "treated as an input-saving benefit, not a measured yield effect."
        )
    else:
        text = (
            "Soil and nutrient compatibility includes texture and pH fit. No legume phase is "
            "present, so no biological nitrogen credit is claimed."
        )
    return score, quality, {"rows": rows}, text


def _pest_criterion(schedule: list[dict], previous_crops: list[str]) -> tuple[float, float, dict, str]:
    ids = [entry["crop"]["id"] for entry in schedule]
    families = [entry["crop"]["family"] for entry in schedule]
    notes: list[str] = []
    score = 82.0

    for index in range(1, len(ids)):
        if ids[index] == ids[index - 1] and ids[index] != "fallow":
            score -= 34.0
            notes.append(f"{get_crop(ids[index])['name']} follows itself directly.")
        elif families[index] == families[index - 1] and families[index] not in {"none"}:
            score -= 18.0
            notes.append(
                f"{get_crop(ids[index])['name']} follows another {families[index]} crop, "
                "so soil-borne pest and disease pressure is not broken."
            )

    counts: dict[str, int] = {}
    for crop_id in ids:
        if crop_id != "fallow":
            counts[crop_id] = counts.get(crop_id, 0) + 1
    for crop_id, count in counts.items():
        if count > 1:
            score -= 10.0 * (count - 1)
            notes.append(
                f"{get_crop(crop_id)['name']} appears {count} times in the sequence, "
                "so the same host crop returns within the rotation."
            )

    distinct = len({family for family in families if family != "none"})
    score += min(10.0, max(0, distinct - 1) * 4.0)

    historic_hits = [crop for crop in previous_crops if crop in ids]
    if historic_hits:
        score -= min(12.0, 6.0 * len(historic_hits))
        notes.append(
            "Recent field history already contains "
            + ", ".join(get_crop(crop)["name"] if get_crop(crop) else crop for crop in historic_hits)
            + ", which weakens the break effect."
        )

    if not notes:
        notes.append("The sequence alternates crop families, which helps break pest and disease cycles.")

    return round(_clamp(score), 1), 0.70, {"distinct_families": distinct, "notes": notes}, " ".join(notes)


def _preference_criterion(
    schedule: list[dict], preferred: list[str], priority: str, area_ha: float | None
) -> tuple[float, float, dict, str]:
    crops = [entry["crop"] for entry in schedule if entry["crop"]["id"] != "fallow"]
    if not crops:
        return 10.0, 0.6, {}, "The sequence contains no marketable crop."

    if preferred:
        matches = sum(1 for crop in crops if crop["id"] in preferred)
        preferred_score = 20.0 + 80.0 * matches / len(crops)
    else:
        preferred_score = 70.0

    profitability = sum(float(crop["profitability_tier"]) for crop in crops) / len(crops)
    food_security = sum(1 for crop in crops if crop["food_security"]) / len(crops) * 100
    water_friendliness = sum(
        _clamp(100.0 - float(crop["water_need_mm"]) / 15.0) for crop in crops
    ) / len(crops)
    soil_service = sum(1 for crop in crops if crop["n_fixation_kg_ha"] > 0) / len(crops) * 100
    resilience = sum(float(crop["drought_tolerance"]) for crop in crops) / len(crops) / 5.0 * 100
    diversity = len({crop["family"] for crop in crops}) / len(crops) * 100

    priority_scores = {
        "balanced": (profitability / 5.0 * 100 + food_security + resilience) / 3.0,
        "profit": profitability / 5.0 * 100,
        "water": water_friendliness,
        "soil": (soil_service + resilience) / 2.0,
        "resilience": (resilience + diversity) / 2.0,
    }
    alignment = priority_scores.get(priority, priority_scores["balanced"])

    score = 0.35 * preferred_score + 0.50 * alignment + 0.15 * food_security
    if area_ha is not None and area_ha <= 2.0 and priority != "profit":
        score += 5.0

    text = (
        f"Farmer priority is '{priority}'. The sequence aligns at {alignment:.0f}/100 with that "
        f"priority and matches {sum(1 for crop in crops if crop['id'] in preferred)} of "
        f"{len(crops)} crop slots to explicitly preferred crops."
        if preferred
        else f"Farmer priority is '{priority}'; the sequence aligns at {alignment:.0f}/100 with it."
    )
    return round(_clamp(score), 1), 0.85, {
        "priority": priority,
        "alignment": round(alignment, 1),
        "preferred_match": round(preferred_score, 1),
        "profitability_tier": round(profitability, 2),
        "food_security_pct": round(food_security, 1),
    }, text


def _compute_trade_offs(
    schedule: list[dict],
    context: dict,
    water_detail: dict,
    soil_detail: dict,
    pest_detail: dict,
    pref_detail: dict,
) -> dict:
    """Compute structured trade-offs for the rotation sequence."""
    gains: list[dict] = []
    losses: list[dict] = []
    
    # 1. Water Trade-offs
    water_rows = water_detail.get("rows", [])
    low_water_crops = [row for row in water_rows if row.get("ratio") is not None and row["ratio"] < 0.8]
    high_water_crops = [row for row in water_rows if row.get("ratio") is not None and row["ratio"] >= 1.0]
    
    if low_water_crops:
        losses.append({
            "type": "water_deficit",
            "severity": "high" if any(row["ratio"] < 0.5 for row in low_water_crops) else "medium",
            "crops": [row["crop"] for row in low_water_crops],
            "detail": "Some crops are expected to receive less than 80% of their indicative water need."
        })
        
    if high_water_crops:
        gains.append({
            "type": "water_surplus",
            "severity": "low",
            "crops": [row["crop"] for row in high_water_crops],
            "detail": "Some crops have a water surplus, which may support higher yield potential or allow for fallow periods."
        })

    # 2. Soil/Nutrient Trade-offs
    soil_rows = soil_detail.get("rows", [])
    legume_crops = [row["crop"] for row in soil_rows if row.get("nitrogen_adjust", 0) > 0]
    heavy_feeders = [row["crop"] for row in soil_rows if row.get("carbon_adjust", 0) < 0]
    
    if legume_crops:
        gains.append({
            "type": "nitrogen_fixation",
            "severity": "medium",
            "crops": legume_crops,
            "detail": "Legumes in the rotation fix atmospheric nitrogen, reducing fertilizer requirements for subsequent crops."
        })
        
    if heavy_feeders:
        losses.append({
            "type": "nutrient_depletion",
            "severity": "medium",
            "crops": heavy_feeders,
            "detail": "Heavy feeder crops may deplete soil organic carbon if not balanced with legumes or organic inputs."
        })

    # 3. Pest/Disease Trade-offs
    pest_notes = pest_detail.get("notes", [])
    family_break = any("alternates crop families" in note for note in pest_notes)
    monoculture_risk = any("repeated" in note or "follows itself" in note for note in pest_notes)
    
    if family_break:
        gains.append({
            "type": "pest_break",
            "severity": "medium",
            "crops": [],
            "detail": "Alternating crop families helps break pest and disease cycles."
        })
        
    if monoculture_risk:
        losses.append({
            "type": "monoculture_risk",
            "severity": "high",
            "crops": [],
            "detail": "Repeating the same crop or family increases the risk of soil-borne pests and diseases."
        })

    # 4. Economic/Preference Trade-offs
    pref_alignment = float(pref_detail.get("alignment") or 0.0)
    preferred_match = float(pref_detail.get("preferred_match") or 0.0)
    
    if pref_alignment > 70:
        gains.append({
            "type": "priority_alignment",
            "severity": "medium",
            "crops": [],
            "detail": f"The rotation aligns well ({pref_alignment:.0f}/100) with the farmer's stated priority."
        })
    elif pref_alignment < 40:
        losses.append({
            "type": "priority_misalignment",
            "severity": "medium",
            "crops": [],
            "detail": f"The rotation has low alignment ({pref_alignment:.0f}/100) with the farmer's stated priority."
        })

    if preferred_match > 50:
        gains.append({
            "type": "preferred_crop_inclusion",
            "severity": "low",
            "crops": [],
            "detail": "The rotation includes a significant proportion of the farmer's preferred crops."
        })

    return {
        "gains": gains,
        "losses": losses,
        "summary": "Structured trade-offs between water, soil, pest, and economic factors."
    }


def _crop_flags(schedule: list[dict], soil_ph: float | None, irrigation: str) -> list[dict]:
    flags: list[dict] = []
    ids = [entry["crop"]["id"] for entry in schedule]
    for entry in schedule:
        crop = entry["crop"]
        if crop["id"] == "fallow":
            continue
        if soil_ph is not None:
            low, high = float(crop["ph_min"]), float(crop["ph_max"])
            if soil_ph < low - 0.8 or soil_ph > high + 0.8:
                flags.append(
                    {
                        "type": "soil_ph_mismatch",
                        "severity": "medium",
                        "crop": crop["id"],
                        "message": (
                            f"{crop['name']} tolerates pH {low}-{high}; the reported soil pH "
                            f"is {soil_ph}."
                        ),
                    }
                )
        if not entry["calendar_match"]:
            flags.append(
                {
                    "type": "calendar_warning",
                    "severity": "low",
                    "crop": crop["id"],
                    "message": (
                        f"{crop['name']} has no indicative planting month left in the planned "
                        "window; local crop calendars must be checked."
                    ),
                }
            )
    if len(set(ids)) == 1 and ids and ids[0] != "fallow":
        flags.append(
            {
                "type": "continuous_cropping",
                "severity": "high",
                "crop": ids[0],
                "message": "The same crop is repeated for the whole sequence.",
            }
        )
    return flags


def score_sequence(
    sequence: list[dict],
    context: dict,
    label: str | None = None,
    rotation_id: str | None = None,
) -> dict:
    context = _normalise_context(context)
    schedule = build_schedule(sequence, context["monthly_rain"], context["start_date"])
    weights = context.get("weights") or WEIGHT_PRESETS[context.get("priority", "balanced")]

    water_score, water_q, water_detail, water_text = _water_criterion(
        schedule, context["irrigation"], context.get("mean_t2m_c")
    )
    climate_score, climate_q, climate_detail, climate_text = _climate_criterion(
        schedule,
        context.get("mean_t2m_c"),
        context.get("heat_days_per_year"),
        context.get("rain_anomaly_90d_pct"),
        context.get("climate_confidence", "medium"),
        context.get("monthly_temp"),
    )
    soil_score, soil_q, soil_detail, soil_text = _soil_criterion(
        schedule,
        context["soil_texture"],
        context.get("soil_ph"),
        context.get("organic_carbon_pct"),
    )
    pest_score, pest_q, pest_detail, pest_text = _pest_criterion(
        schedule, context.get("previous_crops", [])
    )
    pref_score, pref_q, pref_detail, pref_text = _preference_criterion(
        schedule,
        context.get("preferred_crops", []),
        context.get("priority", "balanced"),
        context.get("area_ha"),
    )

    criteria = {
        "water": (water_score, water_q, water_detail, water_text),
        "climate": (climate_score, climate_q, climate_detail, climate_text),
        "soil": (soil_score, soil_q, soil_detail, soil_text),
        "pest": (pest_score, pest_q, pest_detail, pest_text),
        "preference": (pref_score, pref_q, pref_detail, pref_text),
    }

    total = 0.0
    weight_sum = 0.0
    confidence_total = 0.0
    criteria_out = {}
    for key, (score, quality, detail, text) in criteria.items():
        weight = weights[key]
        effective = score * (0.85 + 0.15 * quality)
        total += weight * effective
        weight_sum += weight
        confidence_total += weight * quality
        criteria_out[key] = {
            "label": {
                "water": "Water suitability",
                "climate": "Climate suitability",
                "soil": "Soil & nutrient compatibility",
                "pest": "Pest & disease break",
                "preference": "Farmer priorities",
            }[key],
            "score": round(score, 1),
            "weight": weight,
            "data_quality": round(quality, 2),
            "explanation": text,
            "detail": detail,
        }

    score_before_constraints = round(total / weight_sum, 1) if weight_sum else 0.0
    confidence = round(confidence_total / weight_sum, 2) if weight_sum else 0.0
    confidence_label = "high" if confidence >= 0.85 else "medium" if confidence >= 0.65 else "low"

    # Agronomic feasibility guards. These do not change the scoring rules; they apply a
    # reported penalty to sequences that are physically implausible under the stated
    # water budget or that repeat the same crop. The weighted criteria score is kept
    # separately so the arithmetic stays traceable.
    constraints: list[dict] = []
    penalty_points = 0.0
    severe = [
        row for row in water_detail.get("rows", [])
        if row.get("ratio") is not None and row["ratio"] < 0.45
    ]
    if severe:
        worst = min(severe, key=lambda row: row["ratio"])
        names = ", ".join(get_crop(row["crop"])["name"] for row in severe)
        points = round(10.0 + (0.45 - worst["ratio"]) / 0.45 * 20.0, 1)
        constraints.append(
            {
                "type": "severe_water_gap",
                "penalty_points": points,
                "message": (
                    f"{names} would receive less than 45% of its indicative seasonal water "
                    f"need in its planting window (lowest ratio {worst['ratio']:.2f}). "
                    "The sequence is not feasible at this irrigation level without a "
                    "different crop or additional water."
                ),
            }
        )
        penalty_points += points

    ids = [entry["crop"]["id"] for entry in schedule if entry["crop"]["id"] != "fallow"]
    if ids:
        most_common = max(set(ids), key=ids.count)
        count = ids.count(most_common)
        adjacent = any(
            ids[index] == ids[index - 1] for index in range(1, len(ids))
        )
        if count >= len(ids) and count >= 2:
            points = 12.0
            constraints.append(
                {
                    "type": "continuous_monoculture",
                    "penalty_points": points,
                    "message": (
                        f"The same crop ({get_crop(most_common)['name']}) is repeated for the "
                        "whole sequence. Continuous monoculture carries accumulating pest, "
                        "disease and nutrient-depletion risk."
                    ),
                }
            )
            penalty_points += points
        elif adjacent:
            points = 5.0
            constraints.append(
                {
                    "type": "repeated_crop",
                    "penalty_points": points,
                    "message": (
                        f"{get_crop(most_common)['name']} appears in adjacent slots; the pest "
                        "and disease break is weaker than a true rotation."
                    ),
                }
            )
            penalty_points += points

    final_score = round(max(0.0, score_before_constraints - penalty_points), 1)

    flags = _crop_flags(schedule, context.get("soil_ph"), context["irrigation"])

    water_need = sum(float(entry["crop"]["water_need_mm"]) for entry in schedule)
    benefits: list[str] = []
    concerns: list[str] = [flag["message"] for flag in flags]
    concerns.extend(constraint["message"] for constraint in constraints)

    fixers = [entry["crop"] for entry in schedule if entry["crop"]["n_fixation_kg_ha"] > 0]
    if fixers:
        benefits.append(
            "Includes a legume phase ("
            + ", ".join(crop["name"] for crop in fixers)
            + ") with an indicative nitrogen-fixation credit of "
            f"{int(sum(crop['n_fixation_kg_ha'] for crop in fixers))} kg N/ha over the sequence."
        )
    if len({entry["crop"]["family"] for entry in schedule if entry["crop"]["family"] != "none"}) >= 2:
        benefits.append("Alternates crop families, which supports pest and disease break.")
    low_water = [entry["crop"] for entry in schedule if entry["crop"]["water_need_mm"] <= 450 and entry["crop"]["id"] != "fallow"]
    if low_water:
        benefits.append(
            "Includes lower-water-demand crops (" + ", ".join(crop["name"] for crop in low_water) + ")."
        )
    if not benefits:
        benefits.append("No specific agronomic benefit is demonstrated by this sequence.")

    high_need = [entry["crop"] for entry in schedule if entry["crop"]["water_need_mm"] >= 900]
    if high_need:
        concerns.append(
            "High water demand from " + ", ".join(crop["name"] for crop in high_need) + "."
        )

    short_windows = [
        row
        for row in water_detail.get("rows", [])
        if row.get("ratio") is not None and 0.45 <= row["ratio"] < 0.75
    ]
    if short_windows:
        concerns.append(
            "Indicative seasonal water supply covers only "
            + ", ".join(f"{row['ratio'] * 100:.0f}%" for row in short_windows)
            + " of crop water need in the modelled planting windows; stored soil moisture "
            "and groundwater are not modelled."
        )

    trade_offs = _compute_trade_offs(
        schedule,
        context,
        water_detail,
        soil_detail,
        pest_detail,
        pref_detail,
    )

    return {
        "id": rotation_id or "-".join(crop["id"] for crop in sequence),
        "label": label or "Custom rotation",
        "sequence": [crop["id"] for crop in sequence],
        "sequence_names": [crop["name"] for crop in sequence],
        "schedule": [
            {
                "crop": entry["crop"]["id"],
                "name": entry["crop"]["name"],
                "planting": entry["planting"].isoformat(),
                "harvest": entry["end"].isoformat(),
                "window_rain_mm": entry["window_rain_mm"],
            }
            for entry in schedule
        ],
        "score": final_score,
        "score_before_constraints": score_before_constraints,
        "score_penalty": round(penalty_points, 1),
        "constraints": constraints,
        "confidence": confidence_label,
        "confidence_value": confidence,
        "criteria": criteria_out,
        "flags": flags,
        "benefits": benefits,
        "concerns": concerns,
        "trade_offs": trade_offs,
        "water_budget": {
            "indicative_need_mm": round(water_need, 0),
            "irrigation_assumption_mm_per_cycle": IRRIGATION_MM_PER_CYCLE[context["irrigation"]],
            "effective_rain_fraction": EFFECTIVE_RAIN_FRACTION[context["irrigation"]],
            "rows": water_detail.get("rows", []),
            "assumptions": water_detail.get("assumptions"),
        },
        "evidence": [
            {
                "type": "observed",
                "source": "NASA POWER",
                "detail": "10-year daily rainfall and temperature history for the farm location.",
            },
            {
                "type": "forecast",
                "source": context.get("forecast_provider") or "forecast provider",
                "detail": f"{context.get('forecast_rain_14d')} mm forecast rainfall over the next 14 days.",
            },
            {
                "type": "research-backed",
                "source": "Crop parameter reference table",
                "detail": PROVENANCE,
            },
        ],
    }


# --------------------------------------------------------------------------
# Candidate generation
# --------------------------------------------------------------------------


def _temp_fit(crop: dict, mean_temp: float | None) -> bool:
    if mean_temp is None:
        return True
    return (float(crop["temp_opt_min_c"]) - 8.0) <= mean_temp <= (float(crop["temp_opt_max_c"]) + 8.0)


def generate_candidates(context: dict) -> list[dict]:
    context = _normalise_context(context)
    catalog = load_catalog()["crops"]
    current = catalog.get(context.get("current_crop") or "maize") or catalog["maize"]
    mean_temp = context.get("mean_t2m_c")
    monthly_rain = context.get("monthly_rain") or {}
    monthly_temp = context.get("monthly_temp") or {}
    start = context["start_date"]
    irrigation = context.get("irrigation", "rainfed")

    def window_fit(crop: dict) -> tuple[float, bool, bool]:
        """Return (water ratio, temp fit, planting-calendar fit) for one crop."""
        entry = build_schedule([crop], monthly_rain, start)[0]
        need = float(crop["water_need_mm"])
        ratio = _water_supply(entry, irrigation)["total_mm"] / need if need > 0 else 2.0
        temp = mean_temp_in_window(monthly_temp, entry["planting"], entry["end"], mean_temp)
        if temp is None:
            temp_ok = True
        else:
            low, high = float(crop["temp_opt_min_c"]), float(crop["temp_opt_max_c"])
            # Loose tolerance for candidate generation; scoring does the fine judgement.
            temp_ok = (low - 6.0) <= temp <= (high + 6.0)
        return ratio, temp_ok, entry["calendar_match"]

    # The pool is filtered by the temperature of each crop's own growing window
    # (below), not by the annual mean. Annual means wrongly exclude summer crops in
    # temperate climates and cool-season crops in the tropics.
    pool = [crop for crop in catalog.values() if crop["id"] != "fallow"]

    # Prefer crops that can actually be grown in their own window, but never return
    # an empty candidate set: fall back to the full pool if the filters are too tight.
    def climate_zone_ok(crop: dict) -> bool:
        """A light guard against tropical crops in cold climates."""
        if mean_temp is None:
            return True
        if mean_temp < 15.0 and crop["heat_tolerance"] >= 4:
            return False
        return True

    feasible = [
        crop for crop in pool
        if window_fit(crop)[0] >= 0.45 and window_fit(crop)[1] and climate_zone_ok(crop)
    ]
    if len(feasible) < 4:
        feasible = [crop for crop in pool if climate_zone_ok(crop)]

    def best(predicate, key, exclude=()):
        options = [crop for crop in feasible if crop["id"] not in exclude and predicate(crop)]
        if not options:
            return None
        return sorted(options, key=key, reverse=True)[0]

    # Forage legumes are multi-year stands in practice; the catalogue models them as
    # annual cycles, so they are offered for custom sequences but not auto-generated.
    legumes = [
        crop for crop in feasible
        if crop["n_fixation_kg_ha"] > 0 and "forage" not in crop["category"]
    ]
    if not legumes:
        legumes = [
            crop for crop in pool
            if crop["n_fixation_kg_ha"] > 0 and "forage" not in crop["category"]
        ]

    preferred_all = [
        crop_id for crop_id in context.get("preferred_crops", []) if crop_id in catalog
    ]

    def legume_rank(crop: dict) -> float:
        ratio, _, _ = window_fit(crop)
        food_bonus = 8.0 if crop["food_security"] else 0.0
        forage_penalty = 6.0 if "forage" in crop["category"] else 0.0
        preferred_bonus = 20.0 if crop["id"] in preferred_all else 0.0
        return (
            float(crop["n_fixation_kg_ha"]) * 0.35
            + min(ratio, 1.2) * 45.0
            + float(crop["drought_tolerance"]) * 3.0
            + food_bonus
            - forage_penalty
            + preferred_bonus
        )

    best_legume = max(legumes, key=legume_rank) if legumes else catalog["chickpea"]

    drought_cereals = sorted(
        [
            crop for crop in feasible
            if crop["family"] == "Poaceae" and crop["id"] != current["id"]
            and crop["drought_tolerance"] >= 4
            and not (mean_temp is not None and mean_temp >= 20 and crop["heat_tolerance"] <= 1)
        ],
        key=lambda crop: (
            crop["drought_tolerance"],
            -crop["water_need_mm"],
            crop["profitability_tier"],
        ),
        reverse=True,
    )
    drought_cereal = drought_cereals[0] if drought_cereals else catalog["sorghum"]
    drought_cereal_2 = next(
        (
            crop for crop in drought_cereals
            if crop["id"] not in {drought_cereal["id"], best_legume["id"]}
        ),
        None,
    )
    if drought_cereal_2 is None:
        non_legume_dry = [
            crop for crop in feasible
            if crop["drought_tolerance"] >= 4
            and crop["n_fixation_kg_ha"] == 0
            and crop["id"] not in {drought_cereal["id"], current["id"]}
        ]
        drought_cereal_2 = (
            max(
                non_legume_dry,
                key=lambda crop: (crop["drought_tolerance"], -crop["water_need_mm"]),
            )
            if non_legume_dry
            else best_legume
        )

    diverse_crops = [
        crop for crop in feasible
        if crop["family"] not in {"Poaceae", "Fabaceae", "none"}
        and not (mean_temp is not None and mean_temp >= 20 and crop["heat_tolerance"] <= 1)
    ]
    diverse_crop = (
        max(
            diverse_crops,
            key=lambda crop: (crop["profitability_tier"], -crop["water_need_mm"]),
        )
        if diverse_crops
        else drought_cereal
    )

    profitable = best(
        lambda crop: crop["profitability_tier"] >= 4,
        lambda crop: (crop["profitability_tier"], -crop["water_need_mm"]),
    ) or best_legume

    preferred_ids = [
        crop_id for crop_id in context.get("preferred_crops", [])
        if crop_id in catalog and crop_id != current["id"]
    ]
    preferred_options = [catalog[crop_id] for crop_id in preferred_ids]
    # Avoid placing two legumes next to each other in the priority-led sequence.
    preferred_crop = next(
        (crop for crop in preferred_options if crop["family"] != best_legume["family"]),
        preferred_options[0] if preferred_options else None,
    )
    if preferred_crop is None:
        preferred_crop = profitable if profitable["family"] != best_legume["family"] else drought_cereal

    current_ratio = window_fit(current)[0]
    if current_ratio < 0.45:
        # The current crop already fails the water test: show a full low-water
        # replacement rather than keeping it in every strategy.
        water_sequence = [drought_cereal, best_legume, drought_cereal_2]
    else:
        water_sequence = [current, drought_cereal, best_legume]

    candidates = [
        {
            "label": "Strategy A — Current practice",
            "rationale": "Baseline: repeat the crop the farm is already growing.",
            "sequence": [current, current, current],
        },
        {
            "label": "Strategy B — Legume break",
            "rationale": "Insert a nitrogen-fixing pulse to break the cereal cycle.",
            "sequence": [current, best_legume, current],
        },
        {
            "label": "Strategy C — Water-conscious",
            "rationale": (
                "Reduce water demand with a drought-tolerant cereal and a legume."
                if current_ratio >= 0.45
                else "Replace the current crop with a low-water rotation of drought-tolerant cereals and a legume."
            ),
            "sequence": water_sequence,
        },
        {
            "label": "Strategy D — Diversified",
            "rationale": "Add a third crop family to reduce pest and disease continuity.",
            "sequence": [current, diverse_crop, best_legume],
        },
        {
            "label": "Strategy E — Priority-led",
            "rationale": f"Led by the '{context.get('priority', 'balanced')}' priority and preferred crops.",
            "sequence": [current, preferred_crop, best_legume],
        },
    ]

    seen: set[tuple] = set()
    unique = []
    for candidate in candidates:
        key = tuple(crop["id"] for crop in candidate["sequence"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def build_context(farm: dict, analysis: dict, start_date: date | str | None = None) -> dict:
    monthly = {
        int(entry["month"]): entry["rain_mm"]
        for entry in analysis.get("monthly_climatology", [])
        if entry.get("rain_mm") is not None
    }
    monthly_temp = {
        int(entry["month"]): entry["mean_t2m_c"]
        for entry in analysis.get("monthly_climatology", [])
        if entry.get("mean_t2m_c") is not None
    }
    baseline = analysis.get("baseline", {})
    forecast = analysis.get("forecast") or {}
    return {
        "start_date": parse_start_date(start_date),
        "monthly_rain": monthly,
        "monthly_temp": monthly_temp,
        "annual_rain_mm": baseline.get("annual_rain_mm"),
        "mean_t2m_c": baseline.get("annual_mean_t2m_c"),
        "heat_days_per_year": (analysis.get("windows", {}).get("recent_90d", {}) or {}).get("heat_days_35"),
        "rain_anomaly_90d_pct": (analysis.get("anomalies") or {}).get("rain_90d_pct"),
        "climate_confidence": (analysis.get("data_quality") or {}).get("confidence", "medium"),
        "irrigation": farm["irrigation"],
        "soil_texture": farm["soil_texture"],
        "soil_ph": farm.get("soil_ph"),
        "organic_carbon_pct": farm.get("organic_carbon_pct"),
        "current_crop": farm.get("current_crop"),
        "previous_crops": farm.get("previous_crops") or [],
        "preferred_crops": farm.get("preferred_crops") or [],
        "priority": farm.get("priority", "balanced"),
        "area_ha": farm.get("area_ha"),
        "forecast_provider": forecast.get("provider"),
        "forecast_rain_14d": forecast.get("rain_mm_14d"),
        "weights": WEIGHT_PRESETS[farm.get("priority", "balanced")]
        if farm.get("priority", "balanced") in WEIGHT_PRESETS
        else WEIGHT_PRESETS["balanced"],
    }


def evaluate(context: dict, custom_sequences: list[dict] | None = None) -> dict:
    context = _normalise_context(context)
    catalog = load_catalog()["crops"]
    if custom_sequences:
        candidates = []
        for index, item in enumerate(custom_sequences):
            sequence = [catalog[crop_id] for crop_id in item["sequence"] if crop_id in catalog]
            if not sequence:
                raise ValueError(f"Rotation {item.get('label', index)} has no valid crop ids")
            candidates.append(
                {
                    "label": item.get("label") or f"Rotation {index + 1}",
                    "rationale": item.get("rationale") or "Custom rotation supplied by the user.",
                    "sequence": sequence,
                }
            )
    else:
        candidates = generate_candidates(context)

    scored = []
    for index, candidate in enumerate(candidates):
        scored.append(
            score_sequence(
                candidate["sequence"],
                context,
                label=candidate["label"],
                rotation_id=f"rot_{chr(65 + index)}",
            )
        )
        scored[-1]["rationale"] = candidate["rationale"]

    scored.sort(key=lambda item: item["score"], reverse=True)
    for rank, item in enumerate(scored, start=1):
        item["rank"] = rank

    winner = scored[0] if scored else None
    return {
        "weights": context["weights"],
        "weight_note": (
            "Prototype weights, adjustable and not scientifically validated as universal. "
            "They can be recalibrated with agronomic expertise."
        ),
        "context": {
            "irrigation": context["irrigation"],
            "soil_texture": context["soil_texture"],
            "soil_ph": context.get("soil_ph"),
            "priority": context.get("priority"),
            "mean_t2m_c": context.get("mean_t2m_c"),
            "annual_rain_mm": context.get("annual_rain_mm"),
            "start_date": context["start_date"].isoformat(),
            "irrigation_assumptions_mm_per_cycle": IRRIGATION_MM_PER_CYCLE[context["irrigation"]],
            "effective_rain_fraction": EFFECTIVE_RAIN_FRACTION[context["irrigation"]],
            "planting_month_rule": (
                "Each crop is placed in the earliest listed planting month that meets 80% of "
                "its indicative rainfall water need, otherwise the wettest listed month."
            ),
        },
        "rotations": scored,
        "winner": None
        if winner is None
        else {
            "id": winner["id"],
            "label": winner["label"],
            "score": winner["score"],
            "sequence_names": winner["sequence_names"],
            "why": winner["criteria"]["water"]["explanation"]
            + " "
            + winner["criteria"]["pest"]["explanation"],
        },
        "disclaimer": DISCLAIMER,
        "crop_parameter_provenance": PROVENANCE,
    }


def simulate(context: dict, base_rotation_ids: list[str], changes: dict) -> dict:
    """Re-score the same candidate sequences under changed assumptions.

    The simulation changes declared inputs (rainfall, irrigation, priority), never
    the scoring rules. Deltas are differences between two deterministic runs.
    """
    context = _normalise_context(context)
    before = evaluate(context)

    simulated = dict(context)
    if "rainfall_change_pct" in changes and changes["rainfall_change_pct"] is not None:
        factor = 1.0 + float(changes["rainfall_change_pct"]) / 100.0
        simulated["monthly_rain"] = {
            month: max(0.0, value * factor)
            for month, value in context["monthly_rain"].items()
        }
        simulated["rain_change_pct"] = changes["rainfall_change_pct"]
    if changes.get("irrigation"):
        simulated["irrigation"] = changes["irrigation"]
    if changes.get("priority"):
        simulated["priority"] = changes["priority"]
        simulated["weights"] = WEIGHT_PRESETS.get(changes["priority"], WEIGHT_PRESETS["balanced"])
    if changes.get("soil_ph") is not None:
        simulated["soil_ph"] = changes["soil_ph"]

    after = evaluate(simulated, custom_sequences=[
        {"sequence": rotation["sequence"], "label": rotation["label"]}
        for rotation in before["rotations"]
    ])

    before_by_label = {rotation["label"]: rotation for rotation in before["rotations"]}
    deltas = []
    for rotation in after["rotations"]:
        original = before_by_label.get(rotation["label"])
        deltas.append(
            {
                "label": rotation["label"],
                "sequence_names": rotation["sequence_names"],
                "before": original["score"] if original else None,
                "after": rotation["score"],
                "delta": round(rotation["score"] - original["score"], 1) if original else None,
                "rank_before": original["rank"] if original else None,
                "rank_after": rotation["rank"],
            }
        )
    deltas.sort(key=lambda item: item["after"], reverse=True)
    return {
        "changes": changes,
        "before": before,
        "after": after,
        "deltas": deltas,
        "interpretation": (
            "Under the changed assumptions the ranking "
            + ("changed." if any(item["rank_before"] != item["rank_after"] for item in deltas) else "did not change.")
        ),
    }

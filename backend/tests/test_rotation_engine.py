from __future__ import annotations

from datetime import date

import pytest

from app.services import rotation_engine


def make_context(**overrides) -> dict:
    monthly = {
        1: 2.0, 2: 3.0, 3: 8.0, 4: 25.0, 5: 60.0, 6: 120.0,
        7: 150.0, 8: 140.0, 9: 90.0, 10: 45.0, 11: 15.0, 12: 4.0,
    }
    context = {
        "start_date": date(2026, 11, 1),
        "monthly_rain": monthly,
        "annual_rain_mm": 662.0,
        "mean_t2m_c": 24.0,
        "heat_days_per_year": 40,
        "rain_anomaly_90d_pct": -22.0,
        "climate_confidence": "high",
        "irrigation": "rainfed",
        "soil_texture": "loam",
        "soil_ph": 6.4,
        "organic_carbon_pct": 0.6,
        "current_crop": "maize",
        "previous_crops": ["maize"],
        "preferred_crops": ["maize", "chickpea"],
        "priority": "water",
        "area_ha": 2.0,
        "forecast_provider": "Open-Meteo",
        "forecast_rain_14d": 12.0,
        "weights": rotation_engine.WEIGHT_PRESETS["water"],
    }
    context.update(overrides)
    return context


def test_catalog_loads() -> None:
    catalog = rotation_engine.load_catalog()
    assert len(catalog["crops"]) >= 30
    assert "maize" in catalog["crops"]
    assert catalog["crops"]["chickpea"]["n_fixation_kg_ha"] > 0


def test_evaluate_returns_ranked_candidates() -> None:
    result = rotation_engine.evaluate(make_context())
    rotations = result["rotations"]
    assert 3 <= len(rotations) <= 5
    scores = [item["score"] for item in rotations]
    assert scores == sorted(scores, reverse=True)
    assert rotations[0]["rank"] == 1
    assert result["winner"]["label"] == rotations[0]["label"]
    for item in rotations:
        assert 0 <= item["score"] <= 100
        assert set(item["criteria"]) == {"water", "climate", "soil", "pest", "preference"}
        assert item["confidence"] in {"high", "medium", "low"}


def test_continuous_cropping_penalised_on_pest_break() -> None:
    result = rotation_engine.evaluate(make_context())
    rotations = {item["label"]: item for item in result["rotations"]}
    baseline = rotations["Strategy A — Current practice"]
    legume = rotations["Strategy B — Legume break"]
    assert baseline["criteria"]["pest"]["score"] < legume["criteria"]["pest"]["score"]


def test_water_intensive_crop_penalised_when_rainfed() -> None:
    rainfed = rotation_engine.evaluate(make_context())
    irrigated = rotation_engine.evaluate(make_context(irrigation="full"))

    def score_for_low_water(result: dict) -> float:
        return next(
            item["score"] for item in result["rotations"] if item["label"] == "Strategy C — Water-conscious"
        )

    assert score_for_low_water(irrigated) > score_for_low_water(rainfed)


def test_heavy_water_crop_ranked_below_low_water_option() -> None:
    context = make_context()
    catalog = rotation_engine.load_catalog()["crops"]
    result = rotation_engine.evaluate(
        context,
        custom_sequences=[
            {"label": "Sugarcane year", "sequence": ["sugarcane", "sugarcane"]},
            {"label": "Millet year", "sequence": ["pearl_millet", "cowpea"]},
        ],
    )
    by_label = {item["label"]: item for item in result["rotations"]}
    assert by_label["Millet year"]["criteria"]["water"]["score"] > by_label["Sugarcane year"]["criteria"]["water"]["score"]
    assert by_label["Millet year"]["score"] > by_label["Sugarcane year"]["score"]


def test_missing_soil_data_reduces_confidence() -> None:
    full = rotation_engine.evaluate(make_context())
    missing = rotation_engine.evaluate(make_context(soil_ph=None, organic_carbon_pct=None))
    assert missing["rotations"][0]["confidence_value"] <= full["rotations"][0]["confidence_value"]


def test_simulation_changes_water_outlook() -> None:
    context = make_context()
    result = rotation_engine.simulate(context, [], {"rainfall_change_pct": -30})
    assert len(result["deltas"]) >= 3
    assert result["interpretation"]


def test_unknown_crop_round_trip_raises() -> None:
    base = rotation_engine.evaluate(make_context())
    sequences = [
        {"label": item["label"], "sequence": item["sequence"]}
        for item in base["rotations"]
    ]
    replay = rotation_engine.evaluate(make_context(), custom_sequences=sequences)
    assert [item["score"] for item in replay["rotations"]] == sorted(
        [item["score"] for item in replay["rotations"]], reverse=True
    )

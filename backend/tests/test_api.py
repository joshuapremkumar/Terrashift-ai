from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "terrashift_test.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB.as_posix()}"
os.environ["CACHE_DIR"] = str(Path(tempfile.gettempdir()) / "terrashift_test_cache")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.api import rotations as rotations_api  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["providers"]["nasa_power"] is True


def test_config_never_leaks_secrets(client) -> None:
    response = client.get("/api/config")
    assert response.status_code == 200
    text = response.text.lower()
    for word in ("password", "api_key", "secret", "bearer"):
        assert word not in text


def test_crops_catalogue(client) -> None:
    response = client.get("/api/crops")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 30
    ids = {crop["id"] for crop in body["crops"]}
    assert {"maize", "chickpea", "pearl_millet"} <= ids


def test_farm_lifecycle(client) -> None:
    payload = {
        "name": "Karnataka test farm",
        "latitude": 13.34,
        "longitude": 77.1,
        "soil_texture": "loam",
        "soil_ph": 6.4,
        "irrigation": "rainfed",
        "current_crop": "maize",
        "previous_crops": ["maize"],
        "preferred_crops": ["chickpea"],
        "priority": "water",
    }
    created = client.post("/api/farms", json=payload)
    assert created.status_code == 201, created.text
    farm = created.json()
    assert farm["id"].startswith("farm_")

    fetched = client.get(f"/api/farms/{farm['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == payload["name"]

    patched = client.patch(f"/api/farms/{farm['id']}", json={"priority": "soil"})
    assert patched.status_code == 200
    assert patched.json()["priority"] == "soil"

    listed = client.get("/api/farms")
    assert any(item["id"] == farm["id"] for item in listed.json())

    deleted = client.delete(f"/api/farms/{farm['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/farms/{farm['id']}").status_code == 404


def test_unknown_crop_rejected(client) -> None:
    payload = {
        "name": "Bad crop farm",
        "latitude": 10.0,
        "longitude": 76.0,
        "current_crop": "dragonfruit",
    }
    response = client.post("/api/farms", json=payload)
    assert response.status_code == 422
    assert "dragonfruit" in response.text


def test_validation_rejects_bad_coordinates(client) -> None:
    response = client.post(
        "/api/farms",
        json={"name": "Broken", "latitude": 120, "longitude": 200},
    )
    assert response.status_code == 422


def test_sample_farms_available(client) -> None:
    response = client.get("/api/sample-farms")
    assert response.status_code == 200
    keys = {farm["key"] for farm in response.json()["farms"]}
    assert {"karnataka", "iowa", "machakos", "punjab"} <= keys


SEQUENCE_CONTEXT = {
    "start_date": "2026-06-01",
    "monthly_rain": {
        "1": 2, "2": 3, "3": 8, "4": 25, "5": 60, "6": 120,
        "7": 150, "8": 140, "9": 90, "10": 45, "11": 15, "12": 4,
    },
    "irrigation": "rainfed",
    "soil_texture": "loam",
    "soil_ph": 6.4,
    "priority": "water",
}


def test_rotation_score_accepts_iso_start_date(client) -> None:
    response = client.post(
        "/api/rotation/score",
        json={"sequence_ids": ["maize", "chickpea", "pearl_millet"], "context": SEQUENCE_CONTEXT},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["context_used"]["start_date"] == "2026-06-01"
    assert 0 <= body["score"] <= 100
    assert set(body["trade_offs"]) == {"gains", "losses", "summary"}
    assert date.fromisoformat(body["schedule"][0]["planting"]) >= date(2026, 6, 1)


def test_rotation_plural_alias_resolves(client) -> None:
    singular = client.post(
        "/api/rotation/score",
        json={"sequence_ids": ["maize", "chickpea"], "context": SEQUENCE_CONTEXT},
    )
    plural = client.post(
        "/api/rotations/score",
        json={"sequence_ids": ["maize", "chickpea"], "context": SEQUENCE_CONTEXT},
    )
    assert singular.status_code == 200
    assert plural.status_code == 200
    assert singular.json()["score"] == plural.json()["score"]


def test_rotation_score_rejects_unknown_crop(client) -> None:
    response = client.post(
        "/api/rotation/score",
        json={"sequence_ids": ["dragonfruit"], "context": SEQUENCE_CONTEXT},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["unknown"] == ["dragonfruit"]


def test_rotation_score_rejects_bad_start_date(client) -> None:
    response = client.post(
        "/api/rotation/score",
        json={"sequence_ids": ["maize"], "context": {"start_date": "not-a-date"}},
    )
    assert response.status_code == 422
    assert "start_date" in response.text


SAMPLE_ANALYSIS = {
    "data_source": "NASA POWER",
    "synthetic": False,
    "monthly_climatology": [
        {"month": month, "rain_mm": rain, "mean_t2m_c": 24.0}
        for month, rain in enumerate(
            [2, 3, 8, 25, 60, 120, 150, 140, 90, 45, 15, 4], start=1
        )
    ],
    "baseline": {"annual_rain_mm": 662.0, "annual_mean_t2m_c": 24.0},
    "windows": {"recent_90d": {"heat_days_35": 40}},
    "anomalies": {"rain_90d_pct": -22.0},
    "data_quality": {"confidence": "high"},
    "forecast": {"provider": "Open-Meteo", "rain_mm_14d": 12.0},
}


def test_farm_rotation_and_simulate_endpoints(client, monkeypatch) -> None:
    async def fake_analysis(farm: dict) -> dict:
        return SAMPLE_ANALYSIS

    monkeypatch.setattr(rotations_api.analysis_service, "build_analysis", fake_analysis)

    created = client.post(
        "/api/farms",
        json={
            "name": "Rotation endpoint farm",
            "latitude": 13.34,
            "longitude": 77.1,
            "current_crop": "maize",
            "soil_ph": 6.4,
            "priority": "water",
        },
    )
    assert created.status_code == 201, created.text
    farm_id = created.json()["id"]

    ranked = client.post(f"/api/farms/{farm_id}/rotations", json={})
    assert ranked.status_code == 200, ranked.text
    body = ranked.json()
    assert body["farm_id"] == farm_id
    assert body["analysis_summary"]["data_source"] == "NASA POWER"
    assert 3 <= len(body["rotations"]) <= 5
    assert body["rotations"][0]["rank"] == 1

    simulated = client.post(f"/api/farms/{farm_id}/simulate", json={"rainfall_change_pct": -25})
    assert simulated.status_code == 200, simulated.text
    assert simulated.json()["deltas"]
    assert simulated.json()["interpretation"]

    empty = client.post(f"/api/farms/{farm_id}/simulate", json={})
    assert empty.status_code == 422

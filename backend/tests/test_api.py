from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "terrashift_test.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB.as_posix()}"
os.environ["CACHE_DIR"] = str(Path(tempfile.gettempdir()) / "terrashift_test_cache")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


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


def test_demo_farms_available(client) -> None:
    response = client.get("/api/demo-farms")
    assert response.status_code == 200
    keys = {farm["key"] for farm in response.json()["farms"]}
    assert {"karnataka", "iowa", "machakos", "punjab"} <= keys

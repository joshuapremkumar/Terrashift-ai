from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..config import settings

router = APIRouter(tags=["meta"])

SAMPLE_FILE = Path(__file__).resolve().parent.parent / "data" / "sample_farms.json"


@router.get("/api/config")
def runtime_config() -> dict:
    """Which capabilities are live. Never returns credentials."""
    return {
        "app": settings.app_name,
        "version": settings.version,
        "environment": settings.environment,
        "providers": {
            "nasa_power": {"enabled": True, "requires_credentials": False},
            "nasa_gibs": {"enabled": True, "requires_credentials": False},
            "meteomatics": {
                "enabled": settings.meteomatics_enabled,
                "requires_credentials": True,
                "fallback": "Open-Meteo (labelled in responses)",
            },
            "earthdata_ndvi": {"enabled": settings.earthdata_enabled, "requires_credentials": True},
            "tavily": {"enabled": settings.tavily_enabled, "requires_credentials": True},
            "vultr_inference": {"enabled": settings.vultr_enabled, "requires_credentials": True},
        },
        "scoring": {
            "mode": "deterministic rule-based engine",
            "llm_role": "explanation only; output validated against computed numbers",
        },
    }


@router.post("/api/cache/clear")
def clear_cache() -> dict:
    from ..services import cache

    cache.clear()
    return {"cleared": True}


@router.get("/api/sample-farms")
def sample_farms() -> JSONResponse:
    """Ready-made farm profiles that show global range and different constraints."""
    payload = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    payload["generated"] = date.today().isoformat()
    return JSONResponse(payload)


@router.get("/api")
def api_index() -> dict:
    return {
        "name": settings.app_name,
        "docs": "/docs",
        "health": "/health",
        "ui": "/",
        "note": "TerraShift AI backend. The dashboard is served at /.",
    }

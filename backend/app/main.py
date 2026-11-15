from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import analysis, farm, meta, reports, rotations, satellite, weather
from .config import settings
from .db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger("terrashift")

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("TerraShift AI %s started (%s)", settings.version, settings.environment)
    logger.info("Meteomatics: %s | Tavily: %s | Vultr inference: %s",
                "on" if settings.meteomatics_enabled else "off",
                "on" if settings.tavily_enabled else "off",
                "on" if settings.vultr_enabled else "off")
    yield


app = FastAPI(
    title="TerraShift AI",
    version=settings.version,
    description=(
        "NASA-powered agricultural adaptation and crop-rotation intelligence.\n\n"
        "Deterministic scoring engine + traceable evidence. Observations, forecasts and "
        "research are kept separate. Missing data is penalised, never invented."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal error",
            "message": str(exc),
            "path": request.url.path,
        },
    )


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.version,
        "time": datetime.now(timezone.utc).isoformat(),
        "providers": {
            "meteomatics": settings.meteomatics_enabled,
            "tavily": settings.tavily_enabled,
            "vultr_inference": settings.vultr_enabled,
            "earthdata": settings.earthdata_enabled,
            "nasa_power": True,
        },
    }


for module in (farm, weather, satellite, analysis, rotations, reports, meta):
    # ``rotations`` owns /api/crops plus both /api/rotation/score and
    # /api/rotations/score aliases, and is included exactly once here.
    app.include_router(module.router)

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:  # pragma: no cover
    logger.warning("Frontend directory not found at %s", FRONTEND_DIR)

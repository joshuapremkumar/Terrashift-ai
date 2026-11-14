"""Runtime configuration for TerraShift AI.

Every value is read from the environment (optionally from a local ``.env``
file). No secret is ever hardcoded, and no secret is ever returned by the API.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so the project needs no extra dependency."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value:
            continue
        os.environ.setdefault(key, value)


_load_dotenv(PROJECT_ROOT / ".env")
_load_dotenv(BACKEND_DIR / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "TerraShift AI")
    version: str = os.getenv("APP_VERSION", "0.1.0")
    environment: str = os.getenv("ENVIRONMENT", "development")

    database_url: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'terrashift.db').as_posix()}"
    )
    cache_dir: Path = Path(os.getenv("CACHE_DIR", str(BACKEND_DIR / ".cache")))
    cache_ttl_seconds: int = _env_int("CACHE_TTL_SECONDS", 6 * 60 * 60)

    # --- Data providers -------------------------------------------------
    # NASA POWER needs no credentials.
    nasa_power_base_url: str = os.getenv(
        "NASA_POWER_BASE_URL", "https://power.larc.nasa.gov/api/temporal/daily/point"
    )
    # NASA GIBS provides keyless raster tiles for the map layers.
    nasa_gibs_base_url: str = os.getenv(
        "NASA_GIBS_BASE_URL", "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
    )

    meteomatics_username: str = os.getenv("METEOMATICS_USERNAME", "")
    meteomatics_password: str = os.getenv("METEOMATICS_PASSWORD", "")
    meteomatics_base_url: str = os.getenv(
        "METEOMATICS_BASE_URL", "https://api.meteomatics.com"
    )

    # Keyless forecast fallback. Results are labelled with their real source.
    open_meteo_base_url: str = os.getenv(
        "OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1/forecast"
    )

    earthdata_username: str = os.getenv("EARTHDATA_USERNAME", "")
    earthdata_password: str = os.getenv("EARTHDATA_PASSWORD", "")

    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    tavily_base_url: str = os.getenv("TAVILY_BASE_URL", "https://api.tavily.com/search")

    vultr_api_key: str = os.getenv("VULTR_INFERENCE_API_KEY", "")
    vultr_base_url: str = os.getenv(
        "VULTR_INFERENCE_BASE_URL", "https://api.vultrinference.com/v1"
    )
    vultr_model: str = os.getenv("VULTR_INFERENCE_MODEL", "")

    # --- Product behaviour ----------------------------------------------
    http_timeout_seconds: int = _env_int("HTTP_TIMEOUT_SECONDS", 25)
    baseline_years: int = _env_int("BASELINE_YEARS", 10)
    min_baseline_days: int = _env_int("MIN_BASELINE_DAYS", 365)

    @property
    def meteomatics_enabled(self) -> bool:
        return bool(self.meteomatics_username and self.meteomatics_password)

    @property
    def tavily_enabled(self) -> bool:
        return bool(self.tavily_api_key)

    @property
    def vultr_enabled(self) -> bool:
        return bool(self.vultr_api_key and self.vultr_model)

    @property
    def earthdata_enabled(self) -> bool:
        return bool(self.earthdata_username and self.earthdata_password)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()

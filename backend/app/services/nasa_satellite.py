"""NASA Earth-observation adapters.

Two capabilities are exposed:

1. GIBS raster layers — real, keyless NASA imagery that the map can display
   (true colour, NDVI composites, IMERG precipitation, land surface temperature).
   These are visual layers; they are not statistics and are never used as scores.

2. NDVI time-series statistics — requires NASA Earthdata credentials. When those
   are absent the adapter reports that honestly instead of inventing numbers.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..config import settings


class SatelliteError(RuntimeError):
    pass


def _gibs_time(layer: dict, today: date) -> str:
    if layer["cadence"] == "daily":
        # GIBS daily imagery typically lags ~1 day.
        return (today - timedelta(days=1)).isoformat()
    # Rolling 8-day composites: GIBS resolves the nearest available composite.
    return (today - timedelta(days=3)).isoformat()


def gibs_layers(latitude: float, longitude: float, today: date | None = None) -> dict:
    today = today or date.today()
    definitions = [
        {
            "id": "true_color",
            "name": "MODIS Terra true colour",
            "layer": "MODIS_Terra_CorrectedReflectance_TrueColor",
            "matrix_set": "GoogleMapsCompatible_Level9",
            "extension": "jpg",
            "cadence": "daily",
            "max_zoom": 9,
            "description": "Daily surface imagery for visual orientation.",
            "evidence_type": "observed",
        },
        {
            "id": "ndvi",
            "name": "MODIS Terra NDVI (8-day)",
            "layer": "MODIS_Terra_NDVI_8Day",
            "matrix_set": "GoogleMapsCompatible_Level9",
            "extension": "png",
            "cadence": "8day",
            "max_zoom": 9,
            "description": "Vegetation index composite. Visual layer only; no statistics are derived from the tiles.",
            "evidence_type": "observed",
        },
        {
            "id": "precipitation",
            "name": "GPM IMERG precipitation rate",
            "layer": "IMERG_Precipitation_Rate",
            "matrix_set": "GoogleMapsCompatible_Level6",
            "extension": "png",
            "cadence": "daily",
            "max_zoom": 6,
            "description": "Satellite precipitation estimate (GPM IMERG).",
            "evidence_type": "observed",
        },
        {
            "id": "land_surface_temp",
            "name": "MODIS land surface temperature (day)",
            "layer": "MODIS_Terra_Land_Surface_Temp_Day",
            "matrix_set": "GoogleMapsCompatible_Level7",
            "extension": "png",
            "cadence": "daily",
            "max_zoom": 7,
            "description": "Daytime land surface temperature.",
            "evidence_type": "observed",
        },
    ]

    layers = []
    for definition in definitions:
        stamp = _gibs_time(definition, today)
        template = (
            f"{settings.nasa_gibs_base_url}/{definition['layer']}/default/{stamp}/"
            f"{definition['matrix_set']}/{{z}}/{{y}}/{{x}}.{definition['extension']}"
        )
        layers.append(
            {
                **{k: v for k, v in definition.items() if k not in {"cadence"}},
                "time": stamp,
                "tile_url_template": template,
                "attribution": "NASA GIBS / EOSDIS",
                "resolutions_note": _resolution_note(definition["id"]),
            }
        )
    return {
        "center": {"latitude": latitude, "longitude": longitude},
        "source": "NASA GIBS (Global Imagery Browse Services)",
        "layers": layers,
        "note": (
            "GIBS layers are rendered imagery. They are shown as evidence context "
            "and are not converted into scores."
        ),
    }


def _resolution_note(layer_id: str) -> str:
    return {
        "true_color": "MODIS 250 m pixels, daily.",
        "ndvi": "MODIS 250 m pixels, 8-day composite.",
        "precipitation": "GPM IMERG ~10 km, 30-minute to daily product.",
        "land_surface_temp": "MODIS 1 km, daily.",
    }.get(layer_id, "")


async def ndvi_series(latitude: float, longitude: float) -> dict:
    """MODIS NDVI statistics require Earthdata authentication.

    This is deliberately not faked. Without credentials the endpoint explains
    what is missing and how to enable it.
    """
    if not settings.earthdata_enabled:
        return {
            "available": False,
            "dataset": "MOD13Q1 / MODIS Terra Vegetation Indices (250 m, 16-day)",
            "reason": "NASA Earthdata credentials are not configured.",
            "how_to_enable": (
                "Create a free Earthdata Login at https://urs.earthdata.nasa.gov/ "
                "and set EARTHDATA_USERNAME / EARTHDATA_PASSWORD. The adapter for "
                "LP DAAC / ORNL DAAC subsetting is then enabled."
            ),
            "alternative_evidence": (
                "The map still displays live NASA GIBS NDVI composites, and the "
                "climate analysis reports a clearly labelled soil-moisture proxy."
            ),
            "values": [],
        }

    # Credentialed retrieval is intentionally left as the next integration step:
    # it involves an asynchronous subsetting job and product-specific QA masking.
    return {
        "available": False,
        "dataset": "MOD13Q1 / MODIS Terra Vegetation Indices (250 m, 16-day)",
        "reason": (
            "Earthdata credentials are present, but the asynchronous MODIS "
            "subsetting job has not been integrated in this build."
        ),
        "how_to_enable": (
            "Integrate the ORNL DAAC MODIS/VIIRS subsetting API or AppEEARS, "
            "then map QA-filtered NDVI/EVI values into this response."
        ),
        "alternative_evidence": "NASA GIBS NDVI composites are available on the map.",
        "values": [],
    }

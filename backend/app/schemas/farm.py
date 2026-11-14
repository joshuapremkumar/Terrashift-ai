from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SoilTexture = Literal[
    "sand",
    "loamy sand",
    "sandy loam",
    "loam",
    "silt loam",
    "clay loam",
    "clay",
]
Irrigation = Literal["rainfed", "limited", "supplemental", "full"]
Priority = Literal["balanced", "water", "soil", "profit", "resilience"]


class FarmBase(BaseModel):
    name: str = Field(min_length=2, max_length=120, examples=["Karnataka rain-fed farm"])
    latitude: float = Field(ge=-90, le=90, examples=[13.34])
    longitude: float = Field(ge=-180, le=180, examples=[77.10])
    area_ha: float | None = Field(default=None, gt=0, le=100_000)

    country: str | None = Field(default=None, max_length=80)
    region: str | None = Field(default=None, max_length=120)

    soil_texture: SoilTexture = "loam"
    soil_ph: float | None = Field(default=None, ge=3.0, le=10.0)
    organic_carbon_pct: float | None = Field(default=None, ge=0.0, le=20.0)
    irrigation: Irrigation = "rainfed"

    current_crop: str = "maize"
    previous_crops: list[str] = Field(default_factory=list, max_length=8)
    preferred_crops: list[str] = Field(default_factory=list, max_length=8)
    priority: Priority = "balanced"

    @field_validator("previous_crops", "preferred_crops")
    @classmethod
    def _clean_crops(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            slug = item.strip().lower().replace(" ", "_")
            if slug and slug not in seen:
                seen.append(slug)
        return seen

    @field_validator("current_crop")
    @classmethod
    def _clean_current_crop(cls, value: str) -> str:
        return value.strip().lower().replace(" ", "_")


class FarmCreate(FarmBase):
    pass


class FarmUpdate(BaseModel):
    name: str | None = None
    soil_texture: SoilTexture | None = None
    soil_ph: float | None = Field(default=None, ge=3.0, le=10.0)
    organic_carbon_pct: float | None = Field(default=None, ge=0.0, le=20.0)
    irrigation: Irrigation | None = None
    current_crop: str | None = None
    previous_crops: list[str] | None = None
    preferred_crops: list[str] | None = None
    priority: Priority | None = None


class FarmOut(FarmBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime

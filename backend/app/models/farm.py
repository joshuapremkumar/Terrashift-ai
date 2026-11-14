from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return f"farm_{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Farm(Base):
    __tablename__ = "farms"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    area_ha: Mapped[float | None] = mapped_column(Float, nullable=True)
    boundary_geojson: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)

    soil_texture: Mapped[str] = mapped_column(String(40), default="loam")
    soil_ph: Mapped[float | None] = mapped_column(Float, nullable=True)
    organic_carbon_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    irrigation: Mapped[str] = mapped_column(String(20), default="rainfed")

    current_crop: Mapped[str] = mapped_column(String(60), default="maize")
    previous_crops: Mapped[list] = mapped_column(JSON, default=list)
    preferred_crops: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[str] = mapped_column(String(20), default="balanced")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

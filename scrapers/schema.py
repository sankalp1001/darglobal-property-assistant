"""Unified record schemas shared by both scrapers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Listing(BaseModel):
    id: str
    source: str  # "darglobal" | "wasalt"
    title: str
    property_type: Optional[str] = None
    listing_type: Optional[str] = None  # sale | rent | off-plan project
    city: Optional[str] = None
    district: Optional[str] = None
    country: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    price_text: Optional[str] = None
    bedrooms_min: Optional[int] = None
    bedrooms_max: Optional[int] = None
    bathrooms: Optional[int] = None
    area_sqm_min: Optional[float] = None
    area_sqm_max: Optional[float] = None
    area_text: Optional[str] = None
    amenities: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    status: Optional[str] = None
    handover_date: Optional[str] = None
    developer: Optional[str] = None
    url: str
    scraped_at: str = Field(default_factory=now_iso)
    extra: dict = Field(default_factory=dict)  # e.g. FAQs, nearby landmarks


class Page(BaseModel):
    id: str
    source: str
    url: str
    title: str
    text: str
    scraped_at: str = Field(default_factory=now_iso)

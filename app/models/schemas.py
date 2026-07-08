from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────────

class FacilityType(str, Enum):
    hospital = "hospital"
    grocery = "grocery"
    ev_charger = "ev_charger"
    school = "school"


class TravelMode(str, Enum):
    walk = "walk"
    drive = "drive"


# ── Request bodies ──────────────────────────────────────────────────────────────

class IsochroneRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90, example=33.6405, description="Origin latitude (WGS84)")
    lon: float = Field(..., ge=-180, le=180, example=-117.8443, description="Origin longitude (WGS84)")
    travel_time_minutes: int = Field(..., ge=5, le=30, example=15, description="Budget in minutes (5–30)")
    mode: TravelMode = Field(TravelMode.walk, description="Network type: walk (5 km/h) or drive (OSM speed limits)")

    model_config = {"json_schema_extra": {
        "examples": [{
            "lat": 33.6405,
            "lon": -117.8443,
            "travel_time_minutes": 15,
            "mode": "walk",
        }]
    }}


class CoverageGapRequest(BaseModel):
    minx: float = Field(..., example=-118.05, description="Bounding box min longitude")
    miny: float = Field(..., example=33.55, description="Bounding box min latitude")
    maxx: float = Field(..., example=-117.70, description="Bounding box max longitude")
    maxy: float = Field(..., example=33.85, description="Bounding box max latitude")
    facility_type: FacilityType
    travel_time_minutes: int = Field(..., ge=5, le=30, example=15)
    mode: TravelMode = TravelMode.walk

    model_config = {"json_schema_extra": {
        "examples": [{
            "minx": -118.05,
            "miny": 33.55,
            "maxx": -117.70,
            "maxy": 33.85,
            "facility_type": "grocery",
            "travel_time_minutes": 15,
            "mode": "walk",
        }]
    }}


# ── GeoJSON primitives (RFC 7946) ───────────────────────────────────────────────

class GeoJSONGeometry(BaseModel):
    type: str
    coordinates: Any


class GeoJSONFeature(BaseModel):
    type: str = "Feature"
    geometry: GeoJSONGeometry
    properties: dict[str, Any]
    bbox: list[float] | None = None


class GeoJSONFeatureCollection(BaseModel):
    type: str = "FeatureCollection"
    features: list[GeoJSONFeature]
    bbox: list[float] | None = None

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.schemas import FacilityType
from app.services.spatial_service import explain_nearest, nearest_facilities

router = APIRouter(prefix="/facilities", tags=["Facilities"])


@router.get(
    "/nearest",
    summary="K-nearest facilities — GIST-indexed two-phase query",
    description="""
Returns the **k nearest** facilities of a given type within a search radius.

### Query strategy
```
Phase 1 — ST_DWithin(geom::geography, origin, radius_m)
  Uses the GIST spatial index → O(log n) pre-filter.
  Only rows within the radius enter Phase 2.

Phase 2 — ST_Distance(geom::geography, origin)
  Exact geodesic distance on the pre-filtered set → ORDER BY → LIMIT k.
```

This is the correct pattern for spatial k-NN in PostGIS.
The naive `ORDER BY ST_Distance(...) LIMIT k` triggers a full sequential scan
on large tables. See `/facilities/query-plan` for live `EXPLAIN ANALYZE` output.

### Response
GeoJSON FeatureCollection — each Feature carries `distance_m` in properties,
sorted ascending. CRS: EPSG:4326.
    """,
    response_description="GeoJSON FeatureCollection — sorted by distance_m",
)
async def get_nearest(
    lat: float = Query(..., ge=-90, le=90, json_schema_extra={"example": 33.6405}, description="Origin latitude"),
    lon: float = Query(..., ge=-180, le=180, json_schema_extra={"example": -117.8443}, description="Origin longitude"),
    facility_type: FacilityType = Query(..., description="POI category"),
    k: int = Query(5, ge=1, le=10, description="Number of results (max 10)"),
    radius_km: float = Query(10.0, ge=0.5, le=50.0, description="Search radius in km"),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await nearest_facilities(
            db, lat, lon, facility_type.value, k, radius_km * 1_000
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/query-plan",
    summary="EXPLAIN ANALYZE — live index scan evidence",
    description="""
Returns the PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` output for the
nearest-facility query at the given parameters.

Demonstrates:
- **With GIST index** → `Index Scan using idx_facilities_geom`
- Rows removed by filter vs rows actually scanned

The README benchmark table is generated from this endpoint.
    """,
    response_description="Raw PostgreSQL query plan as a string",
)
async def get_query_plan(
    lat: float = Query(33.6405),
    lon: float = Query(-117.8443),
    facility_type: FacilityType = Query(FacilityType.grocery),
    radius_km: float = Query(5.0),
    db: AsyncSession = Depends(get_db),
):
    try:
        plan = await explain_nearest(db, lat, lon, facility_type.value, radius_km * 1_000)
        return {"query_plan": plan}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

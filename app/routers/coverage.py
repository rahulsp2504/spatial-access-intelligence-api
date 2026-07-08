"""
/coverage/gap — Spatial set algebra for service-area gap analysis.

Algorithm:
  1. PostGIS: find all facilities of `facility_type` within the bbox
  2. asyncio.gather: compute one isochrone per facility (parallel)
  3. PostGIS: ST_Union(isochrones) → ST_Difference(bbox, union) → gap polygon
  4. Return GeoJSON Feature with coverage_pct + gap_area_km2

Concurrency note: isochrone tasks are CPU/IO bound (osmnx + networkx).
asyncio.gather runs them concurrently within the event loop; for truly
CPU-bound workloads a ProcessPoolExecutor would be the next step.
Facilities are capped at 20 per request to avoid memory exhaustion.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from shapely.geometry import box, mapping
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.schemas import CoverageGapRequest
from app.services.isochrone_service import compute_isochrone
from app.services.spatial_service import coverage_gap

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/coverage", tags=["Coverage"])

_MAX_FACILITIES = 20  # guard against bbox-too-large abuse


@router.post(
    "/gap",
    summary="Uncovered area within a bounding box",
    description="""
Computes the polygon representing areas **not** reachable from any facility
of a given type within the bounding box at the specified travel-time budget.

### Algorithm
```
1. SELECT facilities WHERE ST_Within(geom, bbox_envelope)          [PostGIS]
2. asyncio.gather(compute_isochrone(f) for f in facilities)        [parallel]
3. ST_Union(all_isochrone_polygons)                                [PostGIS]
4. ST_Difference(bbox_polygon, unioned_isochrones)  →  gap        [PostGIS]
5. coverage_pct = covered_area / bbox_area × 100
```

Facilities capped at **20 per request** to keep memory bounded.
For large bounding boxes, tile the request or increase `travel_time_minutes`.

### Response
GeoJSON Feature — the gap polygon — with properties:
- `coverage_pct` (0–100)
- `gap_area_km2`
- `facility_count` (number of isochrones computed)
    """,
    response_description="GeoJSON Feature — uncovered area polygon",
)
async def post_coverage_gap(req: CoverageGapRequest, db: AsyncSession = Depends(get_db)):
    # Basic bbox sanity check
    if req.minx >= req.maxx or req.miny >= req.maxy:
        raise HTTPException(status_code=422, detail="Invalid bbox: minx/miny must be less than maxx/maxy")

    try:
        # ── Step 1: facilities inside bbox ─────────────────────────────────────
        sql = text(
            """
            SELECT
                id,
                name,
                ST_Y(geom::geometry) AS lat,
                ST_X(geom::geometry) AS lon
            FROM facilities
            WHERE
                facility_type = :facility_type
                AND ST_Within(geom, ST_MakeEnvelope(:minx, :miny, :maxx, :maxy, 4326))
            LIMIT :cap
            """
        )
        result = await db.execute(
            sql,
            {
                "facility_type": req.facility_type.value,
                "minx": req.minx, "miny": req.miny,
                "maxx": req.maxx, "maxy": req.maxy,
                "cap": _MAX_FACILITIES,
            },
        )
        facilities = result.fetchall()

        if not facilities:
            logger.info("No %s facilities in bbox — returning full bbox as gap", req.facility_type.value)
            gap_geom = box(req.minx, req.miny, req.maxx, req.maxy)
            return {
                "type": "Feature",
                "geometry": mapping(gap_geom),
                "properties": {
                    "coverage_pct": 0.0,
                    "facility_type": req.facility_type.value,
                    "facility_count": 0,
                    "gap_area_km2": round(gap_geom.area * (111.0 ** 2), 4),
                    "note": "No facilities of this type found in bbox",
                },
            }

        # ── Step 2: parallel isochrone computation ─────────────────────────────
        logger.info(
            "Computing %d isochrones (%s, %dmin, %s)",
            len(facilities), req.facility_type.value,
            req.travel_time_minutes, req.mode.value,
        )
        tasks = [
            compute_isochrone(f.lat, f.lon, req.travel_time_minutes, req.mode.value)
            for f in facilities
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_isochrones = [r for r in results if isinstance(r, dict)]
        failed = len(results) - len(valid_isochrones)
        if failed:
            logger.warning("%d/%d isochrones failed (sparse network nodes)", failed, len(results))

        # ── Step 3 & 4: PostGIS set algebra ───────────────────────────────────
        return await coverage_gap(
            db,
            req.minx, req.miny, req.maxx, req.maxy,
            req.facility_type.value,
            valid_isochrones,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Coverage gap computation failed")
        raise HTTPException(status_code=500, detail=str(exc))

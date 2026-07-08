"""
PostGIS spatial query services
================================

nearest_facilities
------------------
Two-phase index-accelerated query (the correct pattern for spatial k-NN):

  Phase 1 — ST_DWithin(...::geography, radius)
    Uses the GIST index for an O(log n) bounding-box pre-filter.
    Only rows whose geography falls within `radius_m` metres reach Phase 2.

  Phase 2 — ST_Distance(...::geography)
    Exact geodesic distance computed only on the pre-filtered set,
    then sorted ascending. This avoids the full sequential scan that
    naive `ORDER BY ST_Distance(...)` triggers.

  The /facilities/query-plan endpoint exposes EXPLAIN ANALYZE output
  so the index scan vs seq-scan tradeoff is visible in the README.

coverage_gap
------------
PostGIS set algebra:

  1. Receive pre-computed isochrone GeoJSON polygons from isochrone_service
  2. Build a UNION ALL subquery, one row per isochrone
  3. ST_Union() → single merged covered area
  4. ST_Difference(bbox_envelope, covered_area) → gap polygon
  5. Compute coverage_pct = covered_area / bbox_area × 100
"""

from __future__ import annotations

import json
import logging

from shapely.geometry import box, mapping
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── Nearest facilities ─────────────────────────────────────────────────────────

async def nearest_facilities(
    db: AsyncSession,
    lat: float,
    lon: float,
    facility_type: str,
    k: int,
    radius_m: float,
) -> dict:
    sql = text(
        """
        SELECT
            id,
            name,
            facility_type,
            address,
            ST_AsGeoJSON(geom)::json            AS geometry,
            ROUND(
                ST_Distance(
                    geom::geography,
                    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
                )::numeric,
                2
            )                                   AS distance_m
        FROM facilities
        WHERE
            facility_type = :facility_type
            AND ST_DWithin(
                    geom::geography,
                    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                    :radius_m
                )
        ORDER BY distance_m
        LIMIT :k
        """
    )

    result = await db.execute(
        sql,
        {"lat": lat, "lon": lon, "facility_type": facility_type, "radius_m": radius_m, "k": k},
    )
    rows = result.fetchall()

    features = [
        {
            "type": "Feature",
            "geometry": row.geometry,
            "properties": {
                "id": row.id,
                "name": row.name,
                "facility_type": row.facility_type,
                "address": row.address,
                "distance_m": float(row.distance_m),
            },
        }
        for row in rows
    ]

    return {
        "type": "FeatureCollection",
        "features": features,
        "bbox": [lon - 0.15, lat - 0.15, lon + 0.15, lat + 0.15],
    }


async def explain_nearest(
    db: AsyncSession,
    lat: float,
    lon: float,
    facility_type: str,
    radius_m: float,
) -> str:
    """
    Return EXPLAIN (ANALYZE, BUFFERS) output for the nearest-facility query.
    Used by /facilities/query-plan and reproduced verbatim in the README
    benchmark table.
    """
    sql = text(
        """
        EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
        SELECT
            id,
            ST_Distance(
                geom::geography,
                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
            ) AS distance_m
        FROM facilities
        WHERE
            facility_type = :facility_type
            AND ST_DWithin(
                    geom::geography,
                    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                    :radius_m
                )
        ORDER BY distance_m
        LIMIT 5
        """
    )
    result = await db.execute(
        sql, {"lat": lat, "lon": lon, "facility_type": facility_type, "radius_m": radius_m}
    )
    return "\n".join(row[0] for row in result.fetchall())


# ── Coverage gap ───────────────────────────────────────────────────────────────

async def coverage_gap(
    db: AsyncSession,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    facility_type: str,
    isochrone_features: list[dict],
) -> dict:
    """
    Compute the uncovered polygon within `bbox` using PostGIS set algebra.

    If no isochrones are provided (no facilities in bbox), the entire
    bbox polygon is returned as the gap with coverage_pct = 0.
    """
    if not isochrone_features:
        gap_geom = box(minx, miny, maxx, maxy)
        return {
            "type": "Feature",
            "geometry": mapping(gap_geom),
            "properties": {
                "coverage_pct": 0.0,
                "facility_type": facility_type,
                "facility_count": 0,
                "gap_area_km2": round(gap_geom.area * (111.0 ** 2), 4),
                "note": "No facilities of this type found in bbox",
            },
        }

    # Build UNION ALL subquery — one row per isochrone polygon
    iso_parts = " UNION ALL ".join(
        f"SELECT ST_SetSRID(ST_GeomFromGeoJSON('{json.dumps(f['geometry'])}'), 4326) AS geom"
        for f in isochrone_features
    )

    sql = text(
        f"""
        WITH isochrones AS ({iso_parts}),
        covered AS (
            SELECT ST_Union(geom) AS geom FROM isochrones
        ),
        bbox AS (
            SELECT ST_MakeEnvelope(:minx, :miny, :maxx, :maxy, 4326) AS geom
        ),
        gap AS (
            SELECT ST_Difference(bbox.geom, covered.geom) AS geom
            FROM bbox, covered
        ),
        metrics AS (
            SELECT
                ST_Area(bbox.geom::geography)                               AS bbox_area,
                COALESCE(
                    ST_Area(ST_Intersection(bbox.geom, covered.geom)::geography),
                    0
                )                                                           AS covered_area,
                ST_AsGeoJSON(gap.geom)::json                               AS gap_geom
            FROM bbox, covered, gap
        )
        SELECT
            gap_geom,
            ROUND((covered_area / NULLIF(bbox_area, 0) * 100)::numeric, 2) AS coverage_pct,
            ROUND(((bbox_area - covered_area) / 1e6)::numeric, 4)          AS gap_area_km2
        FROM metrics
        """
    )

    result = await db.execute(
        sql, {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy}
    )
    row = result.fetchone()

    gap_geometry = (
        row.gap_geom
        if row and row.gap_geom
        else {"type": "Polygon", "coordinates": []}
    )
    coverage_pct = float(row.coverage_pct) if row and row.coverage_pct else 0.0
    gap_area = float(row.gap_area_km2) if row and row.gap_area_km2 else None

    logger.info(
        "Coverage gap: %.1f%% covered, gap=%.2f km² (%d facilities)",
        coverage_pct, gap_area or 0, len(isochrone_features),
    )

    return {
        "type": "Feature",
        "geometry": gap_geometry,
        "properties": {
            "coverage_pct": coverage_pct,
            "facility_type": facility_type,
            "facility_count": len(isochrone_features),
            "gap_area_km2": gap_area,
            "crs": "EPSG:4326",
        },
    }

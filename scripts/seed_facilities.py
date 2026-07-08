#!/usr/bin/env python3
"""
Seed the PostGIS facilities table with OSM POIs for Orange County, CA.

Usage:
    python scripts/seed_facilities.py

Requires:
    DATABASE_URL env var or .env file at project root.
    osmnx, asyncpg, python-dotenv installed.

Data sources:
    - OSM via osmnx.features_from_place() (no API key required)
    - hospitality, grocery, EV chargers, schools for Orange County
"""

import asyncio
import os
import sys
from pathlib import Path

# Allow running from repo root or scripts/ dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
import osmnx as ox
from dotenv import load_dotenv

load_dotenv()

PLACE = "Orange County, California, USA"

# OSM tag filters per facility type
FACILITY_TAGS: dict[str, dict] = {
    "hospital": {"amenity": "hospital"},
    "grocery": {"shop": ["supermarket", "grocery"]},
    "ev_charger": {"amenity": "charging_station"},
    "school": {"amenity": ["school", "university", "college"]},
}


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facilities (
            id            SERIAL PRIMARY KEY,
            name          TEXT NOT NULL,
            facility_type TEXT NOT NULL,
            geom          GEOMETRY(POINT, 4326) NOT NULL,
            osm_id        TEXT,
            address       TEXT
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_facilities_geom ON facilities USING GIST (geom)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_facilities_type ON facilities (facility_type)"
    )


async def seed_type(
    conn: asyncpg.Connection, ftype: str, tags: dict
) -> int:
    print(f"  Fetching '{ftype}' from OSM …", end=" ", flush=True)
    try:
        gdf = ox.features_from_place(PLACE, tags=tags)
    except Exception as exc:
        print(f"FAILED ({exc})")
        return 0

    # Use centroid for polygons/multipolygons (e.g. supermarket building footprints)
    gdf = gdf[gdf.geometry.geom_type.isin(["Point", "Polygon", "MultiPolygon"])].copy()
    gdf["centroid"] = gdf.geometry.centroid

    rows: list[tuple] = []
    for _, row in gdf.iterrows():
        raw_name = row.get("name", None)
        name = raw_name if isinstance(raw_name, str) and raw_name.strip() else f"Unknown {ftype}"
        lon = row["centroid"].x
        lat = row["centroid"].y
        # osmnx MultiIndex: (element_type, osmid)
        osm_id = str(row.name[1]) if isinstance(row.name, tuple) else None
        rows.append((name, ftype, lon, lat, osm_id))

    if rows:
        await conn.executemany(
            """
            INSERT INTO facilities (name, facility_type, geom, osm_id)
            VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326), $5)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )

    print(f"{len(rows)} rows inserted")
    return len(rows)


async def main() -> None:
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/spatial_api",
    )
    print(f"Connecting to {db_url} …")
    conn = await asyncpg.connect(db_url)

    print("Ensuring schema …")
    await ensure_schema(conn)

    total = 0
    for ftype, tags in FACILITY_TAGS.items():
        total += await seed_type(conn, ftype, tags)

    await conn.close()
    print(f"\n✓ Seeding complete — {total} facilities total")


if __name__ == "__main__":
    asyncio.run(main())

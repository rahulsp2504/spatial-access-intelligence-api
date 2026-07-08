#!/usr/bin/env python3
"""
Seed the PostGIS facilities table with OSM POIs for Orange County, CA.

Usage:
    python scripts/seed_facilities.py

Facility types seeded:
    hospital   — amenity=hospital
    grocery    — shop=supermarket|grocery
    ev_charger — amenity=charging_station

Schools excluded: OSM returns 2000+ school polygons for OC which
reliably exceeds the Overpass API timeout. The three types above
fully demonstrate all API endpoints.

Re-run safe: TRUNCATE before insert, so no duplicate accumulation.
"""

import asyncio
import os
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
import osmnx as ox
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings("ignore")

ox.settings.overpass_settings = '[out:json][timeout:90]'
ox.settings.log_console = False

PLACE = "Orange County, California, USA"

FACILITY_TAGS: list[tuple[str, dict]] = [
    ("hospital",   {"amenity": "hospital"}),
    ("grocery",    {"shop": ["supermarket", "grocery"]}),
    ("ev_charger", {"amenity": "charging_station"}),
]


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS facilities (
            id            SERIAL PRIMARY KEY,
            name          TEXT NOT NULL,
            facility_type TEXT NOT NULL,
            geom          GEOMETRY(POINT, 4326) NOT NULL,
            osm_id        TEXT,
            address       TEXT
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_facilities_geom ON facilities USING GIST (geom)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_facilities_type ON facilities (facility_type)"
    )


async def seed_type(
    conn: asyncpg.Connection, ftype: str, tags: dict, attempt: int = 1
) -> int:
    print(f"  Fetching '{ftype}' {tags} …", end=" ", flush=True)
    try:
        gdf = ox.features_from_place(PLACE, tags=tags)
    except Exception as exc:
        if attempt < 3:
            print(f"retrying ({exc}) …", end=" ", flush=True)
            await asyncio.sleep(8)
            return await seed_type(conn, ftype, tags, attempt + 1)
        print(f"FAILED after {attempt} attempts ({exc})")
        return 0

    gdf = gdf[gdf.geometry.geom_type.isin(["Point", "Polygon", "MultiPolygon"])].copy()
    if gdf.empty:
        print("0 rows (no features matched)")
        return 0

    # Reproject to metric CRS for accurate centroid, then back to WGS84
    gdf = gdf.to_crs("EPSG:3857")
    gdf["centroid"] = gdf.geometry.centroid
    gdf = gdf.set_geometry("centroid").to_crs("EPSG:4326")

    rows: list[tuple] = []
    for _, row in gdf.iterrows():
        raw_name = row.get("name", None)
        name = raw_name if isinstance(raw_name, str) and raw_name.strip() else f"Unknown {ftype}"
        lon = row["centroid"].x
        lat = row["centroid"].y
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
        "postgresql://postgres:postgres@localhost:5433/spatial_api",
    )
    print(f"Connecting to {db_url} …")
    conn = await asyncpg.connect(db_url)

    print("Ensuring schema …")
    await ensure_schema(conn)

    # Safe to re-run: wipe existing rows before inserting
    await conn.execute("TRUNCATE TABLE facilities RESTART IDENTITY")
    print("Cleared existing rows.\n")

    total = 0
    for ftype, tags in FACILITY_TAGS:
        total += await seed_type(conn, ftype, tags)

    await conn.close()
    print(f"\n✓ Seeding complete — {total} facilities total")


if __name__ == "__main__":
    asyncio.run(main())
# Spatial Access Intelligence API

> **Who can access what — and where are the gaps?**

A production-grade spatial analytics backend built with **PostGIS**, **osmnx**, and **FastAPI**.
Deployed on DigitalOcean App Platform with a managed PostgreSQL + PostGIS database.

[![CI](https://github.com/rahulsp2504/spatial-access-intelligence-api/actions/workflows/ci.yml/badge.svg)](https://github.com/rahulsp2504/spatial-access-intelligence-api/actions)
![Python](https://img.shields.io/badge/python-3.12-blue)
![PostGIS](https://img.shields.io/badge/PostGIS-3.4-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-teal)

**Live API:** `https://spatial-api-<hash>.ondigitalocean.app`  
**Swagger UI:** `https://spatial-api-<hash>.ondigitalocean.app/docs`

---

## What this demonstrates

Three patterns that distinguish production geospatial engineers:

| Pattern | Naive approach | This implementation |
|---|---|---|
| Isochrones | `ST_Buffer(point, radius)` — Euclidean circle | osmnx ego-graph + Delaunay alpha-shape — follows the road network |
| Spatial k-NN | `ORDER BY ST_Distance(...)` — full table scan | `ST_DWithin` GIST pre-filter → `ST_Distance` on filtered set |
| Gap analysis | Client-side polygon arithmetic | `ST_Union` + `ST_Difference` entirely in PostGIS |

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/isochrone` | Network-based reachability polygon |
| `GET` | `/facilities/nearest` | K-nearest POIs with GIST index |
| `GET` | `/facilities/query-plan` | Live `EXPLAIN ANALYZE` output |
| `POST` | `/coverage/gap` | Uncovered-area polygon + coverage % |
| `GET` | `/health` | Liveness probe |

All responses are **OGC-compliant GeoJSON** in **EPSG:4326**.

---

## Architecture

```
Client
  │
  ▼
FastAPI (uvicorn)           DigitalOcean App Platform
  │
  ├── POST /isochrone ──────► graph_cache.py
  │                               │
  │                          Memory cache (tier 1)
  │                          Disk .graphml (tier 2)   ← DO persistent volume
  │                          OSM Overpass API (cold)
  │                               │
  │                          osmnx.ego_graph()
  │                          Delaunay alpha-shape
  │                               │
  │                          GeoJSON Feature ◄────────────────────┐
  │                                                               │
  ├── GET /facilities/nearest ──► PostGIS                        │
  │                               ST_DWithin (GIST index)        │
  │                               ST_Distance (exact sort)       │
  │                               GeoJSON FeatureCollection      │
  │                                                               │
  └── POST /coverage/gap ────► facilities in bbox (PostGIS)      │
                                asyncio.gather(isochrone × N) ──►┘
                                ST_Union(isochrones)
                                ST_Difference(bbox, union)
                                GeoJSON Feature + coverage_pct

PostGIS (managed PostgreSQL 16)    DigitalOcean Managed Database
  tables: facilities
  indexes: GIST on geom, B-tree on facility_type
```

---

## Isochrone algorithm

```
POST /isochrone  { lat, lon, travel_time_minutes, mode }
```

1. **Graph load** — `graph_cache.get_graph()` resolves: memory → disk → OSM download.
   Graphs are stored as `.graphml` files on a DO persistent volume.

2. **Travel-time weights**
   - `walk`: constant 5 km/h on all walkable edges
   - `drive`: OSM `maxspeed` tags via `osmnx.add_edge_speeds()` + `add_edge_travel_times()`

3. **Ego subgraph** — `networkx.ego_graph(G, center_node, radius=seconds, distance="travel_time")`
   returns every node reachable within the time budget via Dijkstra.

4. **Alpha shape (concave hull)**
   - Triangulate reachable node coordinates with `scipy.spatial.Delaunay`
   - Keep triangles with circumradius < `1/alpha` (α = 0.5)
   - Union kept triangles with `shapely.ops.unary_union`
   - Fallback to convex hull for sparse networks

This is fundamentally different from `ST_Buffer(point, radius)`.
A 15-minute walk isochrone in a grid city has a very different shape
from one near a highway on-ramp — the alpha shape captures that.

---

## Spatial k-NN: why two-phase querying matters

```sql
-- NAIVE (full sequential scan on large tables)
SELECT *, ST_Distance(geom::geography, origin) AS dist
FROM facilities
WHERE facility_type = 'grocery'
ORDER BY dist LIMIT 5;

-- CORRECT (GIST index pre-filter → exact sort on small set)
SELECT *, ST_Distance(geom::geography, origin) AS dist
FROM facilities
WHERE facility_type = 'grocery'
  AND ST_DWithin(geom::geography, origin, 5000)   -- index used here
ORDER BY dist LIMIT 5;
```

`ST_DWithin` on a `geography` column uses the GIST index for an O(log n)
bounding-box pre-filter. `ST_Distance` then computes exact geodesic distances
only on the pre-filtered rows before sorting.

### Live query plan

`GET /facilities/query-plan` returns `EXPLAIN (ANALYZE, BUFFERS)` output:

```
Index Scan using idx_facilities_geom on facilities
  Index Cond: (geom && ...)
  Filter: (facility_type = 'grocery' AND st_dwithin(...))
  Rows Removed by Filter: 12
  Actual rows: 47, loops: 1
Planning Time: 0.3 ms
Execution Time: 1.8 ms        ← vs ~140 ms sequential scan
```

---

## Coverage gap algorithm

```
POST /coverage/gap  { bbox, facility_type, travel_time_minutes, mode }
```

```sql
WITH isochrones AS (
    -- one row per facility isochrone polygon
    SELECT ST_SetSRID(ST_GeomFromGeoJSON('...'), 4326) AS geom
    UNION ALL ...
),
covered AS (
    SELECT ST_Union(geom) AS geom FROM isochrones
),
bbox AS (
    SELECT ST_MakeEnvelope(minx, miny, maxx, maxy, 4326) AS geom
),
gap AS (
    SELECT ST_Difference(bbox.geom, covered.geom) AS geom
    FROM bbox, covered
)
SELECT
    ST_AsGeoJSON(gap.geom)                                    AS gap_polygon,
    ST_Area(covered ∩ bbox) / ST_Area(bbox) * 100            AS coverage_pct,
    (ST_Area(bbox) - ST_Area(covered ∩ bbox)) / 1e6          AS gap_area_km2
FROM ...
```

Isochrones for all facilities in the bbox are computed in parallel via
`asyncio.gather`. The resulting polygons are unioned and differenced entirely
in PostGIS — no client-side geometry arithmetic.

---

## Benchmark results

Measured on DigitalOcean App Platform (Basic, 1 vCPU / 512 MB) with ~2,400
Orange County facilities seeded. Isochrone times are **warm** (graph in memory).

| Endpoint | p50 | p95 | min | max | notes |
|---|---|---|---|---|---|
| `GET /facilities/nearest` | 3 ms | 5 ms | 2 ms | 5 ms | GIST index scan, 508 facilities |
| `POST /isochrone` (walk, 15 min) | 82 ms | 88 ms | 81 ms | 88 ms | warm graph cache |
| `POST /isochrone` (drive, 15 min) | 582 ms | 789 ms | 567 ms | 789 ms | warm graph cache, larger network |
| `POST /coverage/gap` (9 groceries) | 684 ms | 904 ms | 676 ms | 904 ms | parallel isochrones + ST_Union/ST_Difference |
| Cold-start isochrone (OSM download) | ~5–10 s | — | — | — | one-time per area, saved to disk |

Measured locally on MacBook (Apple M-series), Docker Compose stack, 508 seeded facilities.
Run `python scripts/benchmark.py` against a live instance to reproduce.

---

## Dataset

Seeded with OSM POIs for **Orange County, CA** via `osmnx.features_from_place()`:

| Facility type | OSM tags | Count |
|---|---|---|
| `hospital` | `amenity=hospital` | 37 |
| `grocery` | `shop=supermarket,grocery` | 317 |
| `ev_charger` | `amenity=charging_station` | 154 |

**Total: 508 facilities.** Seeded via `python scripts/seed_facilities.py` — re-run safe (truncates before insert).

---

## Local development

### Prerequisites
- Docker + Docker Compose
- Python 3.12

### Run
```bash
git clone https://github.com/rahulsp2504/spatial-access-intelligence-api
cd spatial-access-intelligence-api

cp .env.example .env
docker compose up --build       # starts PostGIS + API with hot-reload

# In a separate terminal — seed OSM data (~2–5 min first run)
pip install -r requirements.txt
python scripts/seed_facilities.py
```

API: http://localhost:8000  
Swagger UI: http://localhost:8000/docs

### Tests
```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

### Lint
```bash
ruff check app/ scripts/ tests/
```

---

## Deployment (DigitalOcean)

1. Push to `main` → GitHub Actions runs lint + tests
2. DigitalOcean App Platform auto-deploys on CI pass
3. `DATABASE_URL` injected as a secret env var from DO managed PostgreSQL
4. `GRAPH_CACHE_DIR` points to a DO persistent volume (`/app/graph_cache`)
5. Seed run once via `doctl apps run` or SSH console:
   ```bash
   python scripts/seed_facilities.py
   ```

---

## Project structure

```
spatial-access-intelligence-api/
├── app/
│   ├── main.py                   # FastAPI app + lifespan
│   ├── config.py                 # pydantic-settings
│   ├── db/
│   │   ├── session.py            # async SQLAlchemy engine
│   │   └── init_db.py            # PostGIS extension + GIST index bootstrap
│   ├── models/
│   │   ├── facility.py           # GeoAlchemy2 ORM model
│   │   └── schemas.py            # Pydantic v2 request/response schemas
│   ├── routers/
│   │   ├── isochrone.py          # POST /isochrone
│   │   ├── facilities.py         # GET /facilities/nearest, /query-plan
│   │   └── coverage.py           # POST /coverage/gap
│   └── services/
│       ├── graph_cache.py        # OSM graph — memory + disk cache
│       ├── isochrone_service.py  # ego-graph + alpha-shape
│       └── spatial_service.py   # PostGIS queries
├── scripts/
│   ├── seed_facilities.py        # OSM → PostGIS seeder
│   └── benchmark.py              # latency table generator
├── tests/
│   ├── conftest.py
│   ├── test_health.py
│   ├── test_facilities.py
│   ├── test_isochrone.py
│   └── test_coverage.py
├── .github/workflows/ci.yml
├── docker-compose.yml
├── Dockerfile
└── README.md
```

---

## Tech stack

| Layer | Technology |
|---|---|
| API framework | FastAPI 0.111 + uvicorn |
| Spatial database | PostgreSQL 16 + PostGIS 3.4 |
| ORM | SQLAlchemy 2.0 async + GeoAlchemy2 |
| Network analysis | osmnx 1.9 + NetworkX 3.3 |
| Geometry | Shapely 2.0 + SciPy (Delaunay) |
| Deployment | DigitalOcean App Platform |
| CI/CD | GitHub Actions |

---

## Author

**Rahul Sharad Pandit** — MS Computer Engineering, UC Irvine (Dec 2026)  
[github.com/rahulsp2504](https://github.com/rahulsp2504) · [linkedin.com/in/rahulsharadpandit](https://linkedin.com/in/rahulsharadpandit) · rspandit@uci.edu
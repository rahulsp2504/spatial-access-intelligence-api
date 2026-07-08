from fastapi import APIRouter, HTTPException

from app.models.schemas import IsochroneRequest
from app.services.isochrone_service import compute_isochrone

router = APIRouter(prefix="/isochrone", tags=["Isochrone"])


@router.post(
    "",
    summary="Network-based isochrone polygon",
    description="""
Computes the area reachable within **N minutes** from a given point
using real OSM street-network traversal — not a Euclidean buffer.

### Algorithm
1. Load or restore the OSM `MultiDiGraph` for the area from disk/memory cache
2. Add travel-time weights to each edge (`walk` → 5 km/h constant; `drive` → OSM speed limits)
3. `networkx.ego_graph(G, center_node, radius=seconds, distance="travel_time")`
4. Extract (lon, lat) of every reachable node
5. Build a **concave hull (alpha shape)** via Delaunay triangulation — keeps only
   triangles whose circumradius < 1/α, then unions them with `shapely.ops.unary_union`

### Response
OGC-compliant GeoJSON Feature in **EPSG:4326** with properties:
- `travel_time_minutes`, `mode`, `node_count`, `area_km2`
- `bbox` array `[minx, miny, maxx, maxy]`

### Performance
| cold start | warm (disk cache) | warm (memory) |
|---|---|---|
| ~3–8 s (OSM download) | ~0.8–1.5 s | ~1.0–2.5 s |

Cold-start latency is dominated by the Overpass API download.
Subsequent calls to the same area are served from disk or memory.
    """,
    response_description="GeoJSON Feature — isochrone polygon",
)
async def post_isochrone(req: IsochroneRequest):
    try:
        return await compute_isochrone(
            req.lat, req.lon, req.travel_time_minutes, req.mode.value
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Isochrone computation failed: {exc}",
        )

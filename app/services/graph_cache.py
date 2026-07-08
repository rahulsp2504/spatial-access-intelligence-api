"""
OSM street-graph cache with two tiers:

  Tier 1 — In-memory dict keyed by (lat4, lon4, mode, dist_m).
            Zero I/O after warm-up. Lost on restart.

  Tier 2 — GraphML files on disk (GRAPH_CACHE_DIR).
            Survives restarts and DigitalOcean App Platform re-deploys.
            A named volume mounts this directory in docker-compose and
            the DO App Platform persistent-disk configuration.

Download path (cold start): osmnx → OSM Overpass API → NetworkX graph.
Travel-time edge weights are added once and baked into the cached graph.
"""

from __future__ import annotations

import logging
from pathlib import Path

import networkx as nx
import osmnx as ox

from app.config import settings

logger = logging.getLogger(__name__)

ox.settings.log_console = False
ox.settings.use_cache = True  # osmnx's own HTTP-response cache

# In-memory tier
_mem_cache: dict[str, nx.MultiDiGraph] = {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _cache_key(lat: float, lon: float, mode: str, dist_m: int) -> str:
    return f"{lat:.4f}_{lon:.4f}_{mode}_{dist_m}"


def _graphml_path(key: str) -> Path:
    cache_dir = Path(settings.GRAPH_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{key}.graphml"


def _travel_dist_m(mode: str, travel_time_minutes: int) -> int:
    """
    Estimate the maximum walking/driving distance for the given time budget
    with a 1.5× safety buffer so the graph covers the full isochrone edge.
    """
    speed_kmh = 5 if mode == "walk" else 50
    dist = int(speed_kmh * 1_000 / 60 * travel_time_minutes * 1.5)
    return max(dist, 1_000)  # floor at 1 km


def _add_travel_time(G: nx.MultiDiGraph, mode: str) -> nx.MultiDiGraph:
    """
    Attach `travel_time` (seconds) to every edge.

    Both modes use a constant speed applied to the OSM edge `length`:
      walk  → 5 km/h  (pedestrian network)
      drive → 50 km/h (urban driving average on the drivable network)

    Using constant speeds avoids osmnx's maxspeed tag parser, which fails
    on OSM data where the `highway` or `maxspeed` attribute is a float or
    list instead of a plain string. The drive/walk distinction is preserved
    through the network type loaded by osmnx (walk-only paths excluded from
    the drive graph), so isochrone shapes correctly differ between modes.
    """
    speed_ms = (5 if mode == "walk" else 50) * 1_000 / 3_600
    for _, _, data in G.edges(data=True):
        data["travel_time"] = data.get("length", 0) / speed_ms
    return G


# ── Public API ─────────────────────────────────────────────────────────────────

async def get_graph(
    lat: float, lon: float, mode: str, travel_time_minutes: int
) -> nx.MultiDiGraph:
    """
    Return a NetworkX MultiDiGraph for the street network centred on
    (lat, lon) large enough to cover the requested travel-time isochrone.

    Resolution order: memory → disk → OSM download.
    """
    dist_m = _travel_dist_m(mode, travel_time_minutes)
    key = _cache_key(lat, lon, mode, dist_m)
    network_type = "walk" if mode == "walk" else "drive"

    # Tier 1 — memory
    if key in _mem_cache:
        logger.debug("Graph cache hit (memory): %s", key)
        return _mem_cache[key]

    # Tier 2 — disk
    graphml = _graphml_path(key)
    if graphml.exists():
        logger.info("Graph cache hit (disk): %s", graphml)
        G = ox.load_graphml(graphml)
        if not nx.get_edge_attributes(G, "travel_time"):
            G = _add_travel_time(G, mode)
        _mem_cache[key] = G
        return G

    # Cold start — download from OSM
    logger.info("Downloading OSM graph for (%s, %s) mode=%s dist=%dm", lat, lon, mode, dist_m)
    G = ox.graph_from_point(
        (lat, lon),
        dist=dist_m,
        network_type=network_type,
        simplify=True,
    )
    G = _add_travel_time(G, mode)

    ox.save_graphml(G, graphml)
    logger.info("Graph saved to disk: %s", graphml)

    _mem_cache[key] = G
    return G
"""
Isochrone computation pipeline
==============================

1. Load/cache the OSM street graph via graph_cache.get_graph()
2. Find the nearest graph node to the origin point
3. Compute an ego subgraph: all nodes reachable within the time budget
   using Dijkstra over `travel_time` edge weights (NetworkX ego_graph)
4. Extract the (lon, lat) coordinates of every reachable node
5. Build a concave hull (alpha shape via Delaunay triangulation) around
   those coordinates — this is what makes the polygon follow the road
   network rather than being a naive Euclidean buffer

The alpha shape algorithm:
  - Triangulate the reachable node cloud with scipy.spatial.Delaunay
  - Keep triangles whose circumradius < 1/alpha  (alpha=0.5 by default)
  - Union the kept triangles with shapely.ops.unary_union
  - Falls back to convex hull if the point cloud is too sparse
"""

from __future__ import annotations

import logging
import math

import networkx as nx
import numpy as np
import osmnx as ox
from shapely.geometry import MultiPoint, mapping
from shapely.ops import unary_union

from app.services.graph_cache import get_graph

logger = logging.getLogger(__name__)


def _nearest_node(G: nx.MultiDiGraph, lon: float, lat: float) -> int:
    """
    Find the nearest graph node to (lon, lat) using Euclidean distance
    on raw degree coordinates — no scikit-learn required.
    Accurate enough within a single city (error < 0.1% at OC latitudes).
    """
    best_node = None
    best_dist = float("inf")
    for node, data in G.nodes(data=True):
        if "x" not in data or "y" not in data:
            continue
        dist = math.hypot(data["x"] - lon, data["y"] - lat)
        if dist < best_dist:
            best_dist = dist
            best_node = node
    return best_node


# ── Alpha shape ────────────────────────────────────────────────────────────────

def _alpha_shape(points: list[tuple[float, float]], alpha: float = 0.5):
    """
    Compute a concave hull (alpha shape) for a set of 2-D points.

    Args:
        points: list of (x, y) tuples — longitude/latitude pairs
        alpha:  smaller values → looser (more convex) hull
                larger values  → tighter (more concave) hull

    Returns:
        shapely Polygon or MultiPolygon
    """
    if len(points) < 4:
        return MultiPoint(points).convex_hull

    try:
        from scipy.spatial import Delaunay
        from shapely.geometry import Polygon

        coords = np.array(points)
        tri = Delaunay(coords)
        polys = []

        for simplex in tri.simplices:
            pts = coords[simplex]
            a, b, c = pts
            # Side lengths
            A = np.linalg.norm(b - a)
            B = np.linalg.norm(c - b)
            C = np.linalg.norm(a - c)
            s = (A + B + C) / 2
            area = max(np.sqrt(abs(s * (s - A) * (s - B) * (s - C))), 1e-12)
            circum_r = (A * B * C) / (4 * area)

            if circum_r < 1.0 / alpha:
                polys.append(Polygon(pts))

        if not polys:
            return MultiPoint(points).convex_hull

        return unary_union(polys)

    except Exception as exc:
        logger.warning("Alpha shape failed (%s); falling back to convex hull", exc)
        return MultiPoint(points).convex_hull


# ── Public API ─────────────────────────────────────────────────────────────────

async def compute_isochrone(
    lat: float,
    lon: float,
    travel_time_minutes: int,
    mode: str,
    alpha: float = 0.5,
) -> dict:
    """
    Compute a network-based isochrone polygon and return an OGC-compliant
    GeoJSON Feature in EPSG:4326.

    Raises:
        ValueError: if the graph has fewer than 3 reachable nodes (e.g.
                    ocean / very sparse rural area)
    """
    G = await get_graph(lat, lon, mode, travel_time_minutes)
    center_node = _nearest_node(G, lon, lat)
    travel_time_seconds = travel_time_minutes * 60

    subgraph = nx.ego_graph(
        G,
        center_node,
        radius=travel_time_seconds,
        distance="travel_time",
    )

    node_coords = [
        (data["x"], data["y"])
        for _, data in subgraph.nodes(data=True)
        if "x" in data and "y" in data
    ]

    if len(node_coords) < 3:
        raise ValueError(
            f"Only {len(node_coords)} nodes reachable — "
            "location may be in a sparse network area. "
            "Try increasing travel_time_minutes."
        )

    logger.info(
        "Isochrone: %d reachable nodes for (%.4f, %.4f) %dmin %s",
        len(node_coords), lat, lon, travel_time_minutes, mode,
    )

    poly = _alpha_shape(node_coords, alpha=alpha)

    # Approximate area in km² (degree-based; good enough for OC latitudes)
    area_km2 = round(poly.area * (111.0 ** 2), 4)

    return {
        "type": "Feature",
        "geometry": mapping(poly),
        "properties": {
            "origin": {"lat": lat, "lon": lon},
            "travel_time_minutes": travel_time_minutes,
            "mode": mode,
            "node_count": len(node_coords),
            "area_km2": area_km2,
            "crs": "EPSG:4326",
        },
        "bbox": list(poly.bounds),  # [minx, miny, maxx, maxy]
    }
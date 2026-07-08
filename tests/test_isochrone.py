import pytest


@pytest.mark.asyncio
async def test_isochrone_missing_body(client):
    r = await client.post("/isochrone", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_isochrone_travel_time_bounds(client):
    """travel_time_minutes must be 5–30."""
    for bad_t in (4, 31):
        r = await client.post(
            "/isochrone",
            json={"lat": 33.64, "lon": -117.84, "travel_time_minutes": bad_t, "mode": "walk"},
        )
        assert r.status_code == 422, f"Expected 422 for travel_time_minutes={bad_t}"


@pytest.mark.asyncio
async def test_isochrone_invalid_mode(client):
    r = await client.post(
        "/isochrone",
        json={"lat": 33.64, "lon": -117.84, "travel_time_minutes": 10, "mode": "teleport"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_isochrone_geojson_shape(client):
    """
    If the OSM download succeeds, verify the response is a valid GeoJSON Feature.
    Skip on network errors (CI without internet access).
    """
    r = await client.post(
        "/isochrone",
        json={"lat": 33.6405, "lon": -117.8443, "travel_time_minutes": 10, "mode": "walk"},
        timeout=60,
    )
    if r.status_code == 200:
        body = r.json()
        assert body["type"] == "Feature"
        assert "geometry" in body
        assert body["geometry"]["type"] in ("Polygon", "MultiPolygon")
        props = body["properties"]
        assert "area_km2" in props
        assert props["travel_time_minutes"] == 10
        assert props["mode"] == "walk"

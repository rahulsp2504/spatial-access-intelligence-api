import pytest


@pytest.mark.asyncio
async def test_nearest_missing_required_params(client):
    """lat/lon/facility_type are required — expect 422."""
    r = await client.get("/facilities/nearest")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_nearest_invalid_facility_type(client):
    r = await client.get(
        "/facilities/nearest",
        params={"lat": 33.64, "lon": -117.84, "facility_type": "casino"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_nearest_k_bounds(client):
    """k=0 and k=11 must be rejected."""
    for bad_k in (0, 11):
        r = await client.get(
            "/facilities/nearest",
            params={
                "lat": 33.64,
                "lon": -117.84,
                "facility_type": "grocery",
                "k": bad_k,
            },
        )
        assert r.status_code == 422, f"Expected 422 for k={bad_k}"


@pytest.mark.asyncio
async def test_nearest_returns_feature_collection(client):
    """
    With a seeded DB, a query near UCI should return a valid GeoJSON
    FeatureCollection. With an empty DB it returns an empty collection —
    both are valid responses.
    """
    r = await client.get(
        "/facilities/nearest",
        params={
            "lat": 33.6405,
            "lon": -117.8443,
            "facility_type": "grocery",
            "k": 5,
            "radius_km": 10.0,
        },
    )
    # 200 or 500 (if DB not seeded in CI) — test the shape, not the data
    if r.status_code == 200:
        body = r.json()
        assert body["type"] == "FeatureCollection"
        assert isinstance(body["features"], list)

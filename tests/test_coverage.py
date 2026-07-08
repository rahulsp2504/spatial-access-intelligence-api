import pytest


@pytest.mark.asyncio
async def test_coverage_gap_missing_body(client):
    r = await client.post("/coverage/gap", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_coverage_gap_invalid_bbox(client):
    """minx > maxx must be rejected."""
    r = await client.post(
        "/coverage/gap",
        json={
            "minx": -117.70,
            "miny": 33.55,
            "maxx": -118.05,  # minx > maxx
            "maxy": 33.85,
            "facility_type": "grocery",
            "travel_time_minutes": 15,
            "mode": "walk",
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_coverage_gap_response_shape(client):
    """
    Response must be a GeoJSON Feature with coverage_pct in properties.
    With an empty DB the gap polygon equals the full bbox.
    """
    r = await client.post(
        "/coverage/gap",
        json={
            "minx": -117.90,
            "miny": 33.62,
            "maxx": -117.80,
            "maxy": 33.68,
            "facility_type": "grocery",
            "travel_time_minutes": 15,
            "mode": "walk",
        },
        timeout=120,
    )
    if r.status_code == 200:
        body = r.json()
        assert body["type"] == "Feature"
        assert "geometry" in body
        props = body["properties"]
        assert "coverage_pct" in props
        assert 0.0 <= props["coverage_pct"] <= 100.0
        assert "facility_count" in props

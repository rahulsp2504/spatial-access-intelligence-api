import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Point at the test DB before importing app modules
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/spatial_api_test",
)

from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c

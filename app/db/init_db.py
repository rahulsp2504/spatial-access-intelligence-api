from sqlalchemy import text
from app.db.session import engine, Base
from app.models.facility import Facility  # noqa: F401 — registers model with Base


async def init_db() -> None:
    """
    Bootstrap the database on startup:
      1. Enable PostGIS extension
      2. Create tables via SQLAlchemy metadata
      3. Create GIST spatial index (idempotent)
    """
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_facilities_geom
                ON facilities USING GIST (geom)
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_facilities_type
                ON facilities (facility_type)
                """
            )
        )

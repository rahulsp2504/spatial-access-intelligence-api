from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings


def _async_url(url: str) -> str:
    """Convert any postgres:// variant to the asyncpg dialect."""
    return (
        url.replace("postgresql://", "postgresql+asyncpg://")
        .replace("postgres://", "postgresql+asyncpg://")
    )


engine = create_async_engine(
    _async_url(settings.DATABASE_URL),
    echo=False,
    pool_size=5,
    max_overflow=10,
)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

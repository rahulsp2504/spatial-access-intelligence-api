from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/spatial_api"
    GRAPH_CACHE_DIR: str = "./graph_cache"

    class Config:
        env_file = ".env"


settings = Settings()

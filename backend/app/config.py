from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AdminFlow"
    database_url: str = "postgresql+psycopg://adminflow:adminflow@localhost:5432/adminflow"
    artifact_storage_path: Path = Path("data/artifacts")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

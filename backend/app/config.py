from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AdminFlow"
    database_url: str = "postgresql+psycopg://adminflow:adminflow@localhost:5432/adminflow"
    artifact_storage_path: Path = Path("data/artifacts")
    ocr_language: str = "eng"
    ocr_dpi: int = Field(default=300, gt=0)
    ocr_timeout_seconds: int = Field(default=30, gt=0)
    ai_provider: Literal["stub", "openai"] = "stub"
    openai_api_key: SecretStr | None = None
    ai_classification_model: str = "gpt-5-mini"
    ai_structured_extraction_model: str = "gpt-5-mini"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

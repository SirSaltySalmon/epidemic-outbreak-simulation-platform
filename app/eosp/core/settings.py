from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    env: str = "development"
    database_url: str | None = None
    redis_url: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_prefix="EOSP_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    env: str = "development"
    database_url: str | None = None
    redis_url: str | None = None
    who_don_poll_enabled: bool = True
    who_don_url: str = "https://www.who.int/emergencies/emergency-events/item/2026-e000227"
    who_don_poll_interval_seconds: int = 21_600
    who_don_feed_key: str = "who_don_2026_e000227"
    # Maps API default for GET /geo/outbreak when ``risk_model`` query is omitted.
    geo_risk_model: str = "legacy"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="EOSP_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

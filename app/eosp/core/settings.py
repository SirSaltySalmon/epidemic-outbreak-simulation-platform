from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

WhoDonItemPolicy = Literal["latest_only", "all_discovered"]
WhoDonExtractMode = Literal["regex", "llm", "hybrid"]


class Settings(BaseSettings):
    env: str = "development"
    database_url: str | None = None
    redis_url: str | None = None
    who_don_poll_enabled: bool = False  # deprecated for ops; manual case entry preferred
    who_don_url: str = "https://www.who.int/emergencies/emergency-events/item/2026-e000227"
    who_don_poll_interval_seconds: int = 21_600
    who_don_feed_key: str = "who_don_2026_e000227"
    who_don_item_policy: WhoDonItemPolicy = "latest_only"
    who_don_extract_mode: WhoDonExtractMode = "hybrid"
    who_don_extract_max_cohort: int = 500
    who_don_llm_api_key: str | None = None
    who_don_llm_base_url: str = "https://api.openai.com/v1"
    who_don_llm_model: str = "gpt-4o-mini"
    who_don_llm_timeout_seconds: int = 60
    # Maps API default for GET /geo/outbreak when ``risk_model`` query is omitted.
    geo_risk_model: str = "legacy"
    # Deprecated product surface: stochastic metapop kernel (keep for regression / research only).
    metapop_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_prefix="EOSP_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

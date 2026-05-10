from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

WhoDonItemPolicy = Literal["latest_only", "all_discovered"]
WhoDonExtractMode = Literal["regex", "llm", "hybrid"]


class Settings(BaseSettings):
    env: str = "development"
    database_url: str | None = None
    redis_url: str | None = None  # optional TCP URL; Upstash uses UPSTASH_REDIS_REST_* + redis_client.get_redis()
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
    # Maps API default when ``risk_model`` query is omitted (ignored at runtime; geo always uses abm_geo_forecast).
    geo_risk_model: str = "abm_geo"
    # Clerk (https://clerk.com): set both to require signed-in sessions for researcher APIs + Console UI.
    clerk_publishable_key: str | None = None
    clerk_frontend_api: str | None = None
    # Secret key: lets the API read ``public_metadata`` via Clerk Backend API when the session JWT
    # does not embed metadata (Clerk default session tokens omit ``public_metadata``).
    clerk_secret_key: str | None = None
    # Required on direct HTTP calls to api.clerk.com (see Clerk versioning docs).
    clerk_backend_api_version: str = "2025-04-10"

    #: Optional UUID string: excluded from hub timeline anchor + line-list forcing (narrative index case).
    hub_index_case_id: str | None = None

    #: When set, hub cohort agent count in :func:`build_world_contact_network` uses this
    #: instead of summing baseline flight ``n_passengers`` (overrides spawn ship block).
    cohort_population_override: int | None = None
    #: Line-list seeding: persons with onset at least this many days before the forecast
    #: anchor date are placed in **R**; more recent onsets in **I**. No hidden multipliers.
    seed_still_infectious_within_days: int = 10

    model_config = SettingsConfigDict(env_file=".env", env_prefix="EOSP_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

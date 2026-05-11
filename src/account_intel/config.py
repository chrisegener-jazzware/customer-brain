"""Centralized settings via pydantic-settings."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://account_intel:account_intel@localhost:5432/account_intel"

    hubspot_token: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_model_large: str = "claude-opus-4-7"

    feeder_fresh_ttl_seconds: int = 3600
    feeder_activity_window_days: int = 90
    rollup_cache_ttl_seconds: int = 21600

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    ui_internal_port: int = 8502
    ui_client_port: int = 8503
    api_base_url: str = "http://localhost:8000"

    demo_company_ids: str = Field(default="320895019724,320995239625")

    @property
    def demo_company_id_list(self) -> list[str]:
        return [c.strip() for c in self.demo_company_ids.split(",") if c.strip()]


settings = Settings()

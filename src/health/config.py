"""Configuration: credentials, lookback windows, AM email mapping."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Anthropic / Claude ---
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(default="claude-sonnet-4-5", alias="CLAUDE_MODEL")
    claude_summary_max_tokens: int = 1024

    # --- HubSpot (primary CRM post-migration) ---
    hubspot_token: str = Field(default="", alias="HUBSPOT_PRIVATE_APP_TOKEN")

    # --- Salesforce (legacy, read-only during migration) ---
    sf_username: str = Field(default="", alias="SF_USERNAME")
    sf_password: str = Field(default="", alias="SF_PASSWORD")
    sf_token: str = Field(default="", alias="SF_SECURITY_TOKEN")
    sf_domain: str = Field(default="login", alias="SF_DOMAIN")
    sf_legacy_enabled: bool = Field(default=True, alias="SF_LEGACY_ENABLED")

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://health:health@localhost:5432/customer_health",
        alias="DATABASE_URL",
    )

    # --- Email / SMTP ---
    smtp_host: str = Field(default="smtp.gmail.com", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="health-digest@jazzware.com", alias="SMTP_FROM")

    # --- Integration health source (log-watch-agent output dir or API) ---
    integration_health_path: Path = Field(
        default=Path("/var/lib/log-watch-agent/health.json"),
        alias="INTEGRATION_HEALTH_PATH",
    )

    # --- Lookback windows (days) ---
    ticket_lookback_days: int = 30
    integration_lookback_days: int = 7
    usage_lookback_days: int = 14
    history_retention_days: int = 365

    # --- Scoring weights (sum ~= 1.0) ---
    weight_ticket_volume: float = 0.20
    weight_escalation_rate: float = 0.25
    weight_integration_errors: float = 0.20
    weight_renewal_proximity: float = 0.15
    weight_contact_recency: float = 0.10
    weight_usage_decline: float = 0.10

    # --- Risk band thresholds (score 0-100, lower = worse health) ---
    risk_critical_threshold: float = 40.0
    risk_warn_threshold: float = 65.0
    needs_attention_threshold: float = 55.0

    # --- AM email mapping (customer_id -> AM email) ---
    # In production, sourced from HubSpot owner_id; this is a manual override map
    am_email_map: dict[str, str] = Field(default_factory=dict)

    # AM display name lookup (email -> "Firstname Lastname")
    am_display_names: dict[str, str] = Field(default_factory=dict)

    # --- Web dashboard ---
    dashboard_base_url: str = Field(
        default="http://localhost:8000", alias="DASHBOARD_BASE_URL"
    )

    # --- Feature flags ---
    dry_run_email: bool = Field(default=False, alias="DRY_RUN_EMAIL")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings(**overrides: Any) -> Settings:
    """Test helper: rebuild settings with overrides applied."""
    global _settings
    _settings = Settings(**overrides)
    return _settings

"""Runtime configuration loaded from environment variables / .env file.

Lightweight (no pydantic) so the package boots cheaply for CLI use.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass


def _env_str(key: str, default: str = "") -> str:
    val = os.environ.get(key)
    return val if val is not None and val != "" else default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    hubspot_token: str
    openai_api_key: str
    vip_list_path: Path | None
    db_path: Path
    repeat_sim_threshold: float
    repeat_window_days: int
    poll_interval_seconds: int

    @property
    def hubspot_mock_mode(self) -> bool:
        return not self.hubspot_token

    @property
    def use_openai_embeddings(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    vip_raw = _env_str("VIP_LIST_PATH", "")
    vip_path = Path(vip_raw) if vip_raw else None
    db_path = Path(_env_str("SUPPORT_ADMIN_DB", "data/support_admin.db"))
    return Settings(
        hubspot_token=_env_str("HUBSPOT_PRIVATE_APP_TOKEN", ""),
        openai_api_key=_env_str("OPENAI_API_KEY", ""),
        vip_list_path=vip_path,
        db_path=db_path,
        repeat_sim_threshold=_env_float("REPEAT_SIM_THRESHOLD", 0.85),
        repeat_window_days=_env_int("REPEAT_WINDOW_DAYS", 30),
        poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 300),
    )


def reset_settings_cache() -> None:
    """Test helper — clears the lru_cache so env changes take effect."""
    get_settings.cache_clear()

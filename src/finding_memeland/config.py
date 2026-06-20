"""Central configuration, driven by environment variables (Doppler-backed).

Nothing here reads secrets from files on disk in production — Doppler injects
env vars at runtime. `.env` is only used for local development.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # Runtime
    fmml_env: str = Field(default="local")
    log_level: str = Field(default="INFO")

    # Anthropic
    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-sonnet-4-6")

    # Supabase
    supabase_url: str = Field(default="")
    supabase_service_role_key: str = Field(default="")

    # X API (single dev app)
    x_api_key: str = Field(default="")
    x_api_secret: str = Field(default="")
    x_bearer_token: str = Field(default="")
    x_main_access_token: str = Field(default="")
    x_main_access_secret: str = Field(default="")

    # Base chain
    base_rpc_url: str = Field(default="https://mainnet.base.org")
    fmml_token_address: str = Field(default="")
    hot_wallet_private_key: str = Field(default="")
    payout_cap_fmml: int = Field(default=0)

    # Telegram
    telegram_bot_token: str = Field(default="")
    telegram_admin_chat_id: str = Field(default="")

    # Game parameters
    prize_usd_min: int = Field(default=200)
    prize_usd_max: int = Field(default=500)
    integrity_salt: str = Field(default="")

    @property
    def is_production(self) -> bool:
        return self.fmml_env == "production"

    def assert_ready_for_hunt(self) -> None:
        """Fail fast before a hunt if critical config is missing."""
        missing = [
            name
            for name, value in {
                "fmml_token_address": self.fmml_token_address,
                "hot_wallet_private_key": self.hot_wallet_private_key,
                "integrity_salt": self.integrity_salt,
                "payout_cap_fmml": self.payout_cap_fmml,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"Cannot start hunt — missing config: {', '.join(missing)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()

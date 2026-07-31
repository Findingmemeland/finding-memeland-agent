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

    # OpenAI (avatar image generation)
    openai_api_key: str = Field(default="")
    openai_image_model: str = Field(default="gpt-image-1")
    openai_image_size: str = Field(default="1024x1024")

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
    fmml_usd_price: float = Field(default=0.0)      # set after token launch (price source)
    total_supply: float = Field(default=100_000_000_000.0)  # 100B — for FDV/suggestion
    holding_floor_usd: float = Field(default=20.0)  # min holding in USD (fallback)
    # PREFERRED floor: a FIXED token amount, announced publicly >= 24h before the
    # hunt fires. The holding window looks 24h BACK, so players must know the
    # exact number before they buy — a trigger-time USD conversion would move
    # the goalposts on people who already hold. Set per season/batch of hunts.
    holding_floor_fmml: int = Field(default=0)      # 0 = fall back to USD conversion
    holding_hours: int = Field(default=24)          # continuous-hold eligibility window
    # Clue cadence. The gap is drawn uniformly from [min, max] per clue. Defaults
    # match the published Pirate Code (1-3h). MUST stay consistent with
    # holding_hours: the eligibility window looks BACK from the claim, so if a
    # hunt can outlast holding_hours, a mid-hunt buyer could still qualify —
    # which would contradict "hold before clue 1". Rule of thumb:
    # holding_hours > (expected clues x max gap).
    clue_min_gap_s: int = Field(default=60 * 60)       # 1h
    clue_max_gap_s: int = Field(default=3 * 60 * 60)   # 3h
    persona_register: str = Field(default="medium")
    # Daily oracle post: generates draft options once a day and sends them to
    # Telegram for approval (nothing publishes without it). Hour is UTC.
    filler_daily_enabled: bool = Field(default=True)
    filler_hour_utc: int = Field(default=15)  # start of the crypto-X peak window
    min_warmup_days: int = Field(default=7)          # persona must be phone-verified + this old
    min_prize_usd: float = Field(default=200.0)      # legacy USD floor (unused by /launch since token prizes)
    # /launch takes a TOKEN amount ("500M", "1B") since 2026-07-31 — no more
    # FMML_USD_PRICE dance in Doppler just to launch a hunt.
    min_prize_fmml: int = Field(default=100_000_000)  # floor — 100M $FIND
    # Holder reward split: a winner whose wallet fails the holding rule still
    # WINS, but gets this % of the pot (holders get 100%). Dormant while the
    # holding floor is zero (everyone passes).
    non_holder_prize_pct: int = Field(default=10, ge=1, le=100)
    # Watchdog: alert on Telegram if a LIVE hunt's loop completes no cycle for
    # this long (hung HTTP call, dead thread). Must exceed the loop's longest
    # legitimate cycle: poll_interval (75s) + max failure backoff (300s).
    watchdog_stall_s: int = Field(default=600)
    # P2 findability architecture: /launch dresses the persona and opens a prep
    # window (persona posts its own anchor posts; X indexes the profile); Clue 1
    # only fires at the end. 0 disables the window (legacy direct go-live).
    prep_window_h: float = Field(default=24.0)
    prep_posts_n: int = Field(default=3)     # 2-4 anchor posts in the window
    # Claim-by-post channel (2026-07-25): the DM API only reads virgin
    # conversations, so submissions moved to public replies on the Clue 1 post.
    # 'post' = claim-by-post (production); 'dm' = legacy DM channel (fallback,
    # kept until the post channel survives a production hunt).
    claim_channel: str = Field(default="post")
    claim_guess_cap: int = Field(default=5)       # code-like posts per account/hunt
    wallet_timeout_s: int = Field(default=600)    # 10 min from OUR public ask
    claim_sweep_every_n: int = Field(default=5)   # thread-search backstop cadence

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

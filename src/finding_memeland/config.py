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
    # Blind solver for relic clues (Hunt #7 post-mortem): an INDEPENDENT model
    # tries to solve every puzzle piece before it is posted. "openai" uses the
    # client above (falls back to Anthropic when no key is set); "anthropic"
    # forces the same family as the writer; "off" disables it.
    relic_solver_backend: str = Field(default="openai")
    relic_solver_model: str = Field(default="gpt-4.1-mini")

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

    # ------------------------------------------------------------------
    # Relic hunts. relic_launch=False keeps today's behaviour exactly
    # (X personas). Turning it on is an env var, not a deploy.
    # ------------------------------------------------------------------
    relic_launch: bool = Field(default=False)
    # Fernet key (urlsafe base64, 32 bytes) for the blind pool. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # ⚠️ LOSING THIS KEY LOSES EVERY IDENTITY IN THE POOL. Identities are
    # stored encrypted and there is NO plaintext column by design — that is
    # what makes blind mode real, and the price is that there is no recovery
    # path. Back it up BEFORE creating the first relic.
    relic_pool_key: str = Field(default="")
    # Mint wallet refs (e.g. "RW01,RW02"). Only needed to MINT.
    # The KEYS live in Doppler as {REF}_ADDR / {REF}_PK — never here.
    relic_wallet_refs: str = Field(default="")
    # Pinata JWT — pins relic artwork to IPFS. Only needed to MINT.
    pinata_jwt: str = Field(default="")
    # Findability gate (launch only). Without a key the gate refuses, which is
    # the correct behaviour: never launch a hunt without confirming the relic
    # can actually be found.
    opensea_api_key: str = Field(default="")
    rarible_api_key: str = Field(default="")
    # Trail clues. With no verifier wired they fall back to direct clues,
    # silently — an unverified anchor is worse than a boring clue.
    relic_trails_enabled: bool = Field(default=False)
    # Which contract a relic is minted as (probe 2026-08-26, Probe_Manifold_Proxy.md):
    #   "manifold" — a Manifold ERC721Creator proxy (contracts/RelicManifoldProxy.json):
    #                runtime byte-for-byte identical to thousands of Manifold
    #                collections on Base, metadata JSON pinned to IPFS, ownership
    #                renounced after the mint. The default.
    #   "relicnft" — the bespoke RelicNFT.sol (contracts/RelicNFT.json): one
    #                bytecode class per pool, enumerable (audit P0-1). Kept for
    #                the dry-run harness and as a fallback.
    relic_mint_backend: str = Field(default="manifold")
    # Override for the Manifold implementation address the proxy points to.
    # Empty = the address recorded in the artifact (read from the chain when the
    # artifact was built). Set it only after checking a fresh Manifold Studio
    # deployment on Base points somewhere else.
    manifold_implementation: str = Field(default="")
    # The override above is refused unless this is true: pointing a relic at any
    # address other than the one the Manifold crowd uses (even our own copy of
    # their code) would make the pool a bytecode class of one again.
    manifold_implementation_override_ok: bool = Field(default=False)

    # ------------------------------------------------------------------
    # Target hunts (Option A, 2026-09): the treasure is an EXISTING NFT that
    # belongs to someone else. target_launch=False keeps every other mode
    # exactly as is. Turning it on is an env var, not a deploy.
    # ------------------------------------------------------------------
    target_launch: bool = Field(default=False)
    # Fernet key for the target artefacts (sealed target on the hunt row,
    # snapshot/registry/discovery blobs). SEPARATE from relic_pool_key by
    # decision (soldadura, decision 3): the registry is the most sensitive
    # artefact of the game and does not share a key with anything else.
    # ⚠️ Losing it loses the registry (weeks of era scans) and every sealed
    # target; a LIVE hunt could not be resumed. Back it up before /scan.
    target_pool_key: str = Field(default="")
    # Ethereum RPC (epoch 1 sources are all Ethereum; base_rpc_url covers
    # Base). R1: one named URL per chain, never a default chain.
    eth_rpc_url: str = Field(default="")
    # Curation epoch (selector.CurationEpoch): id + freshness ceiling.
    target_epoch_id: str = Field(default="")
    target_min_age_days: int = Field(default=180)
    target_max_snapshot_age_days: int = Field(default=14)
    # R2 canary for the era scan: a PINNED era block with KNOWN mints and its
    # MEASURED mint count. Both required, both ≠ 0 — the exact-count canary
    # is what catches truncation; one without the other is worth nothing.
    target_canary_block: int = Field(default=0)
    target_canary_mints: int = Field(default=0)
    # Per-stratum SAMPLED rates for the gate ("foundation:0.52,tail2021:0.35").
    # Unlisted strata count 0 (fail-closed). Re-measure per epoch.
    target_writability_rates: str = Field(default="")
    target_uniqueness_rates: str = Field(default="")
    # Blocks scanned per /scan run (Alchemy free: one block per eth_getLogs).
    target_scan_blocks: int = Field(default=300)
    # GENERIC read paths for the live check and the image fetch — public RPCs
    # and public IPFS gateways, NO KEY (they read the target inside its decoy
    # batch; nothing carrying our identity may read it). Comma-separated,
    # index-aligned: provider i = (ethereum[i], base[i], gateway[i]); rotation
    # is PER BATCH (adapters.GenericMetadata).
    target_public_rpcs_ethereum: str = Field(default="")
    target_public_rpcs_base: str = Field(default="")
    target_ipfs_gateways: str = Field(default="")
    # KEYED gateway for the refresh (resolves everyone's metadata — no secret).
    target_ipfs_gateway: str = Field(default="https://ipfs.io/ipfs/")
    # Judge (batched, text) and vision models — Anthropic client.
    target_judge_model: str = Field(default="claude-sonnet-4-6")
    target_vision_model: str = Field(default="claude-sonnet-4-6")
    # HOLD ceilings (R5): per episode and accumulated per hunt, seconds.
    target_max_hold_s: int = Field(default=6 * 3600)
    target_max_total_hold_s: int = Field(default=12 * 3600)
    target_hold_renotify_s: int = Field(default=3600)

    @staticmethod
    def _rates(spec: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for part in (spec or "").split(","):
            if ":" not in part:
                continue
            k, v = part.split(":", 1)
            try:
                out[k.strip()] = float(v)
            except ValueError:
                continue
        return out

    @property
    def target_writability_rate_map(self) -> dict[str, float]:
        return self._rates(self.target_writability_rates)

    @property
    def target_uniqueness_rate_map(self) -> dict[str, float]:
        return self._rates(self.target_uniqueness_rates)

    @staticmethod
    def _csv(spec: str) -> list[str]:
        return [x.strip() for x in (spec or "").split(",") if x.strip()]

    @property
    def target_public_rpc_map(self) -> dict[str, list[str]]:
        return {"ethereum": self._csv(self.target_public_rpcs_ethereum),
                "base": self._csv(self.target_public_rpcs_base)}

    @property
    def target_ipfs_gateway_list(self) -> list[str]:
        return self._csv(self.target_ipfs_gateways)

    def target_missing(self) -> list[str]:
        """What a target launch still lacks — config names only. Used by
        assert_ready_for_hunt and by /status."""
        missing = []
        if not self.target_pool_key:
            missing.append("target_pool_key")
        if not self.eth_rpc_url:
            missing.append("eth_rpc_url")
        if not self.target_epoch_id:
            missing.append("target_epoch_id")
        # search guard + uniqueness + chain probe all speak Rarible
        if not self.rarible_api_key:
            missing.append("rarible_api_key (search guard is mandatory)")
        if not (self.target_canary_block and self.target_canary_mints):
            missing.append("target_canary_block AND target_canary_mints (both ≠ 0)")
        if not self.target_writability_rate_map:
            missing.append("target_writability_rates")
        if not self.target_uniqueness_rate_map:
            missing.append("target_uniqueness_rates")
        if not (self.target_public_rpc_map["ethereum"] and self.target_ipfs_gateway_list):
            missing.append("target_public_rpcs_ethereum + target_ipfs_gateways (generic reads)")
        return missing

    @property
    def relic_wallet_ref_list(self) -> list[str]:
        return [r.strip() for r in (self.relic_wallet_refs or "").split(",") if r.strip()]

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
        # Relic mode has its own prerequisites. Checked HERE so a missing key
        # surfaces before a launch starts, not halfway through one.
        if self.relic_launch:
            if not self.relic_pool_key:
                missing.append("relic_pool_key (relic_launch is on)")
            # AT LEAST ONE marketplace key, not a specific one.
            #
            # This used to demand `opensea_api_key` by name, which contradicted
            # the composition in main.py: that builds a two-surface QUORUM when
            # both keys exist and falls back to the single surface that does.
            # So a Rarible-only setup staged a launch fine and then died HERE,
            # inside run_hunt — i.e. AFTER the operator confirmed, which is the
            # worst possible moment.
            #
            # It would also have detonated on its own schedule: OpenSea's free
            # key expires after 7 days, so the same crash was waiting for the
            # renewal to be forgotten once.
            #
            # A single verified surface is a weaker gate than two, and main.py
            # says so. But weaker-and-honest beats a launch that passes staging
            # and fails on confirm.
            if not (self.opensea_api_key or self.rarible_api_key):
                missing.append(
                    "opensea_api_key or rarible_api_key "
                    "(the findability gate needs at least one marketplace)"
                )
        # Target mode: same doctrine — everything a launch needs is checked
        # BEFORE the confirmation, never halfway through run_hunt.
        if self.target_launch:
            missing += [f"{m} (target_launch is on)" for m in self.target_missing()]
        if missing:
            raise RuntimeError(f"Cannot start hunt — missing config: {', '.join(missing)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()

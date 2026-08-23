"""Decoy scheduler — keeps the pool a LARGE, AGED, pattern-free anonymity set.

The real relic hides among decoys minted at irregular times from many wallets, so
"recently minted" never narrows the candidates and there is no cadence an observer
could lock onto. This module is PURE (decides what to mint and when); the caller
executes the mints via relic_mint on the returned schedule.

Two levers:
- TARGET SIZE: keep >= target_pool_size minted relics available (real + decoy).
- JITTERED CADENCE: the gap to the next mint is drawn uniformly from
  [min_gap_s, max_gap_s] (same idea as the clue cadence) so mints don't tick on a
  clock. The draw is injectable for deterministic tests.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class DecoyConfig:
    target_pool_size: int = 40      # aged decoys to keep available
    min_gap_s: int = 6 * 3600       # jittered cadence, low end
    max_gap_s: int = 30 * 3600      # high end
    max_batch: int = 1              # mints per wake-up (usually 1 — no bursts)


@dataclass(frozen=True)
class DecoyDecision:
    mint_now: int                   # how many to mint this wake-up (0 = wait)
    next_check_at: datetime         # when to wake up again
    reason: str


def _default_gap(cfg: DecoyConfig) -> int:
    span = max(0, cfg.max_gap_s - cfg.min_gap_s)
    return cfg.min_gap_s + (secrets.randbelow(span + 1) if span else 0)


def plan_decoys(
    *,
    cfg: DecoyConfig,
    pool_size: int,
    free_wallets: int,
    now: datetime | None = None,
    last_mint_at: datetime | None = None,
    gap_fn=_default_gap,
) -> DecoyDecision:
    """Decide whether to mint now.

    - Never mint without a free wallet (one wallet per relic; exhaustion is a
      fund-more signal, surfaced by WalletPool — here we simply wait).
    - Mint only while below target size, and only after the jittered gap since
      the last mint has elapsed (so cadence stays irregular).
    - Bursts are capped by max_batch and by free_wallets."""
    now = now or datetime.now(timezone.utc)
    gap = timedelta(seconds=gap_fn(cfg))

    if free_wallets <= 0:
        return DecoyDecision(0, now + gap, "no free wallet — fund more; waiting")

    if pool_size >= cfg.target_pool_size:
        return DecoyDecision(0, now + gap, f"pool at target ({pool_size}/{cfg.target_pool_size})")

    if last_mint_at is not None:
        elapsed = now - last_mint_at
        min_gap = timedelta(seconds=cfg.min_gap_s)
        if elapsed < min_gap:
            wait = min_gap - elapsed
            return DecoyDecision(0, now + wait, "cadence: too soon since last mint")

    want = cfg.target_pool_size - pool_size
    mint_now = max(0, min(cfg.max_batch, free_wallets, want))
    return DecoyDecision(mint_now, now + gap, f"minting {mint_now} (below target)")

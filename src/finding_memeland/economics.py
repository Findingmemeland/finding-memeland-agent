"""Prize economics — the dynamic per-hunt prize rule.

Prizes are decided per hunt by the operator via Telegram (/launch <usd>), but the
agent suggests a value from the live token valuation so the operator isn't
guessing. Rule (Pedro, 2026-06-25), continuous at both joints:

    prize($) = clamp( PCT_OF_FUND * prize_fund_value , FLOOR , CAP )

with prize_fund_value = VAULT_PCT * FDV. Defaults make it $200 below ~$100k FDV,
scale $200→$500 between $100k and $250k FDV, then flat $500 above. $200 floor =
the minimum prize worth playing for; $500 cap for now (higher tiers can be added
later for large FDVs).
"""

from __future__ import annotations

VAULT_PCT = 0.20
PCT_OF_FUND = 0.01
FLOOR_USD = 200.0
CAP_USD = 500.0


def fdv_from_price(price_usd: float, supply: float) -> float:
    """Fully-diluted valuation in $ = price per token * total supply."""
    return price_usd * supply


def suggested_prize(
    fdv: float,
    *,
    vault_pct: float = VAULT_PCT,
    pct_of_fund: float = PCT_OF_FUND,
    floor: float = FLOOR_USD,
    cap: float = CAP_USD,
) -> float:
    """Suggested prize ($) for a given FDV, per the rule. Clamped to [floor, cap]."""
    if fdv <= 0:
        return floor
    raw = fdv * vault_pct * pct_of_fund
    return max(floor, min(cap, raw))

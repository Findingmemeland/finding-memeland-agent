"""Small runtime adapters that implement the simple Orchestrator ports.

Kept dependency-light (no tweepy/web3/supabase imports) so they're testable; the
heavy clients are built in the composition root (main.py) and injected.
"""

from __future__ import annotations

import math
import os
import tempfile
from datetime import datetime, timezone


def round_sig(n: float, sig: int = 3) -> int:
    """Round to `sig` significant figures and return a clean whole number,
    e.g. 16,260,163 -> 16,300,000. Keeps prize/holding amounts readable."""
    if n <= 0:
        return 0
    digits = sig - int(math.floor(math.log10(n))) - 1
    return int(round(n, digits))


class SystemClock:
    """Real Clock port: wall-clock time + real sleep."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        import time

        time.sleep(seconds)


def write_temp_png(data: bytes) -> str:
    """Persist image bytes to a temp .png file and return its path (for the
    dresser's avatar/banner upload)."""
    fd, path = tempfile.mkstemp(suffix=".png")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


class ManualPriceFeed:
    """Converts a USD prize/floor to a WHOLE-token amount at a manually-set price.

    Until a live DEX price source is wired (post token launch), the operator sets
    `usd_per_token` (e.g. 0.0001). usd_to_fmml(500) -> 5_000_000 tokens at that price.
    """

    def __init__(self, usd_per_token: float, *, sig_figs: int = 3):
        # Allow 0 at construction so the agent can boot before the token exists;
        # it fails clearly only when a hunt actually tries to price a prize.
        self._price = usd_per_token
        self._sig = sig_figs

    def usd_to_fmml(self, usd: float) -> int:
        if self._price <= 0:
            raise RuntimeError("FMML_USD_PRICE not set — set it before launching a hunt")
        # Round to a clean whole number so the post and payout show e.g.
        # 16,300,000 $FIND, not 16,260,163. Same value used for post AND transfer.
        return round_sig(usd / self._price, self._sig)


class StdoutNotifier:
    """Fallback Notifier: prints. Replaced by the Telegram notifier in prod."""

    def notify(self, text: str) -> None:
        print(f"[notify] {text}")


def env_token_resolver(oauth_ref: str) -> tuple[str, str]:
    """Resolve a persona's OAuth tokens from env (Doppler injects them) by ref,
    e.g. ref '01' -> X_PERSONA_01_ACCESS_TOKEN / X_PERSONA_01_ACCESS_SECRET."""
    token = os.environ.get(f"X_PERSONA_{oauth_ref}_ACCESS_TOKEN", "")
    secret = os.environ.get(f"X_PERSONA_{oauth_ref}_ACCESS_SECRET", "")
    if not token or not secret:
        raise RuntimeError(f"missing OAuth tokens for persona ref {oauth_ref!r}")
    return token, secret

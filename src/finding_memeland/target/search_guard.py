"""Search guard — the mechanical form of "no puzzle-phase clue may be a
search" (Opus, 2026-09-04).

Why mechanical and not a prompt instruction: prompts are how emoji were
banned, and emoji appeared anyway; difficulty in this codebase is enforced by
proof, not by prompt (guardrails.py, the blind solver). This guard runs the
attack it defends against: it takes the candidate clue TEXT, uses it as a
marketplace search query, and REJECTS the clue if the target surfaces in the
results. That closes the whole class in one test — a clue quoting the name, a
too-literal art description matching indexed traits, a themed-collection tell
that resolves to the collection and thence the target.

Fail-closed, like every guardrail here: a clue whose searchability cannot be
verified (API down after retries) is NOT publishable. Clue cadence is a random
band, so a delayed piece is a non-event; a leaked piece is forever.

The marketplace adapter is injected; RaribleSearch mirrors the measured
request shape from relic_findability.py (2026-08-25: X-API-KEY header,
`fullText` filter, 429 retry).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol


class MarketSearch(Protocol):
    """Full-text search over the marketplace's NFT index.

    Returns the item ids ('CHAIN:0xcontract:tokenId', upper-cased chain, as
    Rarible formats them) surfaced for `text`. Raises on transport failure —
    the guard turns that into a fail-closed verdict, adapters never guess."""

    def item_ids(self, text: str) -> set[str]: ...


@dataclass(frozen=True)
class SearchGuardVerdict:
    """`ok` is the ONLY field the clue pipeline may act on: False means the
    piece is rejected, whether because the target surfaced (`found=True`) or
    because searchability could not be verified (`found=None`). `detail` is
    for the operator log — it never carries the target's name."""

    ok: bool
    found: bool | None
    detail: str


class ClueSearchGuard:
    """Reject any clue that works as a search query for the target."""

    def __init__(self, *, search: MarketSearch, retries: int = 2,
                 sleep_s: float = 2.0):
        self._search = search
        self._retries = retries
        self._sleep = sleep_s

    def check(self, clue_text: str, *, target_item_id: str) -> SearchGuardVerdict:
        """`target_item_id` is 'CHAIN:0xcontract:tokenId' (Target.id() upper
        chain). The clue text goes to the marketplace verbatim — no keyword
        extraction: we test the exact artefact the public would see."""
        want = _canonical(target_item_id)
        last_err = "unknown"
        for attempt in range(self._retries + 1):
            try:
                ids = {_canonical(i) for i in self._search.item_ids(clue_text)}
            except Exception as e:  # noqa: BLE001 — retry, then fail closed
                last_err = str(e)[:120]
                if attempt < self._retries:
                    time.sleep(self._sleep * (attempt + 1))
                    continue
                return SearchGuardVerdict(
                    ok=False, found=None,
                    detail=f"unverifiable after {self._retries + 1} attempts "
                           f"({last_err}) — fail-closed, piece not publishable",
                )
            if want in ids:
                return SearchGuardVerdict(
                    ok=False, found=True,
                    detail="clue text surfaces the target on the marketplace "
                           "— the piece IS a search; rejected",
                )
            return SearchGuardVerdict(
                ok=True, found=False,
                detail=f"target absent from {len(ids)} search results",
            )
        return SearchGuardVerdict(ok=False, found=None, detail=last_err)


def _canonical(item_id: str) -> str:
    return item_id.strip().upper()


# --------------------------------------------------------------------------- #
# Real adapter — needs a live connection (NOT sandbox-testable)                #
# --------------------------------------------------------------------------- #


class RaribleSearch:
    """items/search with the MEASURED request shape (relic_findability.py,
    2026-08-25): X-API-KEY header (Bearer returns 403), `fullText` filter
    (`text` misses targets). Raises on failure — the guard owns fail-closed.

    `http_post(url, body: bytes, headers: dict) -> str` is injected."""

    def __init__(self, *, http_post, api_key: str,
                 base_url: str = "https://api.rarible.org/v0.1",
                 chain: str = "BASE", size: int = 25):
        if not api_key:
            raise ValueError("RaribleSearch needs an api key — without one the "
                             "guard would fail every clue, stalling the hunt")
        self._post = http_post
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._chain = chain
        self._size = size

    def item_ids(self, text: str) -> set[str]:
        body = json.dumps({
            "size": self._size,
            "filter": {"fullText": {"text": text}, "blockchains": [self._chain]},
        }).encode()
        raw = self._post(f"{self._base}/items/search", body, {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-KEY": self._key,
        })
        payload = json.loads(raw or "{}")
        return {str(item.get("id", "")) for item in payload.get("items", []) or []}


class FakeSearch:
    """MarketSearch fake: maps query substrings to item-id sets (tests)."""

    def __init__(self, hits: dict[str, set[str]] | None = None, *,
                 raises: bool = False):
        self._hits = hits or {}
        self._raises = raises

    def item_ids(self, text: str) -> set[str]:
        if self._raises:
            raise RuntimeError("marketplace unreachable")
        out: set[str] = set()
        for needle, ids in self._hits.items():
            if needle.lower() in text.lower():
                out |= ids
        return out

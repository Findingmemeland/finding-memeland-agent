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

THE CANARY (Opus re-review, 05/09 — P0-4). The first version searched with a
chain fixed at construction ("BASE") while every epoch-1 target lives on
Ethereum: the target could never surface, "absent from 25 results" read as
approval, and EVERY clue passed — including one quoting the name verbatim.
A guard that cannot see its own target has no authority to call a clue
safe. So, before testing the clue, the guard searches the target's exact
on-chain NAME on the target's own chain and REQUIRES the target to appear.
If it doesn't, the index is blind to the target — wrong chain, unindexed
token, key without permissions, malformed filter, or the next variant of
the same family — and the guard refuses (ok=False, found=None) instead of
approving. The chain is never configured: it is derived from the target id
the caller passes, so it cannot drift from the commitment.

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
    """Full-text search over the marketplace's NFT index, on ONE chain.

    `chain` is the marketplace's upper-cased blockchain enum ('ETHEREUM',
    'BASE', …), derived by the guard from the target id — adapters never
    hold a chain of their own. Returns the item ids ('CHAIN:0xcontract:
    tokenId', upper-cased chain, as Rarible formats them) surfaced for
    `text`. Raises on transport failure — the guard turns that into a
    fail-closed verdict, adapters never guess."""

    def item_ids(self, text: str, *, chain: str) -> set[str]: ...


@dataclass(frozen=True)
class SearchGuardVerdict:
    """`ok` is the ONLY field the clue pipeline may act on: False means the
    piece is rejected, whether because the target surfaced (`found=True`) or
    because searchability could not be verified (`found=None` — transport
    failure OR a blind canary). `detail` is for the operator log — it never
    carries the target's name, the clue text, or the chain."""

    ok: bool
    found: bool | None
    detail: str


class _Unverifiable(Exception):
    pass


class ClueSearchGuard:
    """Reject any clue that works as a search query for the target."""

    def __init__(self, *, search: MarketSearch, retries: int = 2,
                 sleep_s: float = 2.0):
        self._search = search
        self._retries = retries
        self._sleep = sleep_s

    def check(self, clue_text: str, *, target_item_id: str,
              target_name_onchain: str) -> SearchGuardVerdict:
        """`target_item_id` is 'CHAIN:0xcontract:tokenId' (Target.id(), any
        case); `target_name_onchain` is the exact metadata name (Target.
        name_onchain) — the canary query. The clue text goes to the
        marketplace verbatim — no keyword extraction: we test the exact
        artefact the public would see."""
        want = _canonical(target_item_id)
        chain = _chain_of(want)
        if not chain or not target_name_onchain.strip():
            return SearchGuardVerdict(
                ok=False, found=None,
                detail="target id or on-chain name missing — the guard "
                       "cannot run its canary; fail-closed, piece not "
                       "publishable")

        # -- 1. canary: can this index see the target at all? ------------- #
        try:
            seen = self._search_with_retries(target_name_onchain, chain)
        except _Unverifiable as e:
            return SearchGuardVerdict(ok=False, found=None, detail=str(e))
        if want not in seen:
            return SearchGuardVerdict(
                ok=False, found=None,
                detail=f"canary failed: the target does not surface for its "
                       f"own on-chain name ({len(seen)} results) — the index "
                       "is blind to the target (chain filter, indexing, key "
                       "or request shape); a blind guard approves nothing. "
                       "Fail-closed, piece not publishable")

        # -- 2. the clue itself ------------------------------------------- #
        try:
            ids = self._search_with_retries(clue_text, chain)
        except _Unverifiable as e:
            return SearchGuardVerdict(ok=False, found=None, detail=str(e))
        if want in ids:
            return SearchGuardVerdict(
                ok=False, found=True,
                detail="clue text surfaces the target on the marketplace "
                       "— the piece IS a search; rejected")
        return SearchGuardVerdict(
            ok=True, found=False,
            detail=f"canary ok; target absent from {len(ids)} search results")

    def _search_with_retries(self, text: str, chain: str) -> set[str]:
        last_err = "unknown"
        for attempt in range(self._retries + 1):
            try:
                return {_canonical(i)
                        for i in self._search.item_ids(text, chain=chain)}
            except Exception as e:  # noqa: BLE001 — retry, then fail closed
                last_err = str(e)[:120]
                if attempt < self._retries:
                    time.sleep(self._sleep * (attempt + 1))
        raise _Unverifiable(
            f"unverifiable after {self._retries + 1} attempts ({last_err}) "
            "— fail-closed, piece not publishable")


def _canonical(item_id: str) -> str:
    return item_id.strip().upper()


def _chain_of(canonical_item_id: str) -> str:
    """'ETHEREUM:0X…:7' -> 'ETHEREUM'. Empty when the id has no chain."""
    head, sep, _rest = canonical_item_id.partition(":")
    return head if sep and head and not head.startswith("0X") else ""


# --------------------------------------------------------------------------- #
# Real adapter — needs a live connection (NOT sandbox-testable)                #
# --------------------------------------------------------------------------- #


class RaribleSearch:
    """items/search with the MEASURED request shape (relic_findability.py,
    2026-08-25): X-API-KEY header (Bearer returns 403), `fullText` filter
    (`text` misses targets). Raises on failure — the guard owns fail-closed.

    No chain here by design (P0-4): the blockchains filter comes from the
    guard per call, derived from the target. Rarible's enum is the
    upper-cased chain slug ('ETHEREUM', 'BASE', 'POLYGON').

    `http_post(url, body: bytes, headers: dict) -> str` is injected."""

    def __init__(self, *, http_post, api_key: str,
                 base_url: str = "https://api.rarible.org/v0.1",
                 size: int = 25):
        if not api_key:
            raise ValueError("RaribleSearch needs an api key — without one the "
                             "guard would fail every clue, stalling the hunt")
        self._post = http_post
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._size = size

    def item_ids(self, text: str, *, chain: str) -> set[str]:
        body = json.dumps({
            "size": self._size,
            "filter": {"fullText": {"text": text},
                       "blockchains": [chain.upper()]},
        }).encode()
        raw = self._post(f"{self._base}/items/search", body, {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-KEY": self._key,
        })
        payload = json.loads(raw or "{}")
        return {str(item.get("id", "")) for item in payload.get("items", []) or []}


class FakeSearch:
    """MarketSearch fake: maps query substrings to item-id sets (tests).
    Only items whose id starts with the queried chain are returned — the
    fake behaves like a real per-chain index, so a guard querying the wrong
    chain sees nothing (the P0-4 blindness, reproducible offline)."""

    def __init__(self, hits: dict[str, set[str]] | None = None, *,
                 raises: bool = False):
        self._hits = hits or {}
        self._raises = raises
        self.queries: list[tuple[str, str]] = []

    def item_ids(self, text: str, *, chain: str) -> set[str]:
        if self._raises:
            raise RuntimeError("marketplace unreachable")
        self.queries.append((text, chain))
        out: set[str] = set()
        for needle, ids in self._hits.items():
            if needle.lower() in text.lower():
                out |= {i for i in ids if i.upper().startswith(chain.upper() + ":")}
        return out

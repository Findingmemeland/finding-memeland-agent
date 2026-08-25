"""Findability gate — the relic MUST index by NAME before Clue 1, or the launch
is REFUSED (fail-closed, R3 discipline).

⚠️ THE ORIGINAL DESIGN WAS WRONG, AND MEASURED WRONG (2026-08-23).

It made BaseScan canonical, on the theory that an explorer is neutral and
deterministic while marketplaces personalise. The theory is fine; the fact is
not: **BaseScan does not index NFT names at all**. Verified against a control
NFT that had been live for 17 hours — searching its name returned nothing, so
this is structural and not indexing lag. Left as-is, the gate would have refused
EVERY launch, forever.

What actually works, measured: OpenSea and Rarible index a fresh 1/1 by name in
about three minutes.

So the gate is now a QUORUM of marketplaces: two independent surfaces must both
find the relic. That answers the original (correct) worry about personalisation
and caching — one marketplace hiding a new 1/1 is plausible, two agreeing is
evidence — while using the only surfaces that can answer the question at all.
BaseScan stays as an informational check on the CONTRACT, which it does index.

The HTTP adapters need a live connection. The gate LOGIC is testable with fakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class FindabilityCheck(Protocol):
    """Does searching `name` surface this relic on a surface? `contract` lets the
    check confirm it's OUR relic and not a namesake."""

    name: str  # a human label for the surface, e.g. "basescan" (for reports)

    def is_indexed_by_name(self, name: str, *, contract: str | None = None) -> bool: ...


@dataclass
class FindabilityReport:
    canonical_ok: bool
    canonical_surface: str
    secondary: dict = field(default_factory=dict)  # surface -> bool (informational)


class FindabilityRefused(RuntimeError):
    """Raised to REFUSE a launch when the canonical surface doesn't index the
    relic. Never downgraded to a warning — an unfindable relic = an unsolvable
    hunt."""


def assert_findable_or_refuse(
    relic_name: str,
    *,
    canonical: FindabilityCheck,
    secondary: tuple[FindabilityCheck, ...] = (),
    contract: str | None = None,
) -> FindabilityReport:
    """Run the canonical check (fail-closed) plus any informational ones. Raises
    FindabilityRefused if the canonical surface does not index the relic by name.

    Secondary failures are recorded but do NOT block — they're for the operator's
    eyes (and for deciding whether clues should name a specific surface)."""
    sec: dict = {}
    for chk in secondary:
        try:
            sec[chk.name] = bool(chk.is_indexed_by_name(relic_name, contract=contract))
        except Exception:  # noqa: BLE001 — informational only, never blocks
            sec[chk.name] = None

    canonical_ok = canonical.is_indexed_by_name(relic_name, contract=contract)
    report = FindabilityReport(
        canonical_ok=canonical_ok, canonical_surface=canonical.name, secondary=sec
    )
    if not canonical_ok:
        raise FindabilityRefused(
            f"relic {relic_name!r} not indexed on the canonical surface "
            f"({canonical.name}) — launch REFUSED (fail-closed). Give it more "
            f"time to index, or investigate the mint."
        )
    return report


class QuorumFindability:
    """N independent surfaces must agree the relic is findable by name.

    Implements FindabilityCheck, so it drops straight into `canonical=` and the
    fail-closed logic above is untouched.

    A surface that ERRORS counts as a NO, never as a pass: the whole point of the
    gate is that we do not launch on hope. And with `required=2` a single
    marketplace caching or hiding a new 1/1 — the original, valid objection to
    using marketplaces — cannot by itself let a launch through OR block it, since
    the other surface still has to agree either way."""

    def __init__(self, checks: tuple, *, required: int = 2, name: str = "marketplaces"):
        if required > len(checks):
            raise ValueError(
                f"quorum needs {required} surfaces but only {len(checks)} were given "
                "— a quorum that can never be met would refuse every launch"
            )
        self.name = name
        self._checks = tuple(checks)
        self._required = required

    def is_indexed_by_name(self, name: str, *, contract: str | None = None) -> bool:
        return self.results(name, contract=contract)[0]

    def results(self, name: str, *, contract: str | None = None) -> tuple[bool, dict]:
        """(quorum_met, per-surface detail) — the detail goes in the operator's
        launch report so a refusal says WHICH surface was missing."""
        detail: dict = {}
        for chk in self._checks:
            try:
                detail[chk.name] = bool(chk.is_indexed_by_name(name, contract=contract))
            except Exception:  # noqa: BLE001 — unreachable == not findable
                detail[chk.name] = None
        return sum(1 for v in detail.values() if v) >= self._required, detail


# --------------------------------------------------------------------------- #
# Real adapter — needs a live connection (NOT sandbox-testable)                #
# --------------------------------------------------------------------------- #


class OpenSeaFindability:
    """Name search via OpenSea's documented v2 search endpoint.

    GET /api/v2/search?query=…&chains=base&asset_types=nft, with the key in an
    X-API-KEY header.

    The response is scanned RECURSIVELY for the contract (or the name) rather
    than read at a fixed path. That is deliberate: this code has to keep working
    when a marketplace reshapes its JSON, and a false NEGATIVE here only delays a
    launch while a crash would break the pipeline. Same reason a missing key or
    any HTTP error returns False instead of raising — the gate is fail-closed by
    design and the caller already treats "not findable" as "do not launch"."""

    name = "opensea"

    def __init__(self, *, http_get, api_key: str = "",
                 base_url: str = "https://api.opensea.io", chain: str = "base"):
        self._get = http_get      # callable(url, headers: dict) -> text
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._chain = chain

    def is_indexed_by_name(self, name: str, *, contract: str | None = None) -> bool:
        import json as _json
        from urllib.parse import quote

        if not self._key:
            return False
        url = (
            f"{self._base}/api/v2/search?query={quote(name)}"
            f"&chains={self._chain}&asset_types=nft&limit=20"
        )
        try:
            raw = self._get(url, {"X-API-KEY": self._key, "Accept": "application/json"})
            payload = _json.loads(raw or "{}")
        except Exception:  # noqa: BLE001 — unreachable/garbled == not findable
            return False
        needle = (contract or name).lower()
        return _contains_value(payload, needle)


class RaribleFindability:
    """Name search via Rarible's items/search endpoint.

    Every detail here was MEASURED against a relic known to be indexed
    (2026-08-25), because the docs show the endpoint but not the filter:

      · header is `X-API-KEY` — `Authorization: Bearer` returns 403
      · the text filter is `fullText`, not `text`: `text` returned 20 unrelated
        items and missed the target
      · the API rate-limits with 429, so a single burst can refuse a launch that
        should have passed — hence the retry below

    `http_post(url, body: bytes, headers: dict) -> str` is injected so tests run
    offline. Fail-closed like every other surface: unreachable == not findable.
    """

    name = "rarible"

    def __init__(self, *, http_post, api_key: str = "",
                 base_url: str = "https://api.rarible.org/v0.1", chain: str = "BASE",
                 retries: int = 2, sleep_s: float = 2.0):
        self._post = http_post
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._chain = chain
        self._retries = retries
        self._sleep = sleep_s

    def is_indexed_by_name(self, name: str, *, contract: str | None = None) -> bool:
        import json as _json
        import time

        if not self._key:
            return False
        body = _json.dumps({
            "size": 20,
            "filter": {"fullText": {"text": name}, "blockchains": [self._chain]},
        }).encode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-KEY": self._key,
        }
        needle = (contract or name).lower()
        for attempt in range(self._retries + 1):
            try:
                raw = self._post(f"{self._base}/items/search", body, headers)
                return _contains_value(_json.loads(raw or "{}"), needle)
            except Exception as e:  # noqa: BLE001
                # A 429 is temporary and must not be read as "not findable" —
                # that would refuse a launch for a relic that IS indexed.
                if "429" in str(e) and attempt < self._retries:
                    time.sleep(self._sleep * (attempt + 1))
                    continue
                return False
        return False


def _contains_value(node, needle: str) -> bool:
    """Does `needle` appear as (part of) any string anywhere in the payload?

    Schema-agnostic on purpose — see OpenSeaFindability. Only strings are
    compared, so a numeric token id can never accidentally match a name."""
    if isinstance(node, str):
        return needle in node.lower()
    if isinstance(node, dict):
        return any(_contains_value(v, needle) for v in node.values())
    if isinstance(node, (list, tuple)):
        return any(_contains_value(v, needle) for v in node)
    return False


class BaseScanFindability:
    """⚠️ NOT USABLE AS THE NAME GATE — kept for CONTRACT checks only.

    Measured 2026-08-23: BaseScan's search does not index NFT names, verified
    against a 17-hour-old control NFT. Passing this as `canonical=` refuses every
    launch. Use it in `secondary=` to confirm the contract exists, and let
    QuorumFindability over marketplaces decide findability by name."""

    name = "basescan"

    def __init__(self, *, http_get, base_url: str = "https://basescan.org"):
        self._get = http_get          # callable(url) -> text (injected; requests/httpx)
        self._base = base_url.rstrip("/")

    def is_indexed_by_name(self, name: str, *, contract: str | None = None) -> bool:
        html = self._fetch(name)
        if not html:
            return False
        low = html.lower()
        if "did not match any records" in low:
            return False
        if contract:
            return contract.lower() in low
        # No contract to disambiguate: require the exact name to appear in results.
        return name.lower() in low

    def _fetch(self, name: str) -> str:  # pragma: no cover - network
        from urllib.parse import quote

        return self._get(f"{self._base}/search?f=0&q={quote(name)}")


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class FakeFindability:
    def __init__(self, name: str, indexed: set[str] | None = None, *, raises: bool = False):
        self.name = name
        self._indexed = set(indexed or ())
        self._raises = raises

    def is_indexed_by_name(self, name: str, *, contract: str | None = None) -> bool:
        if self._raises:
            raise RuntimeError("surface unreachable")
        return name in self._indexed

"""Findability gate — the relic MUST index by NAME before Clue 1, or the launch
is REFUSED (fail-closed, R3 discipline).

BaseScan is the CANONICAL surface: a neutral, deterministic index that returns the
same result for everyone — this is the fairness upgrade over X (whose search is
inconsistent per user). Marketplaces (OpenSea/Rarible) are INFORMATIONAL only:
they personalise, cache and can hide new 1/1s, so they can never be the gate.

The HTTP adapter needs a live Base connection (dry-run: mainnet). The gate LOGIC
is testable with fakes.
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


# --------------------------------------------------------------------------- #
# Real adapter — needs a live connection (NOT sandbox-testable)                #
# --------------------------------------------------------------------------- #


class BaseScanFindability:
    """Canonical check via BaseScan's search. `_fetch` (the HTTP call) is
    overridden in tests; the parsing/decision is pure. We require the relic's
    NAME to resolve to a token/collection AND (when given) the contract to match,
    so a namesake never passes the gate."""

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

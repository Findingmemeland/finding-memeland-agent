"""Claim parsing/validation for Option A — the player names the TOKEN.

Ratified rules (Opus, 05/09) this module implements:

  · a claim identifies the target as chain:contract:tokenId — the SAME
    canonical string as Target.id(), which is what the commitment sealed
  · a marketplace LINK is accepted as a courtesy, but what gets validated
    is ALWAYS the chain:contract:tokenId extracted from it — never the URL
    text, never a slug, never a name
  · clues NEVER state the chain, so the chain is part of the answer: an
    explicit paste WITHOUT a chain ("0x…:12") is claim-shaped (the oracle
    may answer with the public format rule) but can never match. The format
    is a pre-committed public rule, not a judgment call.

Secrecy discipline (same as the whole target package): nothing here ever
formats the hunt's target id into a repr, log line or verdict — verdicts
carry COUNTS. Player-pasted addresses are the player's own public post and
still don't get echoed by us.

Link parsing is structural (find contract+tokenId+chain in the URL), so it
covers OpenSea /assets/ and /item/, Rarible /token/, Zora /collect/, the
*scan NFT pages, Blur and LooksRare without one parser per marketplace.
Slug-only links (Foundation @artist pages, SuperRare artworks) carry NO
contract — those go to `unresolved_links` for an injected resolver the
production wiring provides (marketplace API → chain:contract:tokenId).
Fail-closed: an unresolved or unresolvable link matches nothing.

⚠️ Before Hunt #11's dry-run, exercise the link shapes against REAL
marketplace URLs (the measured-adapter discipline) — URL formats drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlsplit

# Canonical chain slugs — Target.id() vocabulary. Aliases map what players
# and marketplaces write to what the commitment sealed.
CHAIN_ALIASES = {
    "ethereum": "ethereum", "eth": "ethereum", "mainnet": "ethereum",
    "base": "base",
    "polygon": "polygon", "matic": "polygon",
    "arbitrum": "arbitrum",
    "optimism": "optimism", "oeth": "optimism",
    "zora": "zora",
}

# Domains that imply a chain when the URL itself names none.
_DOMAIN_CHAIN = {
    "etherscan.io": "ethereum",
    "basescan.org": "base",
    "polygonscan.com": "polygon",
    "arbiscan.io": "arbitrum",
    "blur.io": "ethereum",
    "looksrare.org": "ethereum",
    "rarible.com": "ethereum",   # rarible defaults to ethereum; other
                                 # chains appear as a /token/<chain>/ segment
}

# Marketplace-ish hosts: a link here that we could NOT parse still counts
# as a claim attempt (unresolved), never as chatter.
_MARKET_HOSTS = (
    "opensea.io", "rarible.com", "foundation.app", "superrare.com",
    "zora.co", "looksrare.org", "blur.io", "etherscan.io", "basescan.org",
    "polygonscan.com", "arbiscan.io",
)

_URL_RE = re.compile(r"https?://\S+")
_ADDR_TID_RE = re.compile(r"(0x[0-9a-fA-F]{40})[:/](\d+)\b")
_QUERY_TID_RE = re.compile(r"[?&](?:tokenId|token_id)=(\d+)\b")
_ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}\b")
_EXPLICIT_RE = re.compile(
    r"\b([A-Za-z]{2,12}):(0x[0-9a-fA-F]{40}):(\d+)\b")
_CHAINLESS_RE = re.compile(r"(?<![:\w])(0x[0-9a-fA-F]{40}):(\d+)\b")
_SEG_SPLIT_RE = re.compile(r"[/:?=&#.]+")


@dataclass(frozen=True)
class TargetRef:
    """A player's identification of a token, canonicalised."""

    chain: str
    contract: str
    token_id: int

    def id(self) -> str:
        return f"{self.chain}:{self.contract.lower()}:{self.token_id}"


@dataclass(frozen=True)
class ClaimExtraction:
    """What one post contains: fully-parsed refs, plus marketplace links we
    could not turn into a triple (a production resolver may still can)."""

    refs: tuple[TargetRef, ...]
    unresolved_links: tuple[str, ...]


def _canonical_chain(word: str) -> str | None:
    return CHAIN_ALIASES.get(word.strip().lower())


def parse_explicit(text: str) -> tuple[TargetRef, ...]:
    """chain:contract:tokenId pastes — the published claim format."""
    out: list[TargetRef] = []
    for chain_word, addr, tid in _EXPLICIT_RE.findall(text or ""):
        chain = _canonical_chain(chain_word)
        if chain is None:
            continue
        ref = TargetRef(chain=chain, contract=addr.lower(), token_id=int(tid))
        if ref not in out:
            out.append(ref)
    return tuple(out)


def parse_link(url: str) -> TargetRef | None:
    """One URL → a triple, or None. Structural: needs a 0x-contract with an
    adjacent tokenId AND a chain (URL segment, else domain implication)."""
    url = (url or "").strip().rstrip(")>.,;!?'\"")
    m = _ADDR_TID_RE.search(url)
    if m:
        addr, tid = m.group(1), int(m.group(2))
    else:
        # contract in the path, tokenId in the query (?tokenId=42) — the
        # other shape marketplaces use (Opus review, 05/09)
        ma = _ADDR_RE.search(url)
        mq = _QUERY_TID_RE.search(url)
        if not (ma and mq):
            return None
        addr, tid = ma.group(0), int(mq.group(1))

    chain: str | None = None
    parts = urlsplit(url)
    for seg in _SEG_SPLIT_RE.split(parts.path):
        c = _canonical_chain(seg)
        if c is not None:
            chain = c
            break
    if chain is None:
        host = parts.netloc.lower().split("@")[-1].split(":")[0]
        for dom, c in _DOMAIN_CHAIN.items():
            if host == dom or host.endswith("." + dom):
                chain = c
                break
    if chain is None:
        return None
    return TargetRef(chain=chain, contract=addr.lower(), token_id=tid)


def _is_market_host(url: str) -> bool:
    host = urlsplit(url).netloc.lower().split("@")[-1].split(":")[0]
    return any(host == d or host.endswith("." + d) for d in _MARKET_HOSTS)


def extract_target_refs(text: str) -> ClaimExtraction:
    """Everything claim-relevant in a post, deduped, in order."""
    refs: list[TargetRef] = []
    unresolved: list[str] = []
    for raw in _URL_RE.findall(text or ""):
        url = raw.rstrip(")>.,;!?'\"")
        ref = parse_link(url)
        if ref is not None:
            if ref not in refs:
                refs.append(ref)
        elif _is_market_host(url) or _ADDR_RE.search(url):
            if url not in unresolved:
                unresolved.append(url)
    # explicit triples OUTSIDE urls (strip urls first so a rarible
    # "…/token/0x…:1" never double-counts as an explicit paste)
    stripped = _URL_RE.sub(" ", text or "")
    for ref in parse_explicit(stripped):
        if ref not in refs:
            refs.append(ref)
    return ClaimExtraction(refs=tuple(refs), unresolved_links=tuple(unresolved))


def claim_shaped(text: str) -> bool:
    """Does the post LOOK like a claim attempt? Explicit triple, chainless
    contract:tokenId paste, or a marketplace-ish link. Drives the oracle's
    format-rule reply and the guess cap — never a match by itself."""
    ext = extract_target_refs(text)
    if ext.refs or ext.unresolved_links:
        return True
    stripped = _URL_RE.sub(" ", text or "")
    return bool(_CHAINLESS_RE.search(stripped))


@dataclass(frozen=True)
class ClaimVerdict:
    """Counts only — never the target, never the refs."""

    matched: bool
    checked: int
    unresolved: int

    def render(self) -> str:
        if self.matched:
            return "claim: MATCH"
        bits = [f"claim: no match ({self.checked} verificado(s)"]
        if self.unresolved:
            bits.append(f", {self.unresolved} link(s) por resolver")
        return "".join(bits) + ")"


class ClaimJudge:
    """Holds the hunt's sealed target id; judges posts. The id NEVER
    appears in repr/str/verdicts — this object lives next to the salt."""

    def __init__(self, *, target_id: str):
        self._target = target_id.strip()

    def judge(self, text: str, *,
              resolve_link: Callable[[str], TargetRef | None] | None = None,
              ) -> ClaimVerdict:
        ext = extract_target_refs(text)
        refs: list[TargetRef] = list(ext.refs)
        unresolved = 0
        for url in ext.unresolved_links:
            ref = None
            if resolve_link is not None:
                try:
                    ref = resolve_link(url)
                except Exception:  # noqa: BLE001 — fail-closed: no match
                    ref = None
            if ref is None:
                unresolved += 1
            elif ref not in refs:
                refs.append(ref)
        matched = any(r.id() == self._target for r in refs)
        return ClaimVerdict(matched=matched, checked=len(refs),
                            unresolved=unresolved)

    def __repr__(self) -> str:  # never the id
        return "ClaimJudge(target set)"

    __str__ = __repr__

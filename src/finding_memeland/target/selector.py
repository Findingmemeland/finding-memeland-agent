"""Target selector — picks the hunt's hidden target from a curated stratum of
EXISTING NFTs on Base (Option A; Opus-validated 2026-09-04).

Threat model this encodes
=========================
The target carries NO secret: nothing in its metadata can be harvested ahead
of a hunt. What remains is multiple-choice guessing — an attacker builds the
candidate set consistent with the published clues and sprays guesses (budget
G = guess cap x sybil accounts, ~5,000 for a well-equipped attacker). The
defences, in the order this module supplies them:

  · the stratum the selector draws from stays far above the validated gate
    (effective universe >= 100,000; < 20,000 refuses selection outright);
    the gate itself is measured by scripts/medir_universo_alvos.py +
    scripts/testar_escrevibilidade.py, not assumed here
  · the pick inside the stratum is uniformly random, so knowing the curation
    criteria never shrinks the candidate set below the stratum size
  · curation criteria ROTATE by epoch (Opus, 2026-09-04: thirty revealed
    targets would otherwise let an attacker learn the curation better than we
    documented it) — every Target records the epoch that produced it
  · the metadata hash taken at selection time goes into the public commitment;
    if the token's owner mutates it mid-hunt the reveal cannot verify and the
    hunt is VOIDED, prize back to the vault

Hard filters (all token-level; measured 2026-09-03..04)
=======================================================
  · metadata resolves and has an image (a target nobody can look at is
    unwritable and unfindable)
  · the BASE NAME — trailing serial stripped: 'Tiny Punk #9278' -> 'Tiny
    Punk' — has >= 2 real words; clues cipher the base name
  · the base name is UNIQUE on the marketplace: searching it must surface
    exactly this contract:tokenId. This is what kills serial collections
    (every item shares the base name) while letting distinctly-named tokens
    inside shared platform contracts qualify — curation is about name
    distinction, never contract shape
  · the owner is an EOA — eth_getCode(ownerOf) == 0x. A contract owner means
    lending vaults, escrow, fractionalisation: custody that can move or
    mutate mid-hunt for reasons no clue can anticipate
  · minimum age is the SOURCE's contract obligation (sources sample only
    blocks older than the epoch's min_age_days); the selector trusts it

Fail-closed discipline (R3): a filter that cannot be evaluated (RPC down,
marketplace 429) REJECTS the candidate — the measurement scripts may pass
indeterminates with a warning, production selection never does.

Every network touch is injected (`fetch_metadata`, `owner_is_eoa`,
`name_is_unique`, the candidate source), so the selection logic tests offline.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Protocol

# --------------------------------------------------------------------------- #
# Base-name normalisation — FROZEN with the commitment protocol: the reveal    #
# names the base name, and verifiers recompute it from the on-chain name.     #
# --------------------------------------------------------------------------- #

_WORD = re.compile(r"[A-Za-zÀ-ÿ]{2,}")
# trailing serial: '#9278', ' 12684', '- 27132', 'No. 7', 'nº 3' — repeatedly
_SERIAL_TAIL = re.compile(
    r"[\s\-–—_.:]*(?:#|n[oº]\.?\s*)?\d{1,10}\s*$", re.IGNORECASE)


def normalize_name(name: str) -> str:
    """The BASE name: trailing serials stripped ('Tiny Punk #9278' -> 'Tiny
    Punk', 'healing lucid glow 12684' -> 'healing lucid glow'). Clues are
    written about the base name and uniqueness is required of it. If stripping
    would erase everything, the original (stripped of whitespace) is kept."""
    base = name.strip()
    while True:
        shorter = _SERIAL_TAIL.sub("", base).strip()
        if shorter == base or not shorter:
            break
        base = shorter
    return base or name.strip()


def name_qualifies(base_name: str, *, min_words: int = 2) -> bool:
    """>= min_words real words (2+ letters each) in the BASE name."""
    return len(_WORD.findall(base_name)) >= min_words


def metadata_hash(meta: dict) -> str:
    """SHA-256 over the canonical JSON of the token metadata AT SELECTION TIME.

    Part of the public commitment (Clue 1): sort_keys + ensure_ascii=False +
    utf-8, matching scripts/medir_universo_alvos.py. FROZEN — anyone must be
    able to recompute it from the reveal, and a mid-hunt metadata mutation
    must break verification (hunt voided, prize back to the vault)."""
    payload = json.dumps(meta, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------- #
# Data model                                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Target:
    """A selected hunt target. `name` is the BASE name (what clues cipher and
    what must be unique); `name_onchain` is the full metadata name shown at
    reveal. Claims validate against `id()` — never against the name."""

    chain: str
    contract: str
    token_id: int
    name: str
    name_onchain: str
    description: str
    image: str
    metadata_sha256: str
    epoch: str

    def id(self) -> str:
        """'base:0x…:5' — the value inside the integrity commitment, and the
        only thing a claim is validated against."""
        return f"{self.chain}:{self.contract.lower()}:{self.token_id}"


@dataclass(frozen=True)
class CurationEpoch:
    """One epoch of curation criteria. Rotating epochs is the mitigation for
    cumulative stratum leakage (Opus, 2026-09-04): revealed targets age out of
    relevance because the criteria that produced them are no longer in force.
    Rotation changes THESE knobs (and/or the source configuration) — the hard
    filters' fail-closed semantics never rotate.

    Owner DORMANCY is deliberately NOT a knob here (demotion reaffirmed by
    Opus 05/09: a mid-hunt transfer touches nothing mechanical, and escrow
    custody is already caught by the owner-is-EOA filter). Its agreed use is
    a TIE-BREAK at selection time: when the pool is abundant, prefer targets
    whose owner wallet is dormant; when the pool is tight, accept active
    owners without drama — a preference that never costs pool. Wire it in
    the snapshot refresh (owner recorded per entry) once owner-activity is
    instrumented; never as a qualify/reject filter."""

    epoch_id: str
    min_age_days: int = 180
    min_words: int = 2
    # Gate freshness (Opus review, 05/09): the mutation-void window is only
    # small because the cadence is weekly — a snapshot older than this makes
    # the stratum gate refuse GREEN (AMBER: refresh before launching).
    max_snapshot_age_days: int = 14


class CandidateSource(Protocol):
    """Yields (chain, contract, token_id) triples from the curated stratum.
    The chain is PER CANDIDATE (Opus review, 05/09): the pool is multi-chain
    — 'the prize is always on Base, the treasure can be anywhere onchain' —
    so a constant chain would seal wrong commitments and refuse legitimate
    winners.

    Contract obligations, both load-bearing:
      · RANDOM ORDER — the stream must already be uniformly shuffled, because
        the selector takes the FIRST candidate that passes every filter and
        that is only a uniform draw over qualifiers if the stream is
        exchangeable
      · AGE — only tokens minted >= the epoch's min_age_days ago"""

    def candidates(self, epoch: CurationEpoch) -> Iterator[tuple[str, str, int]]: ...


class SelectionRefused(RuntimeError):
    """Raised when no candidate passes within the attempt budget. Fail-closed:
    the hunt does NOT launch on a weak or unverifiable stratum."""


# --------------------------------------------------------------------------- #
# Selector                                                                     #
# --------------------------------------------------------------------------- #


class TargetSelector:
    """Draws one target from the curated stratum, applying the hard filters
    with production (fail-closed) semantics.

    All effectful collaborators are injected, and every one takes the CHAIN
    first — a multi-chain pool means the adapter must know which chain's RPC
    or marketplace view to consult (Opus review, 05/09):
      fetch_metadata(chain, contract, token_id) -> dict | None
      owner_is_eoa(chain, contract, token_id)   -> bool | None
      name_is_unique(base_name, chain, contract, token_id) -> bool | None
    """

    def __init__(
        self,
        *,
        source: CandidateSource,
        fetch_metadata: Callable[[str, str, int], dict | None],
        owner_is_eoa: Callable[[str, str, int], bool | None],
        name_is_unique: Callable[[str, str, str, int], bool | None],
        max_attempts: int = 400,
    ):
        self._source = source
        self._fetch_metadata = fetch_metadata
        self._owner_is_eoa = owner_is_eoa
        self._name_is_unique = name_is_unique
        self._max_attempts = max_attempts

    def select(self, epoch: CurationEpoch) -> Target:
        """First qualifying candidate off the (already shuffled) stream —
        a uniform draw over qualifiers, per the CandidateSource contract.
        Raises SelectionRefused when the budget runs out: a stratum that
        cannot produce a qualifier inside `max_attempts` random draws is not
        a stratum we launch on."""
        seen = 0
        for chain, contract, token_id in self._source.candidates(epoch):
            seen += 1
            if seen > self._max_attempts:
                break
            target = self._qualify(chain, contract, token_id, epoch)
            if target is not None:
                return target
        raise SelectionRefused(
            f"no qualifying target in {min(seen, self._max_attempts)} candidates "
            f"(epoch {epoch.epoch_id!r}) — selection refused, fail-closed. "
            "The stratum is too weak or its surfaces are unreachable; do not "
            "launch, re-measure the universe."
        )

    # -- filters, in cost order (cheapest first) ---------------------------- #

    def _qualify(self, chain: str, contract: str, token_id: int,
                 epoch: CurationEpoch) -> Target | None:
        meta = self._fetch_metadata(chain, contract, token_id)
        if not (isinstance(meta, dict) and meta.get("image")):
            return None
        name_onchain = str(meta.get("name") or "").strip()
        base = normalize_name(name_onchain)
        if not name_qualifies(base, min_words=epoch.min_words):
            return None
        # Fail-closed: None (indeterminate) rejects, unlike the measurement
        # scripts, which pass-with-warning. Production never launches on hope.
        if self._owner_is_eoa(chain, contract, token_id) is not True:
            return None
        if self._name_is_unique(base, chain, contract, token_id) is not True:
            return None
        return Target(
            chain=chain,
            contract=contract.lower(),
            token_id=token_id,
            name=base,
            name_onchain=name_onchain,
            description=str(meta.get("description") or "")[:600],
            image=str(meta.get("image") or ""),
            metadata_sha256=metadata_hash(meta),
            epoch=epoch.epoch_id,
        )


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class FakeSource:
    """A CandidateSource over a fixed, pre-shuffled list (tests). Accepts
    (chain, contract, token_id) triples, or legacy (contract, token_id)
    pairs which default to 'base'."""

    def __init__(self, pairs: Iterable[tuple]):
        self._triples = [
            p if len(p) == 3 else ("base", p[0], p[1]) for p in pairs
        ]

    def candidates(self, epoch: CurationEpoch) -> Iterator[tuple[str, str, int]]:
        return iter(self._triples)

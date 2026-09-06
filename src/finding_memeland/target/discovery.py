"""Era discovery — fills the reserved ContractRegistry by scanning the
2021 1/1 era, block by block, accumulating across runs.

This is the production form of the family-map instrument (04-05/09): random
era blocks -> ERC-721 mints -> bytecode fingerprint per contract -> classify
into the reserved strata. Incremental by design: every run scans blocks not
yet scanned and deepens the same accumulated state, so weekly refreshes keep
growing coverage of the ~3.5M-block era instead of resampling it.

SECRECY: the discovery STATE carries contract addresses and mint counts —
it is the registry's raw material and inherits the registry's rules (Opus,
05/09): encrypted at rest, no addresses in any repr/log/error, never in a
public document, red-teamer reply or Telegram print.

Classification (rationale measured 05/09, familias_eth):
  · manifold2021 — runtime EXACTLY 2,141 bytes (the measured artist-proxy
    family: 13 contracts, distinct names across contracts) or any family
    hash the epoch config pins
  · tail2021 — low-activity contracts (few mints observed relative to scan
    depth) whose bytecode FAMILY is small (a family with many contracts and
    few mints each is a platform of individuals; a single contract with
    many mints is a collection and is excluded — collections die at the
    uniqueness filter anyway, no point carrying them)
Thresholds are constants with the measurement they came from; tuning them is
epoch configuration, never silent code drift.

R1 (Opus re-review, 05/09): a scan is OF ONE CHAIN, named at construction
(keyword, no default), and every contract record carries that chain into
the registry as a (chain, contract) entry — the registry is never told
afterwards which chain it is on. The R2 backstop for this instrument is the
stratum gate: a blind fetch_mints yields an empty registry, and an empty
stratum counts zero — the gate fails loud, the pool never quietly grows.

Effectful collaborators injected (fetch_mints, get_code, clock); logic
tests offline.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Callable

from .sources import ContractRegistry

ERA_LO, ERA_HI = 11_500_000, 15_000_000     # ETH 1/1 era (Jan/21–Jun/22)

MANIFOLD_RUNTIME_LEN = 2_141                # measured 05/09 (13 contracts)
# a contract qualifies as tail while its observed mints stay under this
# fraction of scanned blocks (heuristic: artist contracts minted rarely)
TAIL_MINTS_PER_150_BLOCKS = 1
# a bytecode family whose AVERAGE mints/contract exceeds this is a
# collection engine, not an artist platform — excluded from tail
FAMILY_COLLECTION_AVG_MINTS = 8


@dataclass
class DiscoveryState:
    """Accumulated scan state. SENSITIVE — see module docstring."""

    scanned: set[int] = field(default_factory=set)
    # contract -> {"mints": int, "fam": str, "len": int, "chain": str}
    contracts: dict[str, dict] = field(default_factory=dict)

    def __repr__(self) -> str:  # counts only, never addresses
        return (f"DiscoveryState(blocks={len(self.scanned)}, "
                f"contracts={len(self.contracts)})")

    __str__ = __repr__


class DiscoveryIntegrityError(RuntimeError):
    """Discovery state unreadable/corrupted. The message NEVER carries
    contracts — this store holds the registry's raw material."""


class DiscoveryStateStore:
    """Encrypted persistence (PoolCipher port + injected read/write).
    Fail-closed on load like RegistryStore/SnapshotStore (Opus review,
    05/09): a state that cannot be trusted is a state we rescan, not one we
    silently replace with 'empty' or crash over with contents in the trace."""

    def __init__(self, *, cipher, read: Callable[[], str | None],
                 write: Callable[[str], None]):
        self._cipher = cipher
        self._read = read
        self._write = write

    def save(self, st: DiscoveryState) -> None:
        payload = json.dumps({
            "v": 1,
            "scanned": sorted(st.scanned),
            "contracts": st.contracts,
        }, ensure_ascii=False)
        self._write(self._cipher.encrypt(payload))

    def load(self) -> DiscoveryState:
        blob = self._read()
        if blob is None:
            return DiscoveryState()
        try:
            doc = json.loads(self._cipher.decrypt(blob))
            contracts = doc["contracts"]
            for rec in contracts.values():        # R1: chainless = rescan
                if not rec.get("chain"):
                    raise ValueError("discovery record without chain")
            return DiscoveryState(scanned=set(doc["scanned"]),
                                  contracts=contracts)
        except Exception as e:  # noqa: BLE001 — fail closed, no contents
            raise DiscoveryIntegrityError(
                f"discovery state unreadable ({type(e).__name__}) — wrong "
                "key or corrupted store; rescan the era") from e


@dataclass(frozen=True)
class ScanOutcome:
    """One scan run's outcome. `failed` counts blocks ATTEMPTED but not
    committed because a transport call failed mid-block — they stay
    unscanned and a later run retries them. Surfacing this number is part
    of the fix (Opus review, 05/09): an RPC blip must show up in the
    operator report, never erase contracts in silence."""

    scanned: int
    failed: int
    canary_ok: bool = True        # False ⇒ the run refused to scan (R2)
    zero_mint_blocks: int = 0     # second signal: low in the 2021 era


@dataclass(frozen=True)
class DiscoveryReport:
    """Operator-facing numbers. Counts only — Telegram-safe."""

    blocks_scanned: int
    blocks_total: int
    new_contracts: int
    manifold_total: int
    tail_total: int
    excluded_collections: int

    def render(self) -> str:
        return (f"discovery: {self.blocks_scanned} novos blocos "
                f"({self.blocks_total} acumulados) | contratos novos "
                f"{self.new_contracts} | manifold2021: {self.manifold_total} "
                f"| tail2021: {self.tail_total} | colecções excluídas: "
                f"{self.excluded_collections}")


class EraDiscovery:
    """Scan + classify. `fetch_mints(block) -> [(contract, token_id)]`;
    `get_code(contract) -> bytes` (empty for EOA/none). Both raise on
    transport failure — a failed block is simply not marked scanned.

    R2 CANARY (Opus re-review, 06/09): the stratum gate catches an EMPTY
    registry, not a DEGRADED one. A fetch_mints that returns [] for every
    block — non-archive node for the 2021 era, wrong log filter, wrong
    chain — marks blocks as scanned with zero contracts, the registry stops
    growing, and the report shows 'blocos acumulados' rising happily: an
    empty mint list is indistinguishable from 'we scanned and there were
    none'. So every run starts by fetching `canary_block` — a PINNED era
    block with KNOWN mints (epoch configuration, measured, never guessed)
    — and refuses to scan unless it returns EXACTLY `canary_mints` mints,
    the count measured when the block was pinned. Equality, not >= 1
    (Opus, 06/09): a provider that caps logs per request and returns a
    partial page in silence passes a >= 1 canary with room to spare — the
    block has mints, just fewer — and the zero-mint signal never fires
    either, because blocks come back incomplete, not empty. The registry
    grows slowly and every number looks healthy: absence, partial instead
    of total. Exact equality covers truncation, a partial filter and a
    change in the provider's response shape, at the same cost — one call.
    Second signal, reported: the count of zero-mint blocks in the run."""

    def __init__(self, *, chain: str, canary_block: int, canary_mints: int,
                 fetch_mints, get_code,
                 era: tuple[int, int] = (ERA_LO, ERA_HI),
                 pinned_manifold_hashes: frozenset[str] = frozenset()):
        if not chain:
            raise ValueError("EraDiscovery needs the chain it scans (R1)")
        if not isinstance(canary_block, int) or canary_block <= 0:
            raise ValueError("EraDiscovery needs a pinned canary block with "
                             "known mints (R2)")
        if not isinstance(canary_mints, int) or canary_mints <= 0:
            raise ValueError("EraDiscovery needs the MEASURED mint count of "
                             "the canary block (R2: equality, not >= 1)")
        self._chain = chain
        self._canary = canary_block
        self._canary_mints = canary_mints
        self._fetch = fetch_mints
        self._code = get_code
        self._era = era
        self._pinned = pinned_manifold_hashes

    def _canary_passes(self) -> bool:
        try:
            return len(self._fetch(self._canary)) == self._canary_mints
        except Exception:  # noqa: BLE001 — transport counts as blind
            return False

    def scan(self, state: DiscoveryState, n_blocks: int,
             rng: random.Random | None = None) -> ScanOutcome:
        """Scan n random era blocks not yet in state.

        A block commits ATOMICALLY (Opus review, 05/09): fetch the mints,
        resolve EVERY unseen contract's code, and only then write mint
        counts and mark the block scanned. If any call fails the block is
        dropped whole — no partial writes, nothing marked scanned — so a
        later run retries it and an RPC blip can never erase a contract
        from the registry's raw material in silence."""
        rng = rng or random.SystemRandom()
        if not self._canary_passes():
            return ScanOutcome(scanned=0, failed=0, canary_ok=False)
        done = 0
        failed = 0
        zero = 0
        attempts = 0
        while done < n_blocks and attempts < n_blocks * 4:
            attempts += 1
            b = rng.randint(*self._era)
            if b in state.scanned:
                continue
            try:
                mints = self._fetch(b)
            except Exception:  # noqa: BLE001 — bloco fica por varrer
                failed += 1
                continue
            # resolve every unseen contract BEFORE touching state
            new_recs: dict[str, dict] = {}
            ok = True
            for contract, _tid in mints:
                c = contract.lower()
                if c in state.contracts or c in new_recs:
                    continue
                try:
                    code = self._code(c)
                except Exception:  # noqa: BLE001 — transporte: bloco cai todo
                    ok = False
                    break
                new_recs[c] = {"mints": 0,
                               "fam": hashlib.sha256(code).hexdigest()[:16],
                               "len": len(code),
                               "chain": self._chain}
            if not ok:
                failed += 1
                continue
            state.contracts.update(new_recs)
            for contract, _tid in mints:
                state.contracts[contract.lower()]["mints"] += 1
            state.scanned.add(b)
            done += 1
            if not mints:
                zero += 1
        return ScanOutcome(scanned=done, failed=failed, canary_ok=True,
                           zero_mint_blocks=zero)

    def classify_into(self, state: DiscoveryState,
                      registry: ContractRegistry) -> DiscoveryReport:
        """Apply the epoch-1 rules over the ACCUMULATED state and sync the
        registry. Idempotent: re-running reclassifies from scratch counts;
        the registry dedupes."""
        fam_stats: dict[str, dict] = {}
        for c, rec in state.contracts.items():
            fs = fam_stats.setdefault(rec["fam"], {"contracts": 0, "mints": 0})
            fs["contracts"] += 1
            fs["mints"] += rec["mints"]

        tail_cap = max(1, (len(state.scanned) // 150)
                       * TAIL_MINTS_PER_150_BLOCKS + 1)
        # (chain, contract) per bucket — the chain is the RECORD's (R1),
        # so a state merged from several scans classifies each on its own
        manifold: dict[str, list[str]] = {}
        tail: dict[str, list[str]] = {}
        excluded = 0
        for c, rec in state.contracts.items():
            chain = rec["chain"]
            if (rec["len"] == MANIFOLD_RUNTIME_LEN
                    or rec["fam"] in self._pinned):
                manifold.setdefault(chain, []).append(c)
                continue
            fs = fam_stats[rec["fam"]]
            avg = fs["mints"] / fs["contracts"]
            if rec["mints"] <= tail_cap and avg <= FAMILY_COLLECTION_AVG_MINTS:
                tail.setdefault(chain, []).append(c)
            else:
                excluded += 1

        new = 0
        for chain, cs in manifold.items():
            new += registry.add("manifold2021", cs, chain=chain)
        for chain, cs in tail.items():
            new += registry.add("tail2021", cs, chain=chain)
        counts = registry.counts()
        return DiscoveryReport(
            blocks_scanned=0,           # o caller preenche por run se quiser
            blocks_total=len(state.scanned),
            new_contracts=new,
            manifold_total=counts.get("manifold2021", 0),
            tail_total=counts.get("tail2021", 0),
            excluded_collections=excluded,
        )

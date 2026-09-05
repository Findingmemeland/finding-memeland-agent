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
    # contract -> {"mints": int, "fam": str, "len": int}
    contracts: dict[str, dict] = field(default_factory=dict)

    def __repr__(self) -> str:  # counts only, never addresses
        return (f"DiscoveryState(blocks={len(self.scanned)}, "
                f"contracts={len(self.contracts)})")

    __str__ = __repr__


class DiscoveryStateStore:
    """Encrypted persistence (PoolCipher port + injected read/write)."""

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
        doc = json.loads(self._cipher.decrypt(blob))
        return DiscoveryState(scanned=set(doc["scanned"]),
                              contracts=doc["contracts"])


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
    transport failure — a failed block is simply not marked scanned."""

    def __init__(self, *, fetch_mints, get_code,
                 era: tuple[int, int] = (ERA_LO, ERA_HI),
                 pinned_manifold_hashes: frozenset[str] = frozenset()):
        self._fetch = fetch_mints
        self._code = get_code
        self._era = era
        self._pinned = pinned_manifold_hashes

    def scan(self, state: DiscoveryState, n_blocks: int,
             rng: random.Random | None = None) -> int:
        """Scan n random era blocks not yet in state. Returns blocks scanned
        this run. Failures skip silently per block (the block stays
        unscanned and a later run retries it)."""
        rng = rng or random.SystemRandom()
        done = 0
        attempts = 0
        while done < n_blocks and attempts < n_blocks * 4:
            attempts += 1
            b = rng.randint(*self._era)
            if b in state.scanned:
                continue
            try:
                mints = self._fetch(b)
            except Exception:  # noqa: BLE001 — bloco fica por varrer
                continue
            state.scanned.add(b)
            done += 1
            for contract, _tid in mints:
                c = contract.lower()
                rec = state.contracts.get(c)
                if rec is None:
                    try:
                        code = self._code(c)
                    except Exception:  # noqa: BLE001
                        continue
                    rec = {"mints": 0,
                           "fam": hashlib.sha256(code).hexdigest()[:16],
                           "len": len(code)}
                    state.contracts[c] = rec
                rec["mints"] += 1
        return done

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
        manifold, tail, excluded = [], [], 0
        for c, rec in state.contracts.items():
            if (rec["len"] == MANIFOLD_RUNTIME_LEN
                    or rec["fam"] in self._pinned):
                manifold.append(c)
                continue
            fs = fam_stats[rec["fam"]]
            avg = fs["mints"] / fs["contracts"]
            if rec["mints"] <= tail_cap and avg <= FAMILY_COLLECTION_AVG_MINTS:
                tail.append(c)
            else:
                excluded += 1

        new = registry.add("manifold2021", manifold)
        new += registry.add("tail2021", tail)
        counts = registry.counts()
        return DiscoveryReport(
            blocks_scanned=0,           # o caller preenche por run se quiser
            blocks_total=len(state.scanned),
            new_contracts=new,
            manifold_total=counts.get("manifold2021", 0),
            tail_total=counts.get("tail2021", 0),
            excluded_collections=excluded,
        )

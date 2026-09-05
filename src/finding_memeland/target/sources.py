"""Epoch-1 sources — where the refresh pulls candidates from.

Composition ratified 05/09 (Opus): Foundation + SuperRare + MakersPlace
(classic, endpoint-enumerable platforms) + the Manifold-2021 family + the
filtered tail (registry strata, enumerable only by US).

THE CONTRACT REGISTRY IS THE PROJECT'S MOST SENSITIVE ARTIFACT (Opus,
05/09). The tail's cap exemption lives entirely on non-enumerability — and
this registry IS the enumeration that doesn't exist elsewhere. Therefore:
  · stored encrypted with the same cipher port as the snapshot/relic pool
  · ContractRegistry's repr/str never show contracts, and no code path here
    formats contract addresses into log or error strings
  · it goes in NO public document, NO red-teamer reply, NO Telegram print
  · if it ever leaks, the tail's cap exemption falls THE SAME MINUTE and
    GATE_MAX_STRATUM_SHARE applies to the tail again (epoch config change)

Classic platforms enumerate by chain (totalSupply/tokenByIndex, falling back
to dense-id probing) — no marketplace quota on the listing path. Registry
strata enumerate contract by contract the same way. Everything effectful is
injected; the logic tests offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

from .refresh import PlatformItem

SEL_TOTAL = "0x18160ddd"        # totalSupply()
SEL_TOKENBYINDEX = "0x4f6ccce7"  # tokenByIndex(uint256)
SEL_OWNEROF = "0x6352211e"      # ownerOf(uint256)
SEL_TOKENURI = "0xc87b56dd"     # tokenURI(uint256)

# Classic epoch-1 platforms — addresses verified 04-05/09 (Etherscan /
# own census). These are PUBLIC knowledge; the registry strata are not.
EPOCH1_CLASSIC = (
    ("foundation",  "ethereum", "0x3b3ee1931dc30c1957379fac9aba94d1c48a5405"),
    ("superrare2",  "ethereum", "0xb932a70a57673d89f4acffbe830e8ed7f75fb9e0"),
    ("superrare1",  "ethereum", "0x41a322b28d0ff354040e2cbc676f0320d8c8850d"),
    ("makersplace", "ethereum", "0x2963ba471e265e5f51cafafca78310fe87f8e6d1"),
)
EPOCH1_REGISTRY_STRATA = ("manifold2021", "tail2021")
EPOCH1_CAP_EXEMPT = frozenset({"tail2021"})   # ratified 05/09; falls on leak


# --------------------------------------------------------------------------- #
# The reserved registry                                                        #
# --------------------------------------------------------------------------- #


class ChainUnavailable(RuntimeError):
    """Transport failure (RPC down, timeout) — NOT a revert. The production
    eth_call adapter raises THIS for network trouble and any other
    exception for reverts. Listers let it propagate so a broken platform
    fails the refresh loudly (previous snapshot keeps serving) instead of
    being swallowed as 'zero tokens' — the silently-smaller-pool failure
    the refresh exists to refuse."""


class RegistryIntegrityError(RuntimeError):
    """Registry unreadable/corrupted. The message NEVER carries contracts."""


@dataclass
class ContractRegistry:
    """stratum -> contract addresses, discovered by our own era scans.

    Reserved artifact: encrypted at rest, opaque in logs. Access the
    contents only through `contracts(stratum)`; anything that formats this
    object gets counts, not addresses."""

    _strata: dict[str, list[str]] = field(default_factory=dict)

    def add(self, stratum: str, contracts: Iterable[str]) -> int:
        bucket = self._strata.setdefault(stratum, [])
        known = set(bucket)
        added = 0
        for c in contracts:
            c = c.lower()
            if c not in known:
                bucket.append(c)
                known.add(c)
                added += 1
        return added

    def contracts(self, stratum: str) -> tuple[str, ...]:
        return tuple(self._strata.get(stratum, ()))

    def counts(self) -> dict[str, int]:
        return {s: len(cs) for s, cs in self._strata.items()}

    def __repr__(self) -> str:  # never the addresses
        inner = ", ".join(f"{s}: {n}" for s, n in sorted(self.counts().items()))
        return f"ContractRegistry({inner or 'empty'})"

    __str__ = __repr__


class RegistryStore:
    """Encrypted persistence, same shape as SnapshotStore: PoolCipher port +
    injected read/write callables."""

    def __init__(self, *, cipher, read: Callable[[], str | None],
                 write: Callable[[str], None]):
        self._cipher = cipher
        self._read = read
        self._write = write

    def save(self, reg: ContractRegistry) -> None:
        import json
        payload = json.dumps({"v": 1, "strata": reg._strata},  # noqa: SLF001
                             ensure_ascii=False)
        self._write(self._cipher.encrypt(payload))

    def load(self) -> ContractRegistry | None:
        import json
        blob = self._read()
        if blob is None:
            return None
        try:
            doc = json.loads(self._cipher.decrypt(blob))
            reg = ContractRegistry()
            for s, cs in doc["strata"].items():
                reg.add(s, cs)
            return reg
        except Exception as e:  # noqa: BLE001 — fail closed, no contents
            raise RegistryIntegrityError(
                f"registry unreadable ({type(e).__name__}) — wrong key or "
                "corrupted store; rebuild from era scans") from e


# --------------------------------------------------------------------------- #
# Chain enumeration (no marketplace quota on the listing path)                 #
# --------------------------------------------------------------------------- #


class ChainContractLister:
    """PlatformLister over ONE contract via the chain.

    Strategy: totalSupply + tokenByIndex when the contract enumerates;
    otherwise dense-id probing from 1 with a miss budget (artist/tail
    contracts are small and dense-ish; a few burns are tolerated by the
    probe window). `eth_call(to, data) -> hex str` is injected; it must
    RAISE on revert."""

    def __init__(self, *, eth_call, platform: str, contract: str,
                 max_tokens: int = 200_000, probe_miss_budget: int = 25):
        self.name = platform
        self._call = eth_call
        self._contract = contract
        self._max = max_tokens
        self._miss_budget = probe_miss_budget

    def items(self) -> Iterator[PlatformItem]:
        total = self._try_total_supply()
        if total is not None:
            yield from self._by_index(min(total, self._max))
        else:
            yield from self._by_probe()

    def _try_total_supply(self) -> int | None:
        try:
            return int(self._call(self._contract, SEL_TOTAL), 16)
        except ChainUnavailable:
            raise
        except Exception:  # noqa: BLE001 — revert = sem enumeração
            return None

    def _exists(self, tid: int) -> bool:
        try:
            data = self._call(self._contract,
                              SEL_OWNEROF + tid.to_bytes(32, "big").hex())
            return bool(data and data != "0x")
        except ChainUnavailable:
            raise
        except Exception:  # noqa: BLE001 — revert = token não existe
            return False

    def _by_index(self, total: int) -> Iterator[PlatformItem]:
        for idx in range(total):
            try:
                tid = int(self._call(
                    self._contract,
                    SEL_TOKENBYINDEX + idx.to_bytes(32, "big").hex()), 16)
            except ChainUnavailable:
                raise
            except Exception:  # noqa: BLE001 — sem enumeração afinal
                yield from self._by_probe()
                return
            yield PlatformItem(platform=self.name, contract=self._contract,
                               token_id=tid, name="")
        # name="" de propósito: o nome CANÓNICO vem do resolvedor de
        # metadata do refresh (chain+gateway), nunca da listagem

    def _by_probe(self) -> Iterator[PlatformItem]:
        misses = 0
        tid = 0
        while tid < self._max and misses <= self._miss_budget:
            tid += 1
            if self._exists(tid):
                misses = 0
                yield PlatformItem(platform=self.name, contract=self._contract,
                                   token_id=tid, name="")
            else:
                misses += 1


class RegistryStratumLister:
    """PlatformLister over a whole registry stratum: chains the per-contract
    listers, tagging every item with the STRATUM slug (what the snapshot and
    the stratum gate count by). Contract addresses never appear in `name`
    or any log-facing field."""

    def __init__(self, *, eth_call, stratum: str, registry: ContractRegistry,
                 per_contract_cap: int = 2_000):
        self.name = stratum
        self._eth_call = eth_call
        self._registry = registry
        self._cap = per_contract_cap

    def items(self) -> Iterator[PlatformItem]:
        for contract in self._registry.contracts(self.name):
            lister = ChainContractLister(
                eth_call=self._eth_call, platform=self.name,
                contract=contract, max_tokens=self._cap)
            yield from lister.items()


def epoch1_listers(*, eth_call, registry: ContractRegistry) -> tuple:
    """The ratified epoch-1 composition, as refresh-ready listers."""
    classic = tuple(
        ChainContractLister(eth_call=eth_call, platform=slug, contract=addr)
        for slug, _chain, addr in EPOCH1_CLASSIC
    )
    reserved = tuple(
        RegistryStratumLister(eth_call=eth_call, stratum=s, registry=registry)
        for s in EPOCH1_REGISTRY_STRATA
    )
    return classic + reserved

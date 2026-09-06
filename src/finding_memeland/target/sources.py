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

TWO PROJECT RULES (Opus re-review, 05/09), applied package-wide:
  R1 · no component that talks to a chain has a default chain — keyword
       required, and the chain TRAVELS WITH THE ENTRY: registry entries are
       (chain, contract), listed items carry it, the snapshot stores it,
       Target.id() seals it. A ChainRpc bundles a chain with its adapters
       so the two can never drift apart.
  R2 · any guard whose approval is "found nothing" proves first that it can
       see what it looks for. A wrong-chain RPC answers eth_call with '0x'
       for every selector (no code at that address — not a revert), which
       an unguarded lister reads as "zero tokens" and an unguarded EOA
       check reads as "no code, owner is an EOA". So: before enumerating a
       contract the lister requires eth_getCode(contract) to be non-empty,
       and the EOA check requires the same before trusting '0x' on the
       owner. A blind RPC fails LOUD (RefreshFailed / unverifiable), never
       as a smaller pool.

Classic platforms enumerate by chain (totalSupply/tokenByIndex, falling back
to dense-id probing) — no marketplace quota on the listing path. Registry
strata enumerate contract by contract the same way. Everything effectful is
injected; the logic tests offline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

from .refresh import PlatformItem

SEL_TOTAL = "0x18160ddd"        # totalSupply()
SEL_TOKENBYINDEX = "0x4f6ccce7"  # tokenByIndex(uint256)
SEL_OWNEROF = "0x6352211e"      # ownerOf(uint256)
SEL_TOKENURI = "0xc87b56dd"     # tokenURI(uint256)

# Classic epoch-1 platforms — addresses verified 04-05/09 (Etherscan /
# own census). These are PUBLIC knowledge; the registry strata are not.
# (slug, chain, contract) — the chain column is DATA that rides each item.
EPOCH1_CLASSIC = (
    ("foundation",  "ethereum", "0x3b3ee1931dc30c1957379fac9aba94d1c48a5405"),
    ("superrare2",  "ethereum", "0xb932a70a57673d89f4acffbe830e8ed7f75fb9e0"),
    ("superrare1",  "ethereum", "0x41a322b28d0ff354040e2cbc676f0320d8c8850d"),
    ("makersplace", "ethereum", "0x2963ba471e265e5f51cafafca78310fe87f8e6d1"),
)
EPOCH1_REGISTRY_STRATA = ("manifold2021", "tail2021")
EPOCH1_CAP_EXEMPT = frozenset({"tail2021"})   # ratified 05/09; falls on leak


# --------------------------------------------------------------------------- #
# Chain adapters                                                               #
# --------------------------------------------------------------------------- #


class ChainUnavailable(RuntimeError):
    """Transport failure (RPC down, timeout) — NOT a revert. The production
    adapters raise THIS for network trouble and any other exception for
    reverts. Listers let it propagate so a broken platform fails the refresh
    loudly (previous snapshot keeps serving) instead of being swallowed as
    'zero tokens' — the silently-smaller-pool failure the refresh exists to
    refuse."""


class ContractInvisible(RuntimeError):
    """R2 canary failed: eth_getCode(contract) is empty on this chain's RPC.
    Wrong chain, wrong address or a blind endpoint — in every case the
    listing must not proceed to 'zero tokens'. Message carries the platform
    slug only, never the address."""


@dataclass(frozen=True)
class ChainRpc:
    """One chain's adapters, bound to the chain's NAME so the two cannot
    drift (R1). `eth_call(to, data) -> hex str` raises on revert and raises
    ChainUnavailable on transport trouble; `get_code(addr) -> hex str`
    ('0x' when no code) raises ChainUnavailable on transport trouble."""

    chain: str
    eth_call: Callable[[str, str], str]
    get_code: Callable[[str], str]

    def has_code(self, addr: str) -> bool:
        code = (self.get_code(addr) or "").strip().lower()
        return code not in ("", "0x")


def _rpc_for(rpcs: dict[str, ChainRpc], chain: str) -> ChainRpc:
    rpc = rpcs.get(chain)
    if rpc is None:
        raise KeyError(f"no ChainRpc configured for chain {chain!r}")
    if rpc.chain != chain:
        raise ValueError(f"ChainRpc bound to {rpc.chain!r} registered under "
                         f"{chain!r} — refusing (R1: chain and adapter must "
                         "agree)")
    return rpc


# --------------------------------------------------------------------------- #
# The reserved registry                                                        #
# --------------------------------------------------------------------------- #


class RegistryIntegrityError(RuntimeError):
    """Registry unreadable/corrupted. The message NEVER carries contracts."""


@dataclass
class ContractRegistry:
    """stratum -> (chain, contract) entries, discovered by our own era scans.

    Reserved artifact: encrypted at rest, opaque in logs. Access the
    contents only through `entries(stratum)`; anything that formats this
    object gets counts, not addresses. The chain is stored WITH each entry
    (R1) — a registry never needs to be told which chain it is on."""

    _strata: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def add(self, stratum: str, contracts: Iterable[str], *, chain: str) -> int:
        bucket = self._strata.setdefault(stratum, [])
        known = set(bucket)
        added = 0
        for c in contracts:
            entry = (chain, c.lower())
            if entry not in known:
                bucket.append(entry)
                known.add(entry)
                added += 1
        return added

    def entries(self, stratum: str) -> tuple[tuple[str, str], ...]:
        return tuple(self._strata.get(stratum, ()))

    def counts(self) -> dict[str, int]:
        return {s: len(cs) for s, cs in self._strata.items()}

    def __repr__(self) -> str:  # never the addresses
        inner = ", ".join(f"{s}: {n}" for s, n in sorted(self.counts().items()))
        return f"ContractRegistry({inner or 'empty'})"

    __str__ = __repr__


class RegistryStore:
    """Encrypted persistence, same shape as SnapshotStore: PoolCipher port +
    injected read/write callables. Payload v2 stores (chain, contract)
    pairs; a v1 payload (bare addresses, no chain) fails closed — rebuild
    from era scans rather than guess a chain."""

    def __init__(self, *, cipher, read: Callable[[], str | None],
                 write: Callable[[str], None]):
        self._cipher = cipher
        self._read = read
        self._write = write

    def save(self, reg: ContractRegistry) -> None:
        payload = json.dumps({
            "v": 2,
            "strata": {s: [[ch, c] for ch, c in cs]
                       for s, cs in reg._strata.items()},  # noqa: SLF001
        }, ensure_ascii=False)
        self._write(self._cipher.encrypt(payload))

    def load(self) -> ContractRegistry | None:
        blob = self._read()
        if blob is None:
            return None
        try:
            doc = json.loads(self._cipher.decrypt(blob))
            if doc.get("v") != 2:
                raise ValueError("registry payload is not v2 (chainless)")
            reg = ContractRegistry()
            for s, pairs in doc["strata"].items():
                for ch, c in pairs:
                    if not isinstance(ch, str) or not ch:
                        raise ValueError("registry entry without chain")
                    reg.add(s, [c], chain=ch)
            return reg
        except Exception as e:  # noqa: BLE001 — fail closed, no contents
            raise RegistryIntegrityError(
                f"registry unreadable ({type(e).__name__}) — wrong key, "
                "corrupted or chainless store; rebuild from era scans") from e


# --------------------------------------------------------------------------- #
# Chain enumeration (no marketplace quota on the listing path)                 #
# --------------------------------------------------------------------------- #


class ChainContractLister:
    """PlatformLister over ONE contract via its chain's RPC.

    Strategy: totalSupply + tokenByIndex when the contract enumerates;
    otherwise dense-id probing from 1 with a miss budget (artist/tail
    contracts are small and dense-ish; a few burns are tolerated by the
    probe window).

    R2 canary: `items()` first requires eth_getCode(contract) to be
    non-empty on this RPC. Without it a wrong-chain RPC answers '0x' to
    everything and the lister would yield zero tokens with a straight
    face."""

    def __init__(self, *, rpc: ChainRpc, platform: str, contract: str,
                 max_tokens: int = 200_000, probe_miss_budget: int = 25):
        self.name = platform
        self._rpc = rpc
        self._contract = contract
        self._max = max_tokens
        self._miss_budget = probe_miss_budget

    @property
    def chain(self) -> str:
        return self._rpc.chain

    def items(self) -> Iterator[PlatformItem]:
        if not self._rpc.has_code(self._contract):
            raise ContractInvisible(
                f"platform {self.name!r}: contract has no code on the "
                f"{self._rpc.chain!r} RPC — wrong chain/address or blind "
                "endpoint; refusing to list (R2)")
        total = self._try_total_supply()
        if total is not None:
            yield from self._by_index(min(total, self._max))
        else:
            yield from self._by_probe()

    def _call(self, data: str) -> str:
        return self._rpc.eth_call(self._contract, data)

    def _try_total_supply(self) -> int | None:
        try:
            return int(self._call(SEL_TOTAL), 16)
        except ChainUnavailable:
            raise
        except Exception:  # noqa: BLE001 — revert = sem enumeração
            return None

    def _exists(self, tid: int) -> bool:
        try:
            data = self._call(SEL_OWNEROF + tid.to_bytes(32, "big").hex())
            return bool(data and data != "0x")
        except ChainUnavailable:
            raise
        except Exception:  # noqa: BLE001 — revert = token não existe
            return False

    def _item(self, tid: int) -> PlatformItem:
        return PlatformItem(platform=self.name, chain=self._rpc.chain,
                            contract=self._contract, token_id=tid, name="")
        # name="" de propósito: o nome CANÓNICO vem do resolvedor de
        # metadata do refresh (chain+gateway), nunca da listagem

    def _by_index(self, total: int) -> Iterator[PlatformItem]:
        for idx in range(total):
            try:
                tid = int(self._call(
                    SEL_TOKENBYINDEX + idx.to_bytes(32, "big").hex()), 16)
            except ChainUnavailable:
                raise
            except Exception:  # noqa: BLE001 — sem enumeração afinal
                yield from self._by_probe()
                return
            yield self._item(tid)

    def _by_probe(self) -> Iterator[PlatformItem]:
        misses = 0
        tid = 0
        while tid < self._max and misses <= self._miss_budget:
            tid += 1
            if self._exists(tid):
                misses = 0
                yield self._item(tid)
            else:
                misses += 1


class RegistryStratumLister:
    """PlatformLister over a whole registry stratum: chains the per-contract
    listers, tagging every item with the STRATUM slug (what the snapshot and
    the stratum gate count by). Each entry brings its own chain (R1) and
    picks the matching ChainRpc; an entry on a chain with no RPC configured
    fails LOUD (KeyError → RefreshFailed), never as a skipped contract.
    Contract addresses never appear in `name` or any log-facing field."""

    def __init__(self, *, rpcs: dict[str, ChainRpc], stratum: str,
                 registry: ContractRegistry, per_contract_cap: int = 2_000):
        self.name = stratum
        self._rpcs = rpcs
        self._registry = registry
        self._cap = per_contract_cap

    def items(self) -> Iterator[PlatformItem]:
        for chain, contract in self._registry.entries(self.name):
            lister = ChainContractLister(
                rpc=_rpc_for(self._rpcs, chain), platform=self.name,
                contract=contract, max_tokens=self._cap)
            yield from lister.items()


def epoch1_listers(*, rpcs: dict[str, ChainRpc],
                   registry: ContractRegistry) -> tuple:
    """The ratified epoch-1 composition, as refresh-ready listers. `rpcs`
    maps chain -> ChainRpc; every chain the composition touches must be
    present (KeyError otherwise — a missing chain is a configuration error,
    never a silently absent platform)."""
    classic = tuple(
        ChainContractLister(rpc=_rpc_for(rpcs, chain), platform=slug,
                            contract=addr)
        for slug, chain, addr in EPOCH1_CLASSIC
    )
    reserved = tuple(
        RegistryStratumLister(rpcs=rpcs, stratum=s, registry=registry)
        for s in EPOCH1_REGISTRY_STRATA
    )
    return classic + reserved


# --------------------------------------------------------------------------- #
# Owner-is-EOA — with the R2 canary                                            #
# --------------------------------------------------------------------------- #


class ChainEoaCheck:
    """owner_is_eoa(chain, contract, token_id) -> bool | None, the shape the
    selector and the refresh inject.

    Approval here is "the owner address has no code" — an R2 guard. So
    before trusting '0x' on the owner it requires the NFT contract itself
    to have code on the same RPC: if the contract is invisible, so is the
    owner, and '0x' means nothing. None (unverifiable) in that case, on a
    missing RPC for the chain, on an ownerOf revert (burned/unknown), or on
    transport trouble — the callers fail closed on None."""

    def __init__(self, *, rpcs: dict[str, ChainRpc]):
        self._rpcs = rpcs

    def __call__(self, chain: str, contract: str, token_id: int) -> bool | None:
        rpc = self._rpcs.get(chain)
        if rpc is None or rpc.chain != chain:
            return None
        try:
            if not rpc.has_code(contract):
                return None                       # canary: RPC blind to it
            data = rpc.eth_call(
                contract, SEL_OWNEROF + token_id.to_bytes(32, "big").hex())
            owner = _address_from_word(data)
            if owner is None:
                return None
            return not rpc.has_code(owner)
        except Exception:  # noqa: BLE001 — revert or transport: unverifiable
            return None


def _address_from_word(data: str | None) -> str | None:
    if not data or not data.startswith("0x") or len(data) < 66:
        return None
    word = data[2:66]
    addr = "0x" + word[-40:]
    return None if int(addr, 16) == 0 else addr

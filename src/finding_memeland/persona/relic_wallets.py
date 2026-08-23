"""Relic wallet pool — the CEX-funded, mutually non-linkable wallets that mint
relics (one FRESH wallet per relic, Pedro 2026-08-22: never reuse — reuse would
link two relics on-chain via the shared minter).

Keys never live in this module or the DB. A wallet is a REFERENCE (e.g.
"RELIC_WALLET_0007"); the private key is resolved from Doppler at the moment of
signing, used for one mint, and never logged or returned beyond that call. This
mirrors the token_resolver pattern in persona/source.py and the injected-key
pattern in chain/payout.py.

Offline/testable: the directory (which refs exist / are free) and the key
resolver are injected, so tests run with fakes and zero secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class KeyResolver(Protocol):
    """ref -> (address, private_key). Real impl reads Doppler env by convention
    (e.g. {ref}_PK, {ref}_ADDR). The key is used immediately to sign and MUST NOT
    be stored or returned anywhere else."""

    def resolve(self, wallet_ref: str) -> tuple[str, str]: ...


@runtime_checkable
class WalletDirectory(Protocol):
    """Which wallet refs exist, and which are already spoken for by a relic. The
    real impl derives `used` from the relics table (distinct mint_wallet_ref, plus
    any reserved-but-not-yet-recorded); tests inject a fake."""

    def all_refs(self) -> list[str]: ...
    def used_refs(self) -> set[str]: ...


@dataclass(frozen=True)
class WalletHandle:
    """A picked wallet — ref + public address only. NEVER carries the key."""

    ref: str
    address: str


class WalletPool:
    def __init__(self, directory: WalletDirectory, key_resolver: KeyResolver):
        self._dir = directory
        self._resolve = key_resolver

    def free_refs(self) -> list[str]:
        used = self._dir.used_refs()
        return [r for r in self._dir.all_refs() if r not in used]

    def pick_free(self) -> WalletHandle:
        """A wallet not yet used by any relic (fresh, non-linkable). Resolves once
        to validate the ref and read the public address; the key is discarded
        here — signing happens later via signing_key(). Raises when exhausted so
        the operator knows to fund more wallets."""
        free = self.free_refs()
        if not free:
            raise RuntimeError(
                "relic wallet pool exhausted — every wallet already minted a "
                "relic. Fund more CEX wallets (one relic per wallet, no reuse)."
            )
        ref = free[0]
        address, _key = self._resolve.resolve(ref)  # _key intentionally dropped
        return WalletHandle(ref=ref, address=address)

    def signing_key(self, wallet_ref: str) -> tuple[str, str]:
        """(address, private_key) to sign ONE mint. The caller uses it inline and
        must not persist it. Kept separate from pick_free so the key is fetched
        only at the instant of signing."""
        return self._resolve.resolve(wallet_ref)


class FakeKeyResolver:
    """Deterministic fake — never a real key."""

    def resolve(self, wallet_ref: str) -> tuple[str, str]:
        return (f"0xADDR_{wallet_ref}", f"0xKEY_{wallet_ref}")


class FakeWalletDirectory:
    def __init__(self, refs, used=()):
        self._refs = list(refs)
        self._used = set(used)

    def all_refs(self):
        return list(self._refs)

    def used_refs(self):
        return set(self._used)

    def mark_used(self, ref):  # test helper
        self._used.add(ref)

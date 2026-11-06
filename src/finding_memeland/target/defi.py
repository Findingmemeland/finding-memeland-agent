"""DeFi POSITIONS ARE NOT TREASURES (Pedro, after hunt #34).

Hunt #34's treasure was a Uniswap V3 liquidity position — "Uniswap - 0.05% -
EURT/WETH - 2319.3<>2359.1" — and players complained, fairly: it is a
financial receipt, not a collectible. Nothing stopped it. Its name has two
words with letters ("Uniswap", "EURT/WETH"), its metadata and image are
on-chain (data: URIs, accepted like IPFS), a position is unique by
construction, and a random block's ERC-721 mints are full of them — every LP
position minted is one.

What counts (Pedro): liquidity positions (Uniswap V3/V4, PancakeSwap V3,
Aerodrome/Velodrome Slipstream, Sushi…), locks / veNFTs (veAERO, veVELO…),
and streams / vesting (Sablier, Hedgey…).

FOUR SIGNALS, any one is enough:
  · the contract is a KNOWN position manager / lock / stream contract —
    every address below was confirmed on-chain on 06/11 by its own name()
    and symbol() (public RPC), never written from memory;
  · the COLLECTION's name / symbol (name() and symbol() of the contract,
    over RPC) — "… Positions NFT", "UNI-V3-POS", "veNFT", "Sablier …";
  · the TOKEN's name has the position shape — "Uniswap - 0.3% - A/B -
    x<>y", "CL - A/B - x<>y" (both measured on 06/11);
  · the metadata's description says what it is — "This NFT represents a
    liquidity position / payment stream / stream…" (measured on 06/11).

Where it runs: the deposit (every way into the larder: /harvest, /fill, the
carried candidates) and /prepare (never drawn, even if already in the
larder — and kept there until the operator confirms a clean-up).
"""
from __future__ import annotations

import re
from collections.abc import Callable

# Confirmed on-chain on 06/11 (public RPC, no key): each address answered
# name() / symbol() with the protocol's own — see the comment on each line.
DEFI_CONTRACTS: dict[str, dict[str, str]] = {
    "ethereum": {
        # Uniswap V3 Positions NFT-V1 / UNI-V3-POS — hunt #34's contract
        "0xc36442b4a4522e871399cd717abdd847ab11fe88": "uniswap-v3",
        # Uniswap v4 Positions NFT / UNI-V4-POSM
        "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e": "uniswap-v4",
        # Pancake V3 Positions NFT-V1 / PCS-V3-POS
        "0x46a15b0b27311cedf172ab29e4f4766fbe7f4364": "pancakeswap-v3",
        # SushiSwap V3 Positions NFT-V1 / SUSHI-V3-POS
        "0x2214a42d8e2a1d20635c2cb0664422c528b6a432": "sushiswap-v3",
        # Sablier V2 Lockup Linear / Dynamic / Tranched NFT, Sablier Lockup NFT
        "0xb10daee1fcf62243ae27776d7a92d39dc8740f95": "sablier",
        "0x39efdc3dbb57b2388ccc4bb40ac4cb1226bc9e44": "sablier",
        "0xafb979d9afad1ad27c5eff4e27226e3ab9e5dcc9": "sablier",
        "0x7cc7e125d83a581ff438608490cc0f7bdff79127": "sablier",
        "0x3962f6585946823440d274ad7c719b02b49de51e": "sablier",
        "0x9deabf7815b42bf4e9a03eec35a486ff74ee7459": "sablier",
        "0xf86b359035208e4529686a1825f2d5bee38c28a8": "sablier",
        "0x7c01aa3783577e15fd7e272443d44b92d5b21056": "sablier",
        # Hedgey: TokenVestingPlans, VotingTokenVestingPlans, TokenLockupPlans,
        # VotingTokenLockupPlans
        "0x2cde9919e81b20b4b33dd562a48a84b54c48f00c": "hedgey",
        "0x1bb64af7fe05fc69c740609267d2abe3e119ef82": "hedgey",
        "0x1961a23409ca59eedca6a99c97e4087dad752486": "hedgey",
        "0x73cd8626b3cd47b009e68380720cfe6679a3ec3d": "hedgey",
    },
    "base": {
        # Uniswap V3 Positions NFT-V1 / UNI-V3-POS
        "0x03a520b32c04bf3beef7beb72e919cf822ed34f1": "uniswap-v3",
        # Uniswap v4 Positions NFT / UNI-V4-POSM
        "0x7c5f5a4bbd8fd63184577525326123b519429bdc": "uniswap-v4",
        # Pancake V3 Positions NFT-V1 / PCS-V3-POS
        "0x46a15b0b27311cedf172ab29e4f4766fbe7f4364": "pancakeswap-v3",
        # SushiSwap V3 Positions NFT-V1 / SUSHI-V3-POS
        "0x80c7dd17b01855a6d2347444a0fcc36136a314de": "sushiswap-v3",
        # Slipstream Position NFT v1 / AERO-CL-POS (Aerodrome), two managers
        "0x827922686190790b37229fd06084350e74485b72": "aerodrome-cl",
        "0xa990c6a764b73bf43cee5bb40339c3322fb9d55f": "aerodrome-cl",
        # veNFT / veNFT (Aerodrome's vote-escrow)
        "0xebf418fe2512e7e6bd9b87a8f0f294acdc67e6b4": "veaero",
        # Hedgey: TokenVestingPlans, TokenLockupPlans
        "0x2cde9919e81b20b4b33dd562a48a84b54c48f00c": "hedgey",
        "0x1961a23409ca59eedca6a99c97e4087dad752486": "hedgey",
    },
}

# The report's words for the generic signals (a known contract is named by
# its protocol).
BY_COLLECTION = "nome-da-colecção"
BY_TOKEN_NAME = "nome-do-token"
BY_DESCRIPTION = "descrição"

# "Uniswap - 0.05% - EURT/WETH - 2319.3<>2359.1", "… - MIN<>MAX" (Uniswap V3
# and V4, and Sushi V3, which uses the same descriptor), "CL - USDC/ETH -
# 2126.0<>2445.5" (Aerodrome Slipstream): a pair, a price range.
_TOKEN_NAME_RE = re.compile(
    r"^\s*\S.{0,40}?\s+-\s+(?:\S+\s+-\s+)?[^\s/]+/[^\s/]+\s+-\s+\S+<>\S+\s*$")
# "This NFT represents a liquidity position in a Uniswap V3 …", "… a payment
# stream in a Sablier V2 Lockup Linear …", "… a stream in a Sablier Lockup …"
_DESCRIPTION_RE = re.compile(
    r"\bthis nft represents a (?:liquidity position|payment stream|stream)\b",
    re.IGNORECASE)
# name() / symbol() of the contract — only the shapes measured on 06/11.
# "Sablier" alone is also a word (French for hourglass): the measured shape
# carries "Lockup".
_COLLECTION_NAME_RES = (
    re.compile(r"\bpositions?\s+nft\b", re.IGNORECASE),        # Uniswap, Pancake, Sushi, Slipstream
    re.compile(r"^\s*sablier\s+(?:v\d+\s+)?lockup\b", re.IGNORECASE),
    re.compile(r"^\s*(?:voting)?token(?:vesting|lockup)plans\s*$", re.IGNORECASE),  # Hedgey
    re.compile(r"^\s*venft\s*$", re.IGNORECASE),               # Aerodrome's vote-escrow
)
_COLLECTION_SYMBOL_RES = (
    re.compile(r"-POSM?\s*$", re.IGNORECASE),     # UNI-V3-POS, UNI-V4-POSM, AERO-CL-POS…
    re.compile(r"^\s*SAB-(?:V\d+-)?LOCKUP\b", re.IGNORECASE),
    re.compile(r"^\s*venft\s*$", re.IGNORECASE),
)


def known_contract(chain: str, contract: str) -> str | None:
    """The protocol of a known DeFi contract on this chain, or None."""
    return DEFI_CONTRACTS.get(chain or "", {}).get((contract or "").lower())


def token_signal(metadata) -> str | None:
    """The token's own metadata: the position-shaped name, or the
    description that says what it is."""
    if not isinstance(metadata, dict):
        return None
    if _TOKEN_NAME_RE.match(str(metadata.get("name") or "")):
        return BY_TOKEN_NAME
    if _DESCRIPTION_RE.search(str(metadata.get("description") or "")):
        return BY_DESCRIPTION
    return None


def collection_signal(name: str, symbol: str) -> str | None:
    """The contract's name() / symbol()."""
    if any(rx.search(name or "") for rx in _COLLECTION_NAME_RES) or \
            any(rx.search(symbol or "") for rx in _COLLECTION_SYMBOL_RES):
        return BY_COLLECTION
    return None


class DefiDetector:
    """Is this token a DeFi position? The label says why — a protocol for a
    known contract, or the signal that fired — and is all a report shows.

    `collection_of(chain, contract) -> (name, symbol)` reads the contract
    over RPC (adapters.collection_reader); it raises when the read is OURS
    to have failed. Read once per contract and kept (a contract's name does
    not change); a failed read is not kept, counted in `unread`, and never
    a verdict — the other signals still apply. None: no RPC (tests)."""

    def __init__(self, *, collection_of: Callable[[str, str], tuple[str, str]] | None = None):
        self._collection_of = collection_of
        self._collections: dict[tuple[str, str], str | None] = {}
        self.unread = 0

    def offline(self, chain: str, contract: str, metadata=None) -> str | None:
        """The signals that cost nothing: the known contract and the token's
        metadata (when there is one)."""
        return known_contract(chain, contract) or token_signal(metadata)

    def collection(self, chain: str, contract: str) -> str | None:
        """The contract's name / symbol — one read per contract."""
        if self._collection_of is None:
            return None
        key = (chain or "", (contract or "").lower())
        if key not in self._collections:
            try:
                name, symbol = self._collection_of(*key)
            except Exception:  # noqa: BLE001 — ours: no verdict, read again next time
                self.unread += 1
                return None
            self._collections[key] = collection_signal(name, symbol)
        return self._collections[key]

    def __call__(self, chain: str, contract: str, metadata=None) -> str | None:
        return self.offline(chain, contract, metadata) or self.collection(chain, contract)

"""Relic mint — mints one relic NFT from a fresh wallet and records it into the
(blind) pool.

Contract-per-relic (Pedro 2026-08-22): each relic is its own disposable ERC-721
with a searchable contract NAME, so there is no shared contract an indexer could
filter as "the house cluster". The chain client is INJECTED (like chain/payout.py)
so the orchestration is testable with a FakeMinter; the real Web3Minter needs a
live Base RPC and the compiled contract artifact (dry-run: mainnet + throwaway
test wallet, Pedro 2026-08-22).

The claim code lives in the NFT DESCRIPTION (winner reads it off the found relic).
search-by-code is dead (measured), so the code in the description is not a
shortcut; the metadata must be pinned/immutable (decision 2026-08-22 §9) so it
can't vanish mid-hunt.
"""

from __future__ import annotations

import json
import secrets
import string
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# relic_core (package 1)
from .relic import CHAIN_BASE, relic_canonical_id


def json_escape(s: str) -> str:
    """Escape a string for safe embedding inside the on-chain metadata JSON.
    RelicNFT.sol embeds name/description verbatim, so the deployer must escape
    them (quotes, backslashes, control chars). json.dumps then strip the quotes."""
    return json.dumps(s)[1:-1]


# --------------------------------------------------------------------------- #
# Per-relic camouflage: NO shared literal across relics (Pedro 2026-08-22).     #
# A shared contract symbol or artist name would let an observer filter the      #
# whole pool by symbol/artist. So both are generated fresh and STYLE-VARIED per #
# relic; searching one never returns the others.                                #
# --------------------------------------------------------------------------- #

_ART_FRAGS_A = ("ink", "pixel", "quiet", "salt", "ash", "vellum", "amber", "rune",
                "moth", "gilt", "hollow", "brass", "tallow", "cinder", "verd")
_ART_FRAGS_B = ("gremlin", "oracle", "mancer", "smith", "warden", "kiln", "press",
                "wright", "monger", "haunt", "forge", "loom", "husk", "drift")
_FIRST = ("Mara", "Cael", "Vex", "Norr", "Ilse", "Tovi", "Bram", "Sable", "Wren", "Ovid")
_LAST = ("Voss", "Renn", "Quill", "Marsh", "Crane", "Holt", "Vane", "Pike", "Rue", "Ker")


def generate_symbol() -> str:
    """A varied 3-5 letter ticker. Not required to be unique — only NOT a shared
    literal (the old hardcoded 'RELIC' was a filterable signature)."""
    n = 3 + secrets.randbelow(3)
    return "".join(secrets.choice(string.ascii_uppercase) for _ in range(n))


def generate_artist() -> str:
    """A distinct, plausible artist name with a STYLE drawn per call, so the pool
    has no single artist-name shape to filter and no shared artist to search."""
    style = secrets.randbelow(4)
    if style == 0:  # lowercase handle
        return secrets.choice(_ART_FRAGS_A) + secrets.choice(_ART_FRAGS_B)
    if style == 1:  # dotted / underscored handle
        sep = secrets.choice((".", "_"))
        return secrets.choice(_ART_FRAGS_A) + sep + secrets.choice(_ART_FRAGS_B)
    if style == 2:  # First Last
        return f"{secrets.choice(_FIRST)} {secrets.choice(_LAST)}"
    # single word + short number
    return secrets.choice(_ART_FRAGS_A).capitalize() + str(secrets.randbelow(90) + 10)

# The exact on-chain description format. The code line is appended to the lore so
# a finder reads "<lore>\n\ncode: XXXXXXXX" off the relic. Kept in one place so
# the mint and any future parser agree.
def compose_onchain_description(lore: str, claim_code: str) -> str:
    return f"{lore}\n\ncode: {claim_code}"


@dataclass(frozen=True)
class MintResult:
    contract: str
    token_id: str
    image_uri: str
    tx_hash: str


@runtime_checkable
class RelicImageGen(Protocol):
    """image_prompt -> a PINNED/immutable image URI (IPFS pin or data: URI). Real
    impl: OpenAI image (like persona/avatar.py) + IPFS pin; fake returns a stub."""

    def generate(self, image_prompt: str) -> str: ...


@runtime_checkable
class Minter(Protocol):
    """Deploy a disposable ERC-721 (name = the relic name) and mint the 1/1 to the
    wallet, with metadata carrying name/description/image. Returns the coords."""

    def deploy_and_mint(
        self, *, name: str, symbol: str, description: str, image_uri: str,
        artist: str, wallet_ref: str,
    ) -> MintResult: ...


def mint_relic(
    *,
    relic_id: str,
    pool,            # relic_pool.RelicPool
    wallets,         # relic_wallets.WalletPool
    image_gen: RelicImageGen,
    minter: Minter,
    chain: str = CHAIN_BASE,
) -> MintResult:
    """Mint one relic end to end and record it into the pool.

    The identity is decrypted here (the BOT does this — automated, no human sees
    it; blind mode is about the operator/interface, not the mint process). The
    commitment binds the SPECIFIC minted NFT (canonical_id), computable only now
    that contract+token exist."""
    identity = pool.reveal_identity(relic_id)          # decrypt (bot-only)
    wallet = wallets.pick_free()                       # fresh, non-linkable
    image_uri = image_gen.generate(identity.image_prompt)
    description = compose_onchain_description(identity.description, identity.claim_code)

    result = minter.deploy_and_mint(
        name=identity.name,
        symbol=generate_symbol(),      # varied per relic — no shared 'RELIC' literal
        description=description,
        image_uri=image_uri,
        artist=generate_artist(),      # distinct artist per relic — artist search never returns the pool
        wallet_ref=wallet.ref,
    )

    canonical_id = relic_canonical_id(chain, result.contract, result.token_id)
    commitment = identity.commitment_for(canonical_id)
    pool.mark_minted(
        relic_id,
        chain=chain,
        contract=result.contract,
        token_id=result.token_id,
        mint_wallet_ref=wallet.ref,
        image_uri=result.image_uri,
        commitment=commitment,
    )
    return result


# --------------------------------------------------------------------------- #
# Real adapter (needs a live Base RPC + compiled artifact — NOT sandbox-testable)
# --------------------------------------------------------------------------- #


class Web3Minter:
    """Deploys RelicNFT.sol per relic and mints token #1. web3 is injected (v6),
    same discipline as PayoutEngine: no top-level web3 import, and `_send` is
    overridden in tests. `abi`/`bytecode` come from Fable compiling RelicNFT.sol;
    `wallets` resolves the signing key by ref at the instant of signing.

    tokenURI strategy: the contract stores name/description ON-CHAIN (immutable)
    and an image URI pointing to pinned IPFS — so the findable text never depends
    on a host that could go down mid-hunt (decision §9)."""

    def __init__(self, *, web3, wallets, abi, bytecode, chain_id: int | None = None):
        self._w3 = web3
        self._wallets = wallets
        self._abi = abi
        self._bytecode = bytecode
        self._chain_id = chain_id

    def deploy_and_mint(self, *, name, symbol, description, image_uri, artist, wallet_ref) -> MintResult:
        address, key = self._wallets.signing_key(wallet_ref)
        contract_addr, token_id, tx_hash = self._send(
            deployer_address=address,
            key=key,
            name=name,
            symbol=symbol,
            description=description,
            image_uri=image_uri,
            artist=artist,
        )
        return MintResult(
            contract=contract_addr, token_id=str(token_id),
            image_uri=image_uri, tx_hash=tx_hash,
        )

    def _send(self, *, deployer_address, key, name, symbol, description, image_uri, artist):  # pragma: no cover
        """Deploy + mint on-chain. Overridden in tests. Constructor args
        (name, symbol, description, image, artist) match RelicNFT.sol; the contract
        mints token 1 to the deployer in its constructor, so one tx deploys+mints.
        symbol and artist are varied per relic (no shared literal)."""
        w3 = self._w3
        acct = w3.eth.account.from_key(key)
        Relic = w3.eth.contract(abi=self._abi, bytecode=self._bytecode)
        # name/description/artist embed verbatim into on-chain JSON — escape them.
        tx = Relic.constructor(
            json_escape(name), symbol, json_escape(description), image_uri, json_escape(artist)
        ).build_transaction(
            {
                "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address),
                "chainId": self._chain_id or w3.eth.chain_id,
                "gasPrice": w3.eth.gas_price,
            }
        )
        signed = w3.eth.account.sign_transaction(tx, private_key=key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        return receipt.contractAddress, 1, tx_hash.hex()


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class FakeImageGen:
    def generate(self, image_prompt: str) -> str:
        return "ipfs://fake/" + str(abs(hash(image_prompt)) % 10_000)


class FakeMinter:
    """Deterministic in-memory minter for tests. Records what it minted so a test
    can assert the code went into the on-chain description."""

    def __init__(self):
        self.minted: list[dict] = []
        self._n = 0

    def deploy_and_mint(self, *, name, symbol, description, image_uri, artist, wallet_ref) -> MintResult:
        self._n += 1
        contract = f"0xC0FFEE{self._n:034x}"[:42]
        self.minted.append(
            {"name": name, "symbol": symbol, "description": description,
             "image_uri": image_uri, "artist": artist,
             "wallet_ref": wallet_ref, "contract": contract}
        )
        return MintResult(contract=contract, token_id="1", image_uri=image_uri,
                          tx_hash=f"0xtx{self._n}")

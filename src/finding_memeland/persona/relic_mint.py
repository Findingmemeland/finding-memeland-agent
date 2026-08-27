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

def generate_attributes(artist: str | None = None) -> str:
    """The FULL `attributes` JSON array for the on-chain metadata.

    Was a hardcoded single `{"trait_type":"artist", ...}` inside RelicNFT.sol.
    That made the trait shape a shared signature: even with distinct contract
    code, a scraper filtering recent Base ERC-721s by "exactly one trait, named
    artist" pulled the whole pool (audit 2026-08-26, P0-1, third signature).

    So the count (1-3) and the trait NAMES vary per relic. The artist value is
    still carried — it is the one field with a reason to exist — but under a
    name drawn per relic, and sometimes not first.
    """
    artist = artist or generate_artist()
    traits: list[tuple[str, str]] = [
        (secrets.choice(("artist", "maker", "scribe", "hand", "attributed to")), artist)
    ]
    for label, values in (
        ("edition", ("1 of 1", "unique", "single", "sole impression")),
        ("condition", ("intact", "worn", "weathered", "pristine", "chipped")),
        ("medium", ("ink", "pigment", "silver gelatin", "oil", "graphite", "enamel")),
        ("era", ("undated", "early", "late", "unknown")),
    ):
        if secrets.randbelow(2):                  # each optional trait, coin-flipped
            traits.append((label, secrets.choice(values)))
    secrets.SystemRandom().shuffle(traits)        # artist is not always first
    del traits[3:]                                # at most 3 — more looks generated
    return json.dumps(
        [{"trait_type": t, "value": v} for t, v in traits], separators=(",", ":")
    )


# How the claim code rides along in the on-chain description.
#
# This used to be exactly one shape — "<lore>\n\ncode: XXXXXXXX" — which is a
# literal string shared by every relic in the pool. A metadata scraper looking
# for "\n\ncode: " over recent Base ERC-721s found all of them at once, with no
# need to touch the contract code at all (audit 2026-08-26, P0-1, second
# signature). Varying the phrasing removes the constant.
#
# The code itself is unchanged and still plainly visible: the game REQUIRES the
# finder to read it off the relic. The published rules say "the claim code is in
# its description", not "look for the word code", so nothing here changes what a
# player has to do.
_CODE_STYLES = (
    "{lore}\n\ncode: {code}",
    "{lore}\n\nkey — {code}",
    "{lore}\n\npass: {code}",
    "{lore}\n\nsigil: {code}",
    "{lore}\n\nspeak {code} and it answers.",
    "{lore}\n\nit answers to {code}.",
    "{lore}\n\n{code}",
)


def compose_onchain_description(lore: str, claim_code: str) -> str:
    return secrets.choice(_CODE_STYLES).format(lore=lore, code=claim_code)


def generate_provenance_hash() -> str:
    """32 random bytes for RelicNFT's `provenanceHash` immutable — the thing that
    makes each relic's RUNTIME bytecode unique. See the contract's comment: this
    is the fix for the fingerprint that let one query list the whole pool."""
    return "0x" + secrets.token_hex(32)


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
        attributes: str, provenance_hash: str, wallet_ref: str,
    ) -> MintResult: ...


def _find_artifact(filename: str, *, path: str | None = None, env_var: str | None = None):
    """Locate `contracts/<filename>` the same way for every artifact.

    Search instead of computing one path. A single relative guess breaks the
    moment the deployment layout changes — installed package vs run-from-repo
    put this module at different depths, and the failure only shows up in
    production (measured 2026-08-24: worked locally, "not configured" on
    Railway). Every candidate is reported on failure so the next surprise is
    diagnosable instead of mysterious.
    """
    import os
    from pathlib import Path

    tried: list[Path] = []
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    if env_var and os.environ.get(env_var):
        candidates.append(Path(os.environ[env_var]))

    here = Path(__file__).resolve()
    # Walk up from this module: works whether the package sits in src/, in
    # site-packages next to a copied contracts/, or anywhere else.
    candidates += [parent / "contracts" / filename for parent in here.parents[:5]]
    # Alongside the package itself, if the artifact is shipped as package data.
    candidates.append(here.parent.parent / "contracts" / filename)
    # And relative to wherever the process was started.
    candidates.append(Path.cwd() / "contracts" / filename)

    for candidate in candidates:
        tried.append(candidate)
        if candidate.exists():
            return candidate
    listed = "\n  ".join(str(t) for t in tried)
    raise RuntimeError(
        f"contract artifact {filename} not found. Looked in:\n  "
        + listed
        + (f"\nSet {env_var} to an absolute path, or " if env_var else "\n")
        + "place the file in contracts/ at the repo root. It must be the ABI + "
        "bytecode from the SAME compilation deployed by hand, so the agent mints "
        "with bytecode that is known to work."
    )


def load_contract_artifact(path: str | None = None) -> tuple[list, str]:
    """(abi, bytecode) for RelicNFT.sol, read from a compiled artifact JSON.

    Kept as a data file rather than compiled at runtime: solc + OpenZeppelin is a
    heavy toolchain to carry into a Railway container for a contract that never
    changes, and — more importantly — the bytecode that mints real relics should
    be the SAME artifact that was compiled, deployed and verified by hand. A
    build step that silently produces different bytecode is exactly the kind of
    drift you find out about on-chain.

    Expected shape (what Remix's "Compilation Details" gives):
        {"abi": [...], "bytecode": "0x60806040..."}
    Also accepts Remix's raw export where bytecode sits under
    `data.bytecode.object` or `evm.bytecode.object`.
    """
    import json

    p = _find_artifact("RelicNFT.json", path=path, env_var="RELIC_CONTRACT_ARTIFACT")
    data = json.loads(p.read_text())

    abi = data.get("abi")
    bytecode = data.get("bytecode")
    if isinstance(bytecode, dict):                      # Remix nests it
        bytecode = bytecode.get("object")
    if bytecode is None:
        for branch in ("data", "evm"):
            node = (data.get(branch) or {}).get("bytecode") or {}
            bytecode = node.get("object") or bytecode
    if not abi or not bytecode:
        raise RuntimeError(f"artifact at {p} has no usable abi/bytecode")
    if not str(bytecode).startswith("0x"):
        bytecode = "0x" + str(bytecode)
    return abi, str(bytecode)


MANIFOLD_ARTIFACT = "RelicManifoldProxy.json"


def load_manifold_artifact(path: str | None = None) -> dict:
    """The Manifold-proxy artifact (contracts/RelicManifoldProxy.json), validated.

    Keys: `abi` + `bytecode` (the constructor-only deployer, RelicManifoldProxy.sol),
    `manifold_runtime` (the 298 bytes of a real Manifold ERC721Creator proxy on
    Base, returned verbatim as the deployed code) and `manifold_implementation`
    (the EIP-1967 implementation those proxies point to). See
    Probe_Manifold_Proxy.md for how each was read from the chain."""
    import json

    p = _find_artifact(MANIFOLD_ARTIFACT, path=path, env_var="RELIC_MANIFOLD_ARTIFACT")
    data = json.loads(p.read_text())
    abi, bytecode = data.get("abi"), data.get("bytecode")
    runtime, impl = data.get("manifold_runtime"), data.get("manifold_implementation")
    if not abi or not bytecode:
        raise RuntimeError(f"artifact at {p} has no usable abi/bytecode")
    ctor = next((e for e in abi if e.get("type") == "constructor"), None)
    types = [i.get("type") for i in (ctor or {}).get("inputs", [])]
    if types != ["address", "string", "string", "bytes"]:
        raise RuntimeError(
            f"artifact at {p}: constructor must be (address,string,string,bytes), got {types}"
        )
    if not runtime or not str(runtime).startswith("0x") or len(runtime) < 2 + 2 * 100:
        raise RuntimeError(f"artifact at {p}: manifold_runtime missing or too short")
    if not impl or not str(impl).startswith("0x") or len(impl) != 42:
        raise RuntimeError(f"artifact at {p}: manifold_implementation is not an address")
    return data


def create_relic(
    *,
    pool,              # relic_pool.RelicPool
    generator,         # relic_generator.RelicGenerator
    register: str | None = None,
    relic_id: str | None = None,
) -> str:
    """Invent ONE relic identity and store it ENCRYPTED in the pool. Returns its id.

    Deliberately separate from `mint_relic`: creating is cheap and offline, minting
    costs gas and can fail on its own. Keeping them apart means a failed mint never
    burns an identity, and the pool can be filled long before anything is launched
    — which matters, because a relic's anonymity comes from having sat in Base's
    normal traffic for weeks.

    Everything the generator needs to avoid repeating itself is read from the pool
    here, so the caller cannot forget it:
      - `sequence`     rotates the name domain (7 relics => 7 different worlds)
      - `avoid_recent` themes, so the pool stops reinventing the same character
      - `avoid_words`  hard rule: a word spent by one relic is never reused

    The identity is never returned or logged — only the id. Blind mode holds."""
    import uuid

    from .relic import Relic

    generated = generator.generate(
        register=register,
        sequence=pool.relic_count(),
        avoid_recent=pool.avoid_recent(),
        avoid_words=pool.spent_words(),
    )
    # The id is minted HERE, not left to the database: `pool.add` returns nothing,
    # so a server-generated id would be written and immediately lost — and the
    # caller needs it to mint. A uuid4 is as unique as the column's own default.
    relic = Relic(id=relic_id or str(uuid.uuid4()))
    pool.add(relic, generated.to_identity())
    return relic.id


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
    # RETOMAR, não re-sortear (auditoria v3). A reserva do P1-3 era desfeita
    # pelo próprio retry: um segundo /relic_mint pedia uma carteira NOVA e
    # sobrescrevia o `mint_wallet_ref`, devolvendo a primeira ao conjunto livre
    # — precisamente a reutilização que a reserva existe para impedir. Se este
    # relic já tem carteira reservada, é essa que se usa: ela já está queimada
    # para ele e para mais ninguém.
    reserved = pool.reserved_wallet_ref(relic_id)
    if reserved:
        wallet = wallets.handle_for(reserved)
    else:
        wallet = wallets.pick_free()                   # fresh, non-linkable
        # RESERVA antes de qualquer assinatura: a partir daqui a carteira conta
        # como gasta mesmo que tudo o resto falhe.
        pool.reserve_wallet(relic_id, wallet.ref)
    image_uri = image_gen.generate(identity.image_prompt)
    description = compose_onchain_description(identity.description, identity.claim_code)

    result = minter.deploy_and_mint(
        name=identity.name,
        symbol=generate_symbol(),      # varied per relic — no shared 'RELIC' literal
        description=description,
        image_uri=image_uri,
        attributes=generate_attributes(),          # varied trait shape — see generate_attributes
        provenance_hash=generate_provenance_hash(),  # unique runtime bytecode — see the contract
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

    def deploy_and_mint(self, *, name, symbol, description, image_uri, attributes,
                        provenance_hash, wallet_ref) -> MintResult:
        address, key = self._wallets.signing_key(wallet_ref)
        contract_addr, token_id, tx_hash = self._send(
            deployer_address=address,
            key=key,
            name=name,
            symbol=symbol,
            description=description,
            image_uri=image_uri,
            attributes=attributes,
            provenance_hash=provenance_hash,
        )
        return MintResult(
            contract=contract_addr, token_id=str(token_id),
            image_uri=image_uri, tx_hash=tx_hash,
        )

    def _send(self, *, deployer_address, key, name, symbol, description, image_uri,
              attributes, provenance_hash):  # pragma: no cover
        """Deploy + mint on-chain. Overridden in tests. Constructor args
        (name, symbol, description, image, attributes, provenanceHash) match
        RelicNFT.sol; the contract mints token 1 to the deployer in its
        constructor, so one tx deploys+mints. symbol, attributes and
        provenanceHash are varied per relic (no shared literal, no shared code)."""
        w3 = self._w3
        acct = w3.eth.account.from_key(key)
        Relic = w3.eth.contract(abi=self._abi, bytecode=self._bytecode)
        # name/description embed verbatim into on-chain JSON — escape them.
        # `attributes` is ALREADY a JSON array built by generate_attributes(), so
        # escaping it here would double-escape and produce invalid metadata.
        tx = Relic.constructor(
            json_escape(name), symbol, json_escape(description), image_uri,
            attributes, provenance_hash,
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
        # timeout explícito: sem ele o default pode pendurar o worker do mint
        # indefinidamente num RPC lento (auditoria 2026-08-26, P1-8).
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        # O status NÃO era verificado. Numa reversão, `contractAddress` vem None
        # e o mint gravava um relic com contract=None marcado como MINTED —
        # identidade gasta, carteira gasta, e contabilidade confusa. Melhor
        # rebentar aqui: quem chama aborta o mint e a identidade fica no pool.
        if getattr(receipt, "status", 1) != 1:
            raise RuntimeError(
                f"mint transaction reverted (tx {tx_hash.hex()}) — nada gravado"
            )
        if not receipt.contractAddress:
            raise RuntimeError(
                f"mint sem contractAddress no recibo (tx {tx_hash.hex()}) — "
                "recibo inesperado, nada gravado"
            )
        return receipt.contractAddress, 1, tx_hash.hex()


# --------------------------------------------------------------------------- #
# Manifold-proxy minter (probe 2026-08-26, Probe_Manifold_Proxy.md)              #
# --------------------------------------------------------------------------- #

# The slice of Manifold's ERC721CreatorImplementation we call through the proxy.
MANIFOLD_ABI = [
    {
        "inputs": [{"name": "to", "type": "address"}, {"name": "uri", "type": "string"}],
        "name": "mintBase",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "renounceOwnership",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "owner",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]
# Read-only slice used by the resume check (name + ownerOf).
_ERC721_READ_ABI = [
    {"inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "ownerOf",
     "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
]
# keccak("Transfer(address,address,uint256)") — the mint log; topics[3] is the id.
_ERC721_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def build_token_metadata(*, name: str, description: str, image_uri: str, attributes: str) -> bytes:
    """The ERC-721 metadata JSON for a Manifold relic — same four fields the
    RelicNFT.sol tokenURI carried, now a file pinned to IPFS (the crowd's
    convention; an on-chain data: URI would be a rare trait among Manifold
    collections). `attributes` arrives as the JSON array string from
    generate_attributes(). json.dumps does the escaping — no json_escape here."""
    return json.dumps(
        {
            "name": name,
            "description": description,
            "image": image_uri,
            "attributes": json.loads(attributes) if attributes else [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


class ManifoldMinter:
    """Mint a relic as a Manifold `ERC721Creator` proxy (three transactions from
    the relic wallet): deploy the proxy, `mintBase(wallet, uri)`,
    `renounceOwnership()`.

    Why this exists: a pool of contracts compiled from one source is one bytecode
    class, enumerable with a single indexer query (audit P0-1, confirmed on-chain).
    A Manifold proxy's RUNTIME is byte-for-byte the same across thousands of
    collections on Base, so `eth_getCode` no longer distinguishes the pool.

    Why `renounceOwnership` is not optional: Manifold's `setTokenURI` is
    owner/admin-only — while the relic wallet is owner, whoever holds its key can
    rewrite the description (and the claim code) mid-hunt, and the public promise
    "nothing moves after the mint" would be false. After renouncing, owner is
    0x0, no admin exists (the deployer is never admin by default), and the
    metadata is as fixed as RelicNFT.sol's was.

    Same discipline as Web3Minter: web3 injected, `_send` overridden in tests,
    the key resolved only at the instant of signing."""

    # keccak256("eip1967.proxy.implementation") - 1 — where the Manifold proxy
    # runtime reads its implementation from. The address is NOT in the 298 bytes
    # of code; it is in this storage slot, and the camouflage is that thousands
    # of proxies hold the SAME value there.
    IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"

    def __init__(self, *, web3, wallets, pinner, artifact: dict,
                 implementation: str | None = None, chain_id: int | None = None,
                 allow_implementation_override: bool = False):
        self._w3 = web3
        self._wallets = wallets
        self._pinner = pinner                     # relic_image.PinataPinner (or a fake)
        self._abi = artifact["abi"]
        self._bytecode = artifact["bytecode"]
        self._runtime = artifact["manifold_runtime"]
        known = str(artifact["manifold_implementation"])
        chosen = str(implementation or known)
        # The whole point is pointing at the implementation THOUSANDS of Manifold
        # proxies point at. A different address — even a byte-identical copy of
        # their code deployed by us — makes the pool a class of one again, worse
        # than RelicNFT.sol. So an override must be deliberate, never a typo.
        if chosen.lower() != known.lower() and not allow_implementation_override:
            raise RuntimeError(
                f"manifold implementation override {chosen} differs from the artifact's "
                f"{known}; refusing. A relic must point at the implementation the Manifold "
                "crowd uses. If Manifold rotated versions, verify the new address on a fresh "
                "Studio deployment and pass allow_implementation_override=True."
            )
        self._impl = chosen
        self._chain_id = chain_id

    @property
    def implementation(self) -> str:
        return self._impl

    def deploy_and_mint(self, *, name, symbol, description, image_uri, attributes,
                        provenance_hash, wallet_ref) -> MintResult:
        # `provenance_hash` is accepted for protocol compatibility and unused:
        # the proxy has no code of its own to make unique — that is the point.
        del provenance_hash
        metadata = build_token_metadata(
            name=name, description=description, image_uri=image_uri, attributes=attributes,
        )
        token_uri = self._pinner.pin(metadata, name="metadata.json")
        address, key = self._wallets.signing_key(wallet_ref)
        contract_addr, token_id, tx_hash = self._send(
            deployer_address=address, key=key, name=name, symbol=symbol, token_uri=token_uri,
        )
        return MintResult(
            contract=contract_addr, token_id=str(token_id),
            image_uri=image_uri, tx_hash=tx_hash,
        )

    # ---- chain helpers (tolerant of a lagging public RPC) ------------------ #
    def _code_at(self, address, *, attempts: int = 8,
                 pause_s: float = 1.5) -> bytes:  # pragma: no cover
        """`eth_getCode` that survives a load-balanced RPC answering from a node a
        block behind: the very first mint on Base (2026-08-27) deployed a perfect
        proxy and then read empty code back one second later — and the guard,
        correctly, refused to mint on "different" code. Retry until non-empty."""
        import time

        w3 = self._w3
        code = b""
        for i in range(attempts):
            code = bytes(w3.eth.get_code(w3.to_checksum_address(address)))
            if code:
                return code
            time.sleep(pause_s * (i + 1))
        return code

    def _looks_like_our_proxy(self, address, *, deployer, name) -> bool:  # pragma: no cover
        """A Manifold proxy this wallet already deployed for THIS relic and never
        minted on: same runtime, same implementation slot, owner == the wallet,
        same collection name, no token 1. Used to RESUME after a partial mint
        instead of leaving an initialised orphan behind and deploying again."""
        w3 = self._w3
        runtime = bytes.fromhex(self._runtime[2:])
        if self._code_at(address, attempts=2) != runtime:
            return False
        slot = bytes(w3.eth.get_storage_at(address, self.IMPLEMENTATION_SLOT))
        if ("0x" + slot.hex()[-40:]).lower() != self._impl.lower():
            return False
        relic = w3.eth.contract(
            address=w3.to_checksum_address(address), abi=MANIFOLD_ABI + _ERC721_READ_ABI
        )
        try:
            if relic.functions.owner().call().lower() != str(deployer).lower():
                return False
            if relic.functions.name().call() != name:
                return False
        except Exception:  # noqa: BLE001 — not ours if it does not even answer
            return False
        try:
            holder = relic.functions.ownerOf(1).call()
        except Exception:  # noqa: BLE001 — "invalid token ID" == still empty: reusable
            return True
        return int(holder, 16) == 0           # a real token holder == a finished relic

    def _existing_proxy_for(self, deployer, name):  # pragma: no cover
        """The address of an un-minted proxy this wallet already deployed for
        `name`, or None. CREATE addresses are deterministic in (deployer, nonce),
        so every past nonce is a candidate."""
        import rlp
        from eth_utils import keccak, to_checksum_address

        w3 = self._w3
        n = w3.eth.get_transaction_count(w3.to_checksum_address(deployer), "latest")
        for nonce in range(n):
            cand = to_checksum_address(
                keccak(rlp.encode([bytes.fromhex(str(deployer)[2:]), nonce]))[12:]
            )
            if self._looks_like_our_proxy(cand, deployer=deployer, name=name):
                return cand
        return None

    def _send(self, *, deployer_address, key, name, symbol, token_uri):  # pragma: no cover
        """Three transactions, in order, each waited for and status-checked:
        deploy proxy -> mintBase -> renounceOwnership. Overridden in tests.

        RESUMES rather than repeats: if this wallet already deployed a proxy for
        this relic (a previous attempt that died after the deploy), that proxy
        is used and only the missing steps run — no second collection with the
        same name is ever created."""
        w3 = self._w3
        acct = w3.eth.account.from_key(key)
        chain_id = self._chain_id or w3.eth.chain_id
        runtime = bytes.fromhex(self._runtime[2:])
        impl = w3.to_checksum_address(self._impl)
        if not self._code_at(impl, attempts=3):
            raise RuntimeError(
                f"manifold implementation {impl} has no code on this chain — refusing to mint"
            )

        def _submit(build, label: str):
            tx = build.build_transaction({
                "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
                "chainId": chain_id,
                "gasPrice": w3.eth.gas_price,
            })
            signed = w3.eth.account.sign_transaction(tx, private_key=key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            if receipt["status"] != 1:
                raise RuntimeError(f"{label} reverted (tx {tx_hash.hex()})")
            return receipt, tx_hash.hex()

        proxy_addr = self._existing_proxy_for(acct.address, name)
        if proxy_addr is None:
            Proxy = w3.eth.contract(abi=self._abi, bytecode=self._bytecode)
            deploy_rc, _deploy_tx = _submit(
                Proxy.constructor(impl, name, symbol, runtime), "proxy deploy"
            )
            proxy_addr = deploy_rc["contractAddress"]
            if not proxy_addr:
                raise RuntimeError("proxy deploy: no contractAddress in receipt")
            if self._code_at(proxy_addr) != runtime:
                raise RuntimeError(
                    f"proxy {proxy_addr} runtime differs from the Manifold runtime — "
                    "NOT minting on it; investigate before retrying"
                )
            slot = bytes(w3.eth.get_storage_at(proxy_addr, self.IMPLEMENTATION_SLOT))
            slot_addr = "0x" + slot.hex()[-40:]
            if slot_addr.lower() != impl.lower():
                raise RuntimeError(
                    f"proxy {proxy_addr} implementation slot holds {slot_addr}, expected {impl} — "
                    "NOT minting on it"
                )

        relic = w3.eth.contract(address=proxy_addr, abi=MANIFOLD_ABI)
        mint_rc, mint_tx = _submit(
            relic.functions.mintBase(acct.address, token_uri), "mintBase"
        )
        token_id = None
        for log in mint_rc["logs"]:
            topics = [t.hex() if hasattr(t, "hex") else str(t) for t in log.get("topics", [])]
            topics = [t if t.startswith("0x") else "0x" + t for t in topics]
            if (
                log["address"].lower() == proxy_addr.lower()
                and len(topics) == 4 and topics[0] == _ERC721_TRANSFER_TOPIC
            ):
                token_id = int(topics[3], 16)
                break
        if token_id is None:
            raise RuntimeError(
                f"mintBase mined ({mint_tx}) but no Transfer log found — "
                "record the token id by hand before launching this relic"
            )

        _submit(relic.functions.renounceOwnership(), "renounceOwnership")
        if int(relic.functions.owner().call(), 16) != 0:
            raise RuntimeError(f"proxy {proxy_addr} still has an owner after renounce")
        return proxy_addr, token_id, mint_tx


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

    def deploy_and_mint(self, *, name, symbol, description, image_uri, attributes,
                        provenance_hash, wallet_ref) -> MintResult:
        self._n += 1
        contract = f"0xC0FFEE{self._n:034x}"[:42]
        self.minted.append(
            {"name": name, "symbol": symbol, "description": description,
             "image_uri": image_uri, "attributes": attributes,
             "provenance_hash": provenance_hash,
             "wallet_ref": wallet_ref, "contract": contract}
        )
        return MintResult(contract=contract, token_id="1", image_uri=image_uri,
                          tx_hash=f"0xtx{self._n}")

"""Trophy transfer — hand the found relic to the winner.

Debut decision (Pedro, 2026-08-22): the winner receives THE VERY NFT they found,
transferred as-is. No collection, no remint/burn on the critical path — that is
deferred until after the first clean relic hunt (a collection is narrative value;
a winner waiting is not the moment to debut untested contract code).

Ordering rule (deliberate): the $FIND PRIZE IS PAID FIRST by the existing payout
machine; the trophy is a BONUS transferred after. A failed trophy transfer must
never block or reverse a paid prize — it is retried and, if it still fails, the
operator is alerted with everything needed to send it by hand. The winner always
ends up paid.

Idempotency: `transfer_trophy` checks current ownership first. If the relic is
already in the winner's wallet (a retry after a receipt we never saw), it returns
the previous result instead of sending a second transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TrophyResult:
    contract: str
    token_id: str
    to_wallet: str
    tx_hash: str | None
    already_owned: bool = False   # True when the transfer had already happened

    @property
    def delivered(self) -> bool:
        return self.already_owned or bool(self.tx_hash)


class TrophyTransferFailed(RuntimeError):
    """Raised after retries are exhausted. Carries an operator-facing message with
    the exact details needed for a manual send — the prize is already paid, so
    this is a follow-up task, never a lost hunt."""


@runtime_checkable
class NFTTransferPort(Protocol):
    """The chain operations we need. Real adapter: Web3NFTTransfer (below)."""

    def owner_of(self, contract: str, token_id: str) -> str: ...
    def transfer(self, *, contract: str, token_id: str, to_wallet: str,
                 wallet_ref: str) -> str: ...   # returns tx hash


def transfer_trophy(
    *,
    relic,                 # persona.relic.Relic (minted: contract/token/wallet_ref)
    to_wallet: str,
    transfer_port: NFTTransferPort,
    notifier=None,
    attempts: int = 3,
) -> TrophyResult:
    """Send the relic to the winner. Idempotent, retried, and loud on failure."""
    if not (relic.contract and relic.token_id and relic.mint_wallet_ref):
        raise TrophyTransferFailed(
            f"relic {relic.id} has no on-chain coordinates — cannot transfer a trophy"
        )
    if not to_wallet:
        raise TrophyTransferFailed("winner wallet missing — cannot transfer the trophy")

    # Idempotency: never send twice.
    try:
        current = transfer_port.owner_of(relic.contract, relic.token_id)
        if current and current.lower() == to_wallet.lower():
            return TrophyResult(relic.contract, relic.token_id, to_wallet,
                                tx_hash=None, already_owned=True)
    except Exception:  # noqa: BLE001 — an unreadable owner must not block the send
        pass

    last: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            tx = transfer_port.transfer(
                contract=relic.contract, token_id=relic.token_id,
                to_wallet=to_wallet, wallet_ref=relic.mint_wallet_ref,
            )
            return TrophyResult(relic.contract, relic.token_id, to_wallet, tx_hash=tx)
        except Exception as e:  # noqa: BLE001
            last = e

    msg = (
        f"🏆 TROPHY NOT DELIVERED (the $FIND prize WAS paid — this is a follow-up). "
        f"Send it by hand: contract {relic.contract} token {relic.token_id} "
        f"from wallet {relic.mint_wallet_ref} to {to_wallet}. Last error: {last!r}"
    )
    if notifier is not None:
        try:
            notifier.notify(msg)
        except Exception:  # noqa: BLE001 — never mask the original failure
            pass
    raise TrophyTransferFailed(msg)


# --------------------------------------------------------------------------- #
# Real adapter (live RPC — NOT sandbox-testable; proven in the mainnet dry-run) #
# --------------------------------------------------------------------------- #

# Minimal ERC-721 ABI: ownerOf + safeTransferFrom (same style as chain/payout.py).
ERC721_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "ownerOf",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "tokenId", "type": "uint256"},
        ],
        "name": "safeTransferFrom",
        "outputs": [],
        "type": "function",
    },
]


class Web3NFTTransfer:
    """ERC-721 transfer from the relic's own wallet. web3 injected; `_send` is
    overridden in tests — same discipline as PayoutEngine/Web3Minter. The signing
    key is resolved from Doppler at the instant of signing and never stored."""

    def __init__(self, *, web3, wallets, chain_id: int | None = None):
        self._w3 = web3
        self._wallets = wallets
        self._chain_id = chain_id

    def owner_of(self, contract: str, token_id: str) -> str:
        c = self._w3.eth.contract(
            address=self._w3.to_checksum_address(contract), abi=ERC721_ABI
        )
        return c.functions.ownerOf(int(token_id)).call()

    def transfer(self, *, contract: str, token_id: str, to_wallet: str, wallet_ref: str) -> str:
        address, key = self._wallets.signing_key(wallet_ref)
        return self._send(
            contract=contract, token_id=token_id, to_wallet=to_wallet,
            from_address=address, key=key,
        )

    def _send(self, *, contract, token_id, to_wallet, from_address, key) -> str:  # pragma: no cover
        w3 = self._w3
        c = w3.eth.contract(address=w3.to_checksum_address(contract), abi=ERC721_ABI)
        tx = c.functions.safeTransferFrom(
            w3.to_checksum_address(from_address),
            w3.to_checksum_address(to_wallet),
            int(token_id),
        ).build_transaction(
            {
                "from": w3.to_checksum_address(from_address),
                "nonce": w3.eth.get_transaction_count(from_address),
                "chainId": self._chain_id or w3.eth.chain_id,
                "gasPrice": w3.eth.gas_price,
            }
        )
        signed = w3.eth.account.sign_transaction(tx, private_key=key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash)
        return tx_hash.hex()


class FakeNFTTransfer:
    """In-memory transfer port for tests."""

    def __init__(self, *, owner: str = "0xRELICWALLET", fail_times: int = 0):
        self.owners: dict[tuple, str] = {}
        self._default_owner = owner
        self._fail_times = fail_times
        self.sent: list[dict] = []

    def owner_of(self, contract, token_id):
        return self.owners.get((contract, str(token_id)), self._default_owner)

    def transfer(self, *, contract, token_id, to_wallet, wallet_ref):
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("rpc hiccup")
        self.sent.append({"contract": contract, "token_id": token_id,
                          "to": to_wallet, "wallet_ref": wallet_ref})
        self.owners[(contract, str(token_id))] = to_wallet
        return f"0xtrophytx{len(self.sent)}"

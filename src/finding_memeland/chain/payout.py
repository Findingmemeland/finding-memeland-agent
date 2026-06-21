"""Payout Engine — sends the prize from the hot wallet (ERC-20 transfer on Base).

Blast-radius control (litepaper §3): the hot wallet holds at most 1-2 hunts of
prizes, with a HARDCODED per-hunt cap enforced here. The treasury Safe is
separate and the agent cannot touch it.

The web3 client is INJECTED (built by the caller), so this module needs no
top-level web3 import — it stays importable/testable without web3 installed, and
the actual chain call lives in `_submit_transfer`, which tests override.

Amounts at this API are in WHOLE tokens (for clean display/config); they are
scaled to base units by `decimals` before the on-chain transfer.
"""

from __future__ import annotations

from dataclasses import dataclass

# Minimal ERC-20 ABI: transfer + balanceOf + decimals.
ERC20_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
]


@dataclass
class PayoutResult:
    tx_hash: str
    amount_fmml: int       # whole tokens (matches the amount requested)


class PayoutEngine:
    def __init__(
        self,
        *,
        web3,
        token_address: str,
        hot_wallet_key: str,
        per_hunt_cap: int,         # whole tokens
        decimals: int = 18,
    ):
        self._w3 = web3
        self._token_address = token_address
        self._key = hot_wallet_key
        self._cap = per_hunt_cap
        self._decimals = decimals

    # ------------------------------------------------------------------
    def _scale(self, whole_tokens: int) -> int:
        return whole_tokens * (10 ** self._decimals)

    def send_prize(self, *, hunt_id, to_wallet: str, amount_fmml: int) -> PayoutResult:
        """Transfer the prize, enforcing the per-hunt cap. Returns the receipt.

        Hard invariant: never send more than the cap.
        """
        if amount_fmml <= 0:
            raise ValueError("payout amount must be positive")
        if amount_fmml > self._cap:
            raise ValueError(
                f"payout {amount_fmml} exceeds hardcoded per-hunt cap {self._cap}"
            )
        to = self._w3.to_checksum_address(to_wallet) if self._w3 else to_wallet
        base_amount = self._scale(amount_fmml)
        tx_hash = self._submit_transfer(to, base_amount)
        return PayoutResult(tx_hash=tx_hash, amount_fmml=amount_fmml)

    def balance_of(self, address: str) -> int:
        """Token balance of `address` in WHOLE tokens (floor)."""
        contract = self._w3.eth.contract(
            address=self._w3.to_checksum_address(self._token_address), abi=ERC20_ABI
        )
        base = contract.functions.balanceOf(
            self._w3.to_checksum_address(address)
        ).call()
        return base // (10 ** self._decimals)

    # ------------------------------------------------------------------
    def _submit_transfer(self, to_checksummed: str, base_amount: int) -> str:
        """Build, sign and send the ERC-20 transfer; wait for the receipt.

        Overridden in tests. Uses the injected web3 client (web3.py v6).
        """
        w3 = self._w3
        account = w3.eth.account.from_key(self._key)
        contract = w3.eth.contract(
            address=w3.to_checksum_address(self._token_address), abi=ERC20_ABI
        )
        tx = contract.functions.transfer(to_checksummed, base_amount).build_transaction(
            {
                "from": account.address,
                "nonce": w3.eth.get_transaction_count(account.address),
                "chainId": w3.eth.chain_id,
                "gasPrice": w3.eth.gas_price,
            }
        )
        signed = w3.eth.account.sign_transaction(tx, private_key=self._key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status != 1:
            raise RuntimeError(f"transfer reverted: {tx_hash.hex()}")
        return tx_hash.hex()

"""Payout Engine — sends the prize from the hot wallet.

Blast-radius control (litepaper §3): the hot wallet holds at most 1-2 hunts of
prizes, with a HARDCODED per-hunt cap enforced here. The treasury Safe is
separate and the agent cannot touch it. Every payout is logged to `payouts`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PayoutResult:
    tx_hash: str
    amount_fmml: int


class PayoutEngine:
    def __init__(self, *, web3, token_address: str, hot_wallet_key: str, per_hunt_cap: int, repo):
        self._w3 = web3
        self._token_address = token_address
        self._key = hot_wallet_key
        self._cap = per_hunt_cap
        self._repo = repo

    def send_prize(self, *, hunt_id: int, to_wallet: str, amount_fmml: int) -> PayoutResult:
        """Transfer the prize, enforcing the per-hunt cap. Logs to `payouts`.

        Hard invariant — refuse to ever send more than the cap.
        """
        if amount_fmml <= 0:
            raise ValueError("payout amount must be positive")
        if amount_fmml > self._cap:
            raise ValueError(
                f"payout {amount_fmml} exceeds hardcoded per-hunt cap {self._cap}"
            )
        # TODO(step 27): build ERC-20 transfer(to_wallet, amount_fmml),
        # sign with self._key, send via self._w3, wait for receipt, write a
        # payouts row (pending->sent->confirmed), return PayoutResult.
        raise NotImplementedError("transfer — implemented in step 27")

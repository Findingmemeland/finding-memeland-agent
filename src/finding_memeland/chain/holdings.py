"""On-chain holding checks against Base.

Continuity matters (anti-sniper): a wallet must have held >= the floor across
the whole window, not just at submission. We sample balances daily into
holding_samples and check that every sample in the window met the floor.
Smart-contract wallets (Safes) are allowed, not just EOAs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Minimal ERC-20 ABI — balanceOf only.
ERC20_BALANCEOF_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    }
]


class Holdings:
    def __init__(self, *, web3, token_address: str, repo):
        self._w3 = web3
        self._token_address = token_address
        self._repo = repo            # reads/writes holding_samples

    def current_balance(self, wallet: str) -> int:
        """Live balanceOf in base units.

        TODO(step 26): build contract from ERC20_BALANCEOF_ABI, call balanceOf,
        return int. Validate checksum address (EIP-55) before calling.
        """
        raise NotImplementedError("balanceOf — implemented in step 26")

    def sample_balance(self, wallet: str) -> int:
        """Read current balance and persist a holding_samples row (daily job)."""
        raise NotImplementedError("sample — implemented in step 26")

    def has_continuous_holding(
        self, *, wallet: str, min_balance: int, holding_hours: int
    ) -> bool:
        """True iff every daily sample in the window met the floor.

        TODO(step 26): load holding_samples for `wallet` within
        now-holding_hours..now; require >=1 sample at/under the window start and
        that all samples are >= min_balance. Missing early coverage => False
        (cannot prove continuity). Also re-check current balance now.
        """
        _window_start = datetime.now(timezone.utc) - timedelta(hours=holding_hours)
        raise NotImplementedError("continuity check — implemented in step 26")

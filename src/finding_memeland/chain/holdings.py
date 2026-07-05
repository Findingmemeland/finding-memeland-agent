"""On-chain holding checks against Base — exact continuity proof via Transfer logs.

Anti-sniper: a wallet must have held >= the floor across the WHOLE window, not
just at submission time. Instead of sampling balances on a schedule (which would
require knowing every holder in advance and leaves coverage gaps), continuity is
PROVEN on demand, exactly, when a claim arrives:

    1. read the wallet's current balance (balanceOf at latest block)
    2. fetch the wallet's Transfer events INSIDE the window (eth_getLogs)
    3. replay them newest -> oldest, reconstructing the balance at every
       point in the window (balances are piecewise-constant between events)
    4. continuous iff the MINIMUM reconstructed balance >= the floor

No daily job, no indexer, no third-party API — only the RPC. A wallet with no
transfers in the window trivially proves continuity (balance was constant).
Fail-closed: any inconsistency (negative reconstruction => missing logs) denies.
Smart-contract wallets (Safes) are allowed, not just EOAs.

Balances: the public API takes/returns WHOLE tokens (consistent with prize/cap
and the hunt's min_balance_fmml); the replay runs in base units internally.
The web3 client is injected — no top-level web3 import.
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

# keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# eth_getLogs block-range chunk (public RPCs cap the range per call).
LOG_CHUNK_BLOCKS = 9_000

# Safe lower bound when locating the window-start block: Base produces a block
# every ~2s, so going back `window_seconds` BLOCKS overshoots the window by ~2x.
_MIN_SECONDS_PER_BLOCK = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _topic_addr(wallet: str) -> str:
    """Left-pad an address to a 32-byte log topic."""
    return "0x" + "0" * 24 + wallet.lower().replace("0x", "")


def _addr_from_topic(topic) -> str:
    h = topic.hex() if hasattr(topic, "hex") else str(topic)
    if not h.startswith("0x"):
        h = "0x" + h
    return ("0x" + h[-40:]).lower()


class Holdings:
    def __init__(self, *, web3, token_address: str, repo=None, decimals: int = 18, now_fn=_utcnow):
        self._w3 = web3
        self._token_address = token_address
        self._repo = repo  # kept for wiring compat; the proof needs no DB
        self._decimals = decimals
        self._now = now_fn

    # ------------------------------------------------------------------
    def current_balance(self, wallet: str) -> int:
        """Live balance in WHOLE tokens (floored)."""
        return self._balance_base(wallet) // (10 ** self._decimals)

    def _balance_base(self, wallet: str) -> int:
        contract = self._w3.eth.contract(
            address=self._w3.to_checksum_address(self._token_address),
            abi=ERC20_BALANCEOF_ABI,
        )
        return contract.functions.balanceOf(self._w3.to_checksum_address(wallet)).call()

    # ------------------------------------------------------------------
    def has_continuous_holding(self, *, wallet: str, min_balance: int, holding_hours: int) -> bool:
        """True iff `min_balance` (whole tokens) was held at EVERY point of the
        last `holding_hours` — proven from the chain, exact to the block."""
        floor_base = min_balance * (10 ** self._decimals)

        current = self._balance_base(wallet)
        if current < floor_base:
            return False

        window_start_ts = int((self._now() - timedelta(hours=holding_hours)).timestamp())
        latest = self._w3.eth.block_number
        start_block = self._block_at_or_after(window_start_ts, latest)

        events = self._transfer_events(wallet, start_block, latest)

        # Replay newest -> oldest. After undoing an event, `bal` is the balance
        # BEFORE it — i.e. the balance held during the preceding interval. The
        # window minimum is min(current, every pre-event balance), which also
        # covers the balance at window start (before the oldest in-window event).
        bal = current
        min_seen = current
        for _, _, frm, to, value in sorted(events, key=lambda e: (e[0], e[1]), reverse=True):
            if frm == to:
                continue  # self-transfer: no balance change
            if to == wallet.lower():
                bal -= value  # they received it during the window: undo
            elif frm == wallet.lower():
                bal += value  # they sent it during the window: undo
            if bal < 0:
                return False  # inconsistent logs -> cannot prove -> deny
            min_seen = min(min_seen, bal)

        return min_seen >= floor_base

    # ------------------------------------------------------------------
    def _block_at_or_after(self, target_ts: int, latest: int) -> int:
        """First block with timestamp >= target_ts (binary search, ~16 calls)."""
        seconds_back = max(0, int(self._w3.eth.get_block(latest).timestamp) - target_ts)
        lo = max(0, latest - seconds_back // _MIN_SECONDS_PER_BLOCK)
        hi = latest
        while lo < hi:
            mid = (lo + hi) // 2
            if int(self._w3.eth.get_block(mid).timestamp) >= target_ts:
                hi = mid
            else:
                lo = mid + 1
        return lo

    def _transfer_events(self, wallet: str, from_block: int, to_block: int) -> list[tuple]:
        """The wallet's Transfer events in [from_block, to_block], as normalized
        (block, log_index, from_addr, to_addr, value) tuples. Chunked getLogs."""
        token = self._w3.to_checksum_address(self._token_address)
        wtopic = _topic_addr(wallet)
        out: list[tuple] = []
        start = from_block
        while start <= to_block:
            end = min(start + LOG_CHUNK_BLOCKS - 1, to_block)
            for topics in (
                [TRANSFER_TOPIC, wtopic],          # wallet as sender
                [TRANSFER_TOPIC, None, wtopic],    # wallet as receiver
            ):
                for log in self._w3.eth.get_logs({
                    "fromBlock": start, "toBlock": end,
                    "address": token, "topics": topics,
                }):
                    data = log["data"]
                    if hasattr(data, "hex"):
                        data = data.hex()
                    if not str(data).startswith("0x"):
                        data = "0x" + str(data)
                    out.append((
                        int(log["blockNumber"]),
                        int(log["logIndex"]),
                        _addr_from_topic(log["topics"][1]),
                        _addr_from_topic(log["topics"][2]),
                        int(str(data), 16),
                    ))
            start = end + 1
        # A transfer wallet->wallet appears in both queries; dedupe.
        return sorted(set(out), key=lambda e: (e[0], e[1]))

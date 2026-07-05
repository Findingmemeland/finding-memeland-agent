"""Holding continuity — exact proof via Transfer-event replay (P6/P7 redesign).

The wallet must have held >= the floor at EVERY point of the window. We test the
replay math with injected balances/events (no web3 needed).

Event tuples: (block, log_index, from_addr, to_addr, value_base_units).
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from finding_memeland.chain.holdings import Holdings, _topic_addr

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
WALLET = "0x" + "a" * 40
OTHER = "0x" + "b" * 40
D = 10 ** 18  # one whole token in base units


class _Holdings(Holdings):
    """Injects current balance + in-window events; skips RPC entirely."""

    def __init__(self, *, current_whole, events):
        fake_w3 = SimpleNamespace(eth=SimpleNamespace(block_number=1000))
        super().__init__(web3=fake_w3, token_address="0xt", now_fn=lambda: NOW)
        self._current = current_whole * D
        self._events = events

    def _balance_base(self, wallet):
        return self._current

    def _block_at_or_after(self, target_ts, latest):
        return 0

    def _transfer_events(self, wallet, from_block, to_block):
        return self._events


def test_no_transfers_in_window_and_balance_above_floor_passes():
    # The long-term holder: no events in the window => balance was constant.
    # (The OLD sampling design failed this exact case — nobody could win.)
    h = _Holdings(current_whole=100, events=[])
    assert h.has_continuous_holding(wallet=WALLET, min_balance=50, holding_hours=24)


def test_current_below_floor_fails():
    h = _Holdings(current_whole=10, events=[])
    assert not h.has_continuous_holding(wallet=WALLET, min_balance=50, holding_hours=24)


def test_bought_inside_window_fails():
    # Sniper: received 100 tokens mid-window; before that they held 0.
    h = _Holdings(current_whole=100, events=[(500, 1, OTHER, WALLET, 100 * D)])
    assert not h.has_continuous_holding(wallet=WALLET, min_balance=50, holding_hours=24)


def test_dip_below_floor_inside_window_fails():
    # Held 100, sold 80 (balance 20 < floor), bought 80 back before claiming.
    h = _Holdings(current_whole=100, events=[
        (400, 1, WALLET, OTHER, 80 * D),   # sale -> dip to 20
        (600, 1, OTHER, WALLET, 80 * D),   # re-buy -> back to 100
    ])
    assert not h.has_continuous_holding(wallet=WALLET, min_balance=50, holding_hours=24)


def test_topping_up_from_above_floor_passes():
    # Held 60 all along (>= floor), bought 40 more mid-window: min is 60.
    h = _Holdings(current_whole=100, events=[(500, 1, OTHER, WALLET, 40 * D)])
    assert h.has_continuous_holding(wallet=WALLET, min_balance=50, holding_hours=24)


def test_partial_sale_staying_above_floor_passes():
    # Started at 120, sold 20 mid-window: balances 120 -> 100, never below 50.
    h = _Holdings(current_whole=100, events=[(500, 1, WALLET, OTHER, 20 * D)])
    assert h.has_continuous_holding(wallet=WALLET, min_balance=50, holding_hours=24)


def test_self_transfer_is_ignored():
    h = _Holdings(current_whole=100, events=[(500, 1, WALLET, WALLET, 100 * D)])
    assert h.has_continuous_holding(wallet=WALLET, min_balance=50, holding_hours=24)


def test_inconsistent_logs_fail_closed():
    # Undoing an incoming transfer bigger than the balance => negative balance
    # => logs are incomplete; deny rather than guess.
    h = _Holdings(current_whole=100, events=[(500, 1, OTHER, WALLET, 150 * D)])
    assert not h.has_continuous_holding(wallet=WALLET, min_balance=50, holding_hours=24)


def test_replay_uses_event_order_within_block():
    # Same block, two events: order by log_index. Sold 80 (idx 1) then bought
    # 80 back (idx 2). Replayed newest->oldest correctly => dip to 20 detected.
    h = _Holdings(current_whole=100, events=[
        (500, 1, WALLET, OTHER, 80 * D),
        (500, 2, OTHER, WALLET, 80 * D),
    ])
    assert not h.has_continuous_holding(wallet=WALLET, min_balance=50, holding_hours=24)


def test_topic_addr_padding():
    assert _topic_addr(WALLET) == "0x" + "0" * 24 + "a" * 40

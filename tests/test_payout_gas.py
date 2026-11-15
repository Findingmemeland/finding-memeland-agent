"""A margem de gás do payout — pedido do Pedro a 15/11, em cima do builder code.

O limite de gás era a estimativa exacta, e o payout da #34 correu com 378 de
folga. Passa a ser a estimativa — feita com o calldata final, o sufixo
incluído — mais 15%, arredondada para cima.

MEDIDO NA BASE a 15/11 (RPC público, nada assinado): o payout é uma transacção
legacy (gasPrice, sem maxFeePerGas) e a Base cobra o gás USADO — o ETH da
carteira caiu, no bloco da #34, exactamente usado × preço + a taxa de L1, não
limite × preço. A margem não mexe no custo. O mínimo do preflight (MIN_GAS_ETH
= 0,0002) cobre o limite com a margem até ~2,78 gwei; o preço era 0,006.

A cadeia aqui é falsa e local; a chave é sintética (sem fundos).
"""
from __future__ import annotations

import math

import pytest
import rlp
from test_builder_code import TEST_KEY, TOKEN, WINNER, FakeBase
from web3 import Web3

from finding_memeland.chain.payout import GAS_MARGIN_PCT, PayoutEngine, gas_limit_for
from finding_memeland.preflight import MIN_GAS_ETH


class Recording(FakeBase):
    """Every RPC asked, in order; the estimate fixed when `estimate` is set."""

    def __init__(self, estimate: int | None = None):
        super().__init__()
        self.methods: list[str] = []
        self._fixed = estimate

    def make_request(self, method, params):
        self.methods.append(method)
        if method == "eth_estimateGas" and self._fixed is not None:
            self.estimates.append(params[0]["data"])
            return {"jsonrpc": "2.0", "id": 1, "result": hex(self._fixed)}
        return super().make_request(method, params)


def _send(chain: Recording, *, amount: int = 20_000_000, cap: int = 1_000_000_000):
    engine = PayoutEngine(web3=Web3(chain), token_address=TOKEN, hot_wallet_key=TEST_KEY,
                          per_hunt_cap=cap, decimals=18)
    engine.send_prize(hunt_id=34, to_wallet=WINNER, amount_fmml=amount)
    nonce, gas_price, gas, to, value, data, v, r, s = rlp.decode(chain.sent[0])
    return int.from_bytes(gas, "big"), int.from_bytes(gas_price, "big")


@pytest.mark.parametrize("estimate, limit", [
    (100, 115), (101, 117),                 # 116.15 → 117: up, never down
    (44_633, 51_328),                       # the #34 estimate: 51 327.95 → 51 328
    (62_641, 72_038),                       # the worst measured on 15/11
    (100_000, 115_000),                     # exact stays exact
])
def test_the_limit_is_the_estimate_plus_15_percent_rounded_up(estimate, limit):
    assert GAS_MARGIN_PCT == 15
    assert gas_limit_for(estimate) == limit == math.ceil(estimate * 115 / 100)


@pytest.mark.parametrize("estimate", [44_633, 62_641, 21_001])
def test_the_limit_sent_is_the_estimate_plus_the_margin(estimate):
    gas, _price = _send(Recording(estimate=estimate))
    assert gas == gas_limit_for(estimate) > estimate


def test_the_margin_is_on_the_estimate_of_the_final_calldata():
    chain = Recording()                                  # gas follows the calldata
    gas, _price = _send(chain)
    final = chain.estimates[-1]
    assert final.endswith("62635f6c637269707a71750b0080218021802180218021802180218021")
    raw = bytes.fromhex(final[2:])
    assert gas == gas_limit_for(44_000 + sum(16 if b else 4 for b in raw))


def test_the_margin_never_touches_the_price():
    gas, price = _send(Recording(estimate=44_633))
    assert price == 5_000_000                            # eth_gasPrice of the fake chain
    assert gas == 51_328


def test_the_cap_per_hunt_is_still_checked_first():
    chain = Recording(estimate=44_633)
    with pytest.raises(ValueError, match="exceeds hardcoded per-hunt cap"):
        _send(chain, amount=101, cap=100)
    assert chain.methods == [] and chain.sent == []      # not one RPC, not one estimate


def test_the_preflight_minimum_covers_the_limit_with_the_margin():
    """Medido a 15/11: pior estimativa 62 641 (já com o sufixo), preço 0,006
    gwei, taxa de L1 da #34 ~0,0000000009 ETH. O mínimo não muda."""
    assert MIN_GAS_ETH == 0.0002
    limit = gas_limit_for(62_641)
    l1_fee_wei = 900_000_000                             # ~0.0000000009 ETH
    assert limit * 6_000_000 + l1_fee_wei < MIN_GAS_ETH * 1e18             # today: 0.006 gwei
    assert limit * 2_770_000_000 + l1_fee_wei < MIN_GAS_ETH * 1e18         # up to ~2.77 gwei
    assert limit * 2_790_000_000 + l1_fee_wei > MIN_GAS_ETH * 1e18         # not at 2.79

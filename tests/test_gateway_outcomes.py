"""As falhas do gateway da metadata, por causa (30/09).

O /harvest de 29/09 mostrou "rotação de gateways salvou 0" — e a razão era
de configuração: TARGET_IPFS_GATEWAYS tem um só URL, igual ao nosso. Com um
gateway só não há rotação, e o que falta saber é PORQUE é que ele falha: 14
candidatos de Base caíram na releitura de metadata que o mesmo gateway tinha
servido minutos antes. Um 429 (limitados) e um timeout (lento) pedem
respostas diferentes. Isto só mede: nenhuma decisão muda.
"""
from __future__ import annotations

import http.client
import urllib.error

import pytest
from test_harvest_command import _Harv, _Store
from test_metadata_failover import DOC, GW0, GW1, OURS, A, Gateways, _meta
from test_target_prepare import World, _finder

from finding_memeland.target.adapters import FailoverMetadata, _failure_kind
from finding_memeland.target.sources import GatewayUnavailable
from finding_memeland.target.wiring import TargetWiring

# --------------------------------------------------------------------------- #
# 1. Cada pedido conta, por gateway e por resultado                             #
# --------------------------------------------------------------------------- #


def test_every_request_is_counted_by_gateway_and_outcome():
    gw = Gateways(ours=TimeoutError(), gw0=429, gw1=DOC)
    m = _meta(gw)
    m.read("ethereum", A, 1)
    assert m.stats["outcomes"] == {1: {"timeout": 1}, 2: {"429": 1}, 3: {"serviu": 1}}


def test_a_throttle_page_is_counted_as_not_json():
    gw = Gateways(ours="<html>slow down</html>", gw0=DOC)
    m = _meta(gw)
    m.read("ethereum", A, 1)
    assert m.stats["outcomes"][1] == {"não-json": 1}


def test_counts_add_up_across_reads():
    gw = Gateways(ours=503)
    m = _meta(gw, gateways=(OURS,))
    for _ in range(3):
        with pytest.raises(GatewayUnavailable):
            m.read("ethereum", A, 1)
    assert m.stats["outcomes"] == {1: {"503": 3}}


@pytest.mark.parametrize("exc, kind", [
    (urllib.error.HTTPError("u", 429, "x", {}, None), "429"),
    (urllib.error.HTTPError("u", 504, "x", {}, None), "504"),
    (TimeoutError("read"), "timeout"),
    (urllib.error.URLError(TimeoutError("connect")), "timeout"),
    (urllib.error.URLError("name or service not known"), "ligação"),
    (ConnectionResetError("reset"), "ligação"),
    (http.client.IncompleteRead(b"", 10), "ligação"),
    (ValueError("adapter"), "outro"),
])
def test_failure_kinds_in_the_operator_s_words(exc, kind):
    assert _failure_kind(exc) == kind


def test_the_gateway_count_says_when_there_is_nothing_to_rotate_to():
    """A configuração de 29/09: a lista repete o nosso gateway."""
    m = FailoverMetadata(rpcs={}, gateways=[OURS, OURS], http_get=lambda u, h: "")
    assert m.gateway_count == 1
    assert _meta(Gateways(), gateways=(OURS, GW0, GW1)).gateway_count == 3


# --------------------------------------------------------------------------- #
# 2. A linha do /harvest                                                        #
# --------------------------------------------------------------------------- #


class _Reader:
    """Um leitor de mentira que a colheita e o depósito fazem mexer."""

    gateway_count = 1

    def __init__(self):
        self.stats = {"rescued": 0, "outcomes": {}}

    def note(self, pos, kind, n=1):
        by = self.stats["outcomes"].setdefault(pos, {})
        by[kind] = by.get(kind, 0) + n


def _line(reader, *, in_harvest, in_deposit):
    class _H(_Harv):
        def harvest(self, n, **kw):
            in_harvest(reader)
            return super().harvest(n, **kw)

    world = World()
    inner = world.read_token

    def read_token(chain, contract, tid):
        in_deposit(reader)
        return inner(chain, contract, tid)
    world.read_token = read_token
    tw = TargetWiring(
        ports=None, pipeline=None, epoch=None, snapshot_store=None,
        scan_blocks=0, writability_rates={}, uniqueness_rates={},
        finder=_finder(world), larder_store=_Store(),
        harvesters={"ethereum": _H([f"ethereum:{A}:1"])},
        deposit_chains=frozenset({"ethereum"}), larder_meta=reader)
    return tw.harvest(200)


def test_the_line_splits_our_gateway_s_outcomes_by_stage():
    def harvest(r):
        r.note(1, "serviu", 150)
        r.note(1, "timeout", 30)
        r.note(1, "429", 8)

    def deposit(r):
        r.note(1, "429")
    out = _line(_Reader(), in_harvest=harvest, in_deposit=deposit)
    assert "salvou 0 na colheita e 0 no depósito (1 gateway disponível)" in out, out
    assert "gateway 1 — colheita: serviu 150, timeout 30, 429 8; depósito: 429 1" in out, out


def test_a_stage_without_requests_is_left_out():
    out = _line(_Reader(), in_harvest=lambda r: r.note(1, "serviu", 5),
                in_deposit=lambda r: None)
    assert "gateway 1 — colheita: serviu 5" in out, out
    assert "depósito: serviu" not in out and "; depósito" not in out, out


def test_each_gateway_gets_its_own_part():
    reader = _Reader()
    reader.gateway_count = 2

    def harvest(r):
        r.note(1, "timeout", 4)
        r.note(2, "serviu", 3)
        r.note(2, "403", 1)
        r.stats["rescued"] += 3
    out = _line(reader, in_harvest=harvest, in_deposit=lambda r: None)
    assert "salvou 3 na colheita e 0 no depósito (2 gateways disponíveis)" in out, out
    assert "gateway 1 — colheita: timeout 4 · gateway 2 — colheita: serviu 3, 403 1" in out, out


def test_the_line_never_carries_a_gateway_url():
    out = _line(_Reader(), in_harvest=lambda r: r.note(1, "timeout"),
                in_deposit=lambda r: None)
    assert "http" not in out and "ipfs/" not in out, out

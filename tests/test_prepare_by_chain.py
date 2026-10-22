"""`/prepare <cadeia>` (22/10) — a hunt especial do Base Meme Summit tem de
ter um alvo de Base.

As decisões do Pedro:
  · `/prepare base` sorteia só dessa cadeia, e tudo o resto fica como está:
    uniforme por contrato, evitar o contrato da hunt anterior (suave, como
    sempre: se for o único dessa cadeia, repete-se), as cinco verificações
    e as guardas;
  · sem argumento, o /prepare fica exactamente como está;
  · sem alvos dessa cadeia, recusa com uma mensagem clara — nunca cai para
    outra cadeia.
"""
from __future__ import annotations

import inspect
import random
from collections import Counter

import pytest
from test_avoid_survives_rejection import _FirstOfPool
from test_target_prepare import World, _finder, _preparer

from finding_memeland.target.prepare import (
    PREPARE_USAGE,
    Candidate,
    Larder,
    PrepareRefused,
    parse_prepare_args,
)

E1, E2 = "0x" + "e1" * 20, "0x" + "e2" * 20
B1, B2 = "0x" + "b1" * 20, "0x" + "b2" * 20


def cand(chain: str, contract: str, tid: int) -> Candidate:
    meta = {"name": "Some Two Words", "image": f"ipfs://img{tid}", "description": "d"}
    return Candidate(chain=chain, contract=contract, token_id=tid,
                     name="Some Two Words", name_onchain="Some Two Words",
                     description="d", image=f"ipfs://img{tid}",
                     token_uri=f"ipfs://Qm{tid}", artist="", metadata=meta)


def mixed(used=()) -> Larder:
    """Ethereum com mais peças e mais contratos; Base com dois contratos de
    tamanhos diferentes (B1 três peças, B2 uma)."""
    return Larder(candidates=[cand("ethereum", E1, t) for t in range(1, 6)]
                  + [cand("ethereum", E2, 6)]
                  + [cand("base", B1, t) for t in (7, 8, 9)]
                  + [cand("base", B2, 10)],
                  used=list(used))


class _LastOfPool:
    """O último do balde. Numa despensa mista e sem filtro sai Ethereum: os
    contratos ordenam-se e "ethereum:" vem depois de "base:"."""

    def randrange(self, n):
        return n - 1

    def choice(self, seq):
        return seq[-1]


# --------------------------------------------------------------------------- #
# 1. O argumento                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("arg, chain", [
    ("", None), ("   ", None), ("base", "base"), ("BASE", "base"),
    (" Ethereum ", "ethereum"),
])
def test_the_argument_is_a_chain_or_nothing(arg, chain):
    assert parse_prepare_args(arg) == chain


@pytest.mark.parametrize("arg", [
    "bsae", "polygon", "base base", "base ethereum", "200", "base 2", "manifold",
])
def test_anything_else_is_the_usage(arg):
    with pytest.raises(ValueError) as e:
        parse_prepare_args(arg)
    assert str(e.value) == PREPARE_USAGE == (
        "usage: /prepare [cadeia] — cadeias: ethereum, base (sem cadeia: todas)")


def test_main_routes_both_forms_and_the_usage_before_any_job():
    """Sem argumento, a MESMA chamada de sempre; com cadeia, a cadeia; o uso
    responde antes do `_target_job` — nada corre."""
    from finding_memeland import main
    from finding_memeland.telegram.approval_queue import TELEGRAM_COMMANDS
    src = inspect.getsource(main.build_agent)
    body = src[src.index("def _prepare("):src.index("actions = {")]
    assert body.index("chain = parse_prepare_args(arg)") < body.index("_target_job(")
    assert "return str(e)" in body
    assert '_target_job("prepare", lambda: target_wiring.prepare())' in body
    assert '_target_job("prepare", lambda: target_wiring.prepare(chain))' in body
    assert '"prepare": _prepare' in src and "prepare" in TELEGRAM_COMMANDS


# --------------------------------------------------------------------------- #
# 2. O sorteio                                                                  #
# --------------------------------------------------------------------------- #


def test_take_with_a_chain_never_leaves_it():
    lar, rng = mixed(), random.Random(22)
    assert {lar.take(rng, chain="base").chain for _ in range(400)} == {"base"}
    assert {lar.take(rng, chain="ethereum").chain for _ in range(400)} == {"ethereum"}


def test_take_with_a_chain_is_still_uniform_by_contract():
    """B1 tem três peças e B2 uma: por candidato daria 75/25; por contrato,
    metade cada."""
    lar, rng, n = mixed(), random.Random(22), 4000
    by = Counter(lar.take(rng, chain="base").contract for _ in range(n))
    assert 0.45 < by[B2] / n < 0.55


def test_the_previous_contract_is_avoided_inside_the_chain():
    lar, rng = mixed(), random.Random(22)
    got = {lar.take(rng, chain="base", avoid_contract=f"base:{B1}").contract
           for _ in range(200)}
    assert got == {B2}


def test_when_the_previous_contract_is_the_only_one_of_the_chain_it_repeats():
    """A regra de 09/09 ("antes repetir do que não haver hunt"), dentro da
    cadeia — nunca resolvida a ir buscar outra."""
    lar, rng = Larder(candidates=[cand("ethereum", E1, 1), cand("base", B1, 2)]), random.Random(22)
    got = {lar.take(rng, chain="base", avoid_contract=f"base:{B1}").id() for _ in range(50)}
    assert got == {f"base:{B1}:2"}


def test_no_candidate_of_the_chain_is_none_never_another_chain():
    lar = Larder(candidates=[cand("ethereum", E1, 1), cand("ethereum", E2, 2)])
    assert lar.take(random.Random(1), chain="base") is None
    assert lar.take(random.Random(1), chain="base", avoid_contract=f"ethereum:{E1}") is None
    assert lar.count_chain("base") == 0 and lar.count_chain("ethereum") == 2


def _take_before_22_10(lar: Larder, rng, avoid_contract: str = ""):
    """O `Larder.take` do HEAD de 22/10, copiado tal e qual."""
    if not lar.candidates:
        return None
    pool = lar.candidates
    if avoid_contract:
        other = [c for c in lar.candidates
                 if f"{c.chain}:{c.contract.lower()}" != avoid_contract]
        if other:
            pool = other
    by_contract: dict[str, list] = {}
    for c in pool:
        by_contract.setdefault(f"{c.chain}:{c.contract.lower()}", []).append(c)
    keys = sorted(by_contract)
    group = by_contract[keys[rng.randrange(len(keys))]]
    return group[rng.randrange(len(group))]


@pytest.mark.parametrize("avoid", ["", f"base:{B1}", f"ethereum:{E1}", f"ethereum:{E2}"])
def test_without_a_chain_take_is_exactly_what_it_was(avoid):
    for seed in range(200):
        lar = mixed()
        new = lar.take(random.Random(seed), avoid_contract=avoid)
        old = _take_before_22_10(lar, random.Random(seed), avoid)
        assert new.id() == old.id(), seed
    assert Larder().take(random.Random(1)) is None


# --------------------------------------------------------------------------- #
# 3. O /prepare                                                                 #
# --------------------------------------------------------------------------- #


def test_no_target_of_the_chain_refuses_before_any_read():
    """A despensa fica intacta: nem a lista de expostos é lida."""
    world, seen = World(), []
    finder = _finder(world, is_exposed=lambda contract, tid: seen.append(1) or False)
    lar = Larder(candidates=[cand("ethereum", E1, 1), cand("ethereum", E2, 2)],
                 used=[f"ethereum:{E1}:999"])
    before = (list(lar.candidates), list(lar.used))
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, finder).prepare(lar, chain="base")
    assert str(e.value) == (
        "não há alvos de base na despensa (despensa 2: ethereum 2) — nada foi "
        "sorteado; nunca tiro de outra cadeia. Corre /harvest base.")
    assert (lar.candidates, lar.used) == before
    assert seen == [] and world.probes == [] and world.full_reads == []
    assert world.paid_calls == 0


def test_the_clean_up_taking_the_last_one_of_the_chain_is_the_same_refusal():
    """A limpeza (alvos expostos, 09/10) corre antes do sorteio e pode levar
    o último alvo da cadeia: a resposta é a mesma recusa clara, não um
    "esgotados ao fim de 0 tentativas"."""
    world = World()
    lar = Larder(candidates=[cand("ethereum", E1, 1), cand("base", B1, 2)])
    finder = _finder(world, is_exposed=lambda contract, tid: contract == B1)
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, finder).prepare(lar, chain="base")
    assert str(e.value).startswith(
        "não há alvos de base na despensa (despensa 1: ethereum 1) — nada foi sorteado")
    assert [c.chain for c in lar.candidates] == ["ethereum"]


@pytest.mark.parametrize("rng, chain, unfiltered", [
    (_LastOfPool, "base", "ethereum"),
    (_FirstOfPool, "ethereum", "base"),
])
def test_the_sealed_target_is_of_the_chain_asked(rng, chain, unfiltered):
    """O mesmo sorteio, sem cadeia, daria a outra — o filtro é que decide."""
    world = World()
    prep = _preparer(world, _finder(world))
    prep._rng = rng()
    prepared, left = prep.prepare(mixed(), chain=chain)
    assert prepared.target.chain == chain
    assert left.size() == mixed().size() - 1
    plain = _preparer(world, _finder(world))
    plain._rng = rng()
    assert plain.prepare(mixed())[0].target.chain == unfiltered


def test_when_the_chain_runs_out_mid_run_it_refuses_and_never_crosses():
    """As quatro peças de Base têm a imagem morta: saem as quatro, e o
    /prepare recusa — com as de Ethereum todas ainda na despensa."""
    world = World(dead={"ipfs://img7", "ipfs://img8", "ipfs://img9", "ipfs://img10"})
    lar = mixed()
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, _finder(world)).prepare(lar, chain="base")
    msg = str(e.value)
    assert msg.startswith("alvos de base esgotados ao fim de 4 tentativa(s) — ")
    assert msg.endswith("; nunca tiro de outra cadeia. Corre /harvest base.")
    assert lar.count_chain("ethereum") == 6 and lar.count_chain("base") == 0


def _after_b1(b2_tids) -> Larder:
    """A hunt anterior saiu do B1, em Base."""
    return Larder(candidates=[cand("ethereum", E1, 1)]
                  + [cand("base", B1, t) for t in (7, 8, 9)]
                  + [cand("base", B2, t) for t in b2_tids],
                  used=[f"base:{B1}:999"])


def test_the_previous_contract_stays_avoided_inside_the_chain_after_a_rejection():
    """A guarda da #14 (o contrato a evitar calcula-se uma vez), dentro da
    cadeia: o primeiro do B2 morre e o selado continua fora do B1."""
    world = World(dead={"ipfs://img10"})
    prep = _preparer(world, _finder(world))
    prep._rng = _FirstOfPool()
    prepared, _left = prep.prepare(_after_b1((10, 11)), chain="base")
    assert prepared.target.id() == f"base:{B2}:11"


def test_with_only_the_previous_contract_left_in_the_chain_it_repeats_it():
    """O B2 morre e só resta o B1 em Base: repete-se o B1 — nunca o
    Ethereum que está ali ao lado."""
    world = World(dead={"ipfs://img10"})
    prep = _preparer(world, _finder(world))
    prep._rng = _FirstOfPool()
    prepared, _left = prep.prepare(_after_b1((10,)), chain="base")
    assert (prepared.target.chain, prepared.target.contract) == ("base", B1)


# --------------------------------------------------------------------------- #
# 4. A ligação ao bot                                                           #
# --------------------------------------------------------------------------- #


class _Store:
    def __init__(self):
        self.saved = []

    def load(self):
        return Larder()

    def save(self, obj):
        self.saved.append(obj)


class _SpyPreparer:
    """Regista com que argumentos o /prepare foi chamado."""

    def __init__(self, refuse: bool):
        self.calls: list[dict] = []
        self.refuse = refuse

    def prepare(self, larder, **kw):
        self.calls.append(kw)
        if self.refuse:
            raise PrepareRefused("não há alvos de base na despensa")
        return type("P", (), {"attempts": 1})(), larder


def _wiring(preparer):
    from finding_memeland.target.wiring import TargetWiring
    return TargetWiring(ports=None, pipeline=None, epoch=None, snapshot_store=None,
                        scan_blocks=0, writability_rates={}, uniqueness_rates={},
                        larder_store=_Store(), prepared_store=_Store(),
                        larder_preparer=preparer)


def test_the_wiring_passes_the_chain_and_nothing_without_one():
    spy = _SpyPreparer(refuse=True)
    w = _wiring(spy)
    assert w.prepare() == "⛔ prepare recusado — não há alvos de base na despensa"
    assert w.prepare("base") == "⛔ prepare recusado — não há alvos de base na despensa"
    assert spy.calls == [{}, {"chain": "base"}]


def test_the_sealed_reply_never_names_the_chain():
    """O alvo selado nunca se revela — nem a cadeia: confirma-se o pedido no
    /status, onde a contagem dessa cadeia desce."""
    w = _wiring(_SpyPreparer(refuse=False))
    out = w.prepare("base")
    assert out.startswith("prepare: alvo selado à 1.ª tentativa · despensa 0 · válido ")
    assert "base" not in out and "ethereum" not in out

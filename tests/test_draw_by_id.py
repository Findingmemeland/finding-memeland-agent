"""Sortear por id, para fontes que não enumeram.

O PORQUÊ (22/09). Os hunts #12 e #13 saíram do MESMO contrato partilhado do
SuperRare. O contador novo da despensa mostrou a causa — 25 alvos, 2
contratos, tudo Ethereum — e a causa da causa estava no SOURCES: duas
fontes, porque exigíamos ERC721Enumerable e quase ninguém o implementa.

A classe Source dizia, por escrito, que sondar ids às cegas era "a
complexidade que esta reescrita existe para apagar". Continua a ser
complexidade; o que mudou foi a medição. A sonda mostrou o superrare1 a
devolver tokenURI em 3/3 ids ao calhas — ids densos — e o custo real é uma
leitura falhada de vez em quando, fora do relógio, no /fill.

O que estes testes fixam: o modo `index` não muda NADA (é o caminho que
correu treze hunts), o modo `id` não chama tokenByIndex, e o id sorteado
cai sempre dentro do intervalo que a cadeia declarou.
"""
from __future__ import annotations

import random

from finding_memeland.target.prepare import (
    DRAW_ID,
    DRAW_INDEX,
    SOURCES,
    Source,
    TargetFinder,
)

ENUM = Source("enum", "ethereum", "0x" + "aa" * 20)
BYID = Source("byid", "ethereum", "0x" + "bb" * 20, draw=DRAW_ID)


def _finder(sources, *, total=1000, seed=0, index_raises=False):
    calls: dict[str, int] = {"index": 0, "total": 0}

    def total_supply(chain, contract):
        calls["total"] += 1
        return total

    def token_by_index(chain, contract, i):
        calls["index"] += 1
        if index_raises:
            raise RuntimeError("tokenByIndex reverts")
        return 500_000 + i          # id distinto do índice, de propósito

    f = TargetFinder(
        sources=sources, total_supply=total_supply, token_by_index=token_by_index,
        read_token=lambda *a: None, probe_image=lambda u: None,
        owner_is_eoa=lambda *a: True, name_is_unique=lambda *a: True,
        rng=random.Random(seed))
    return f, calls


# --------------------------------------------------------------------------- #
# O caminho antigo não muda                                                     #
# --------------------------------------------------------------------------- #


def test_index_mode_is_untouched():
    """Treze hunts correram por aqui. O modo novo não lhes pode tocar."""
    f, calls = _finder([ENUM])
    src, tid = f.draw()
    assert src is ENUM
    assert calls["index"] == 1
    assert tid >= 500_000, "o id veio do tokenByIndex, não do gerador"


def test_index_mode_still_gives_up_on_a_hole():
    """Um buraco no índice continua a devolver None para se sortear outra
    vez — nunca um token inventado."""
    f, _ = _finder([ENUM], index_raises=True)
    assert f.draw() is None


def test_the_default_is_index():
    """Uma fonte escrita sem `draw=` comporta-se como sempre se comportou."""
    assert Source("x", "ethereum", "0x" + "cc" * 20).draw == DRAW_INDEX


# --------------------------------------------------------------------------- #
# O modo novo                                                                   #
# --------------------------------------------------------------------------- #


def test_id_mode_never_asks_for_the_index():
    """É esse o ponto: o contrato não sabe responder a tokenByIndex."""
    f, calls = _finder([BYID])
    src, tid = f.draw()
    assert src is BYID
    assert calls["index"] == 0
    assert isinstance(tid, int)


def test_the_drawn_id_stays_inside_what_the_chain_declared():
    """Sortear fora do intervalo seria garantir leituras falhadas."""
    f, _ = _finder([BYID], total=50)
    for _ in range(200):
        _src, tid = f.draw()
        assert 1 <= tid <= 50


def test_ids_start_at_one_not_zero():
    """A esmagadora maioria destes contratos começa em 1; um zero é uma
    leitura queimada em cada sorteio."""
    f, _ = _finder([BYID], total=3)
    assert all(f.draw()[1] >= 1 for _ in range(100))


def test_a_tiny_source_does_not_break_the_range():
    """totalSupply == 1 não pode produzir randrange(1, 1)."""
    f, _ = _finder([BYID], total=1)
    assert f.draw()[1] in (1, 2)


def test_both_modes_coexist_and_size_no_longer_decides():
    """Os dois modos convivem no mesmo sorteio, e o TAMANHO da fonte já não
    manda nada (22/09).

    Esta asserção era a inversa: uma fonte de 100k tinha de sair muito mais
    vezes do que uma de 100. Era essa regra que tornava inútil acrescentar
    fontes pequenas — e o fim dela é metade da correcção da variedade. O
    que se mantém é o resto: cada fonte é lida pelo seu próprio modo."""
    calls = {"index": 0}

    def total_supply(chain, contract):
        return 100_000 if contract == ENUM.contract else 100

    def token_by_index(chain, contract, i):
        calls["index"] += 1
        return 900_000 + i

    f = TargetFinder(
        sources=[ENUM, BYID], total_supply=total_supply,
        token_by_index=token_by_index, read_token=lambda *a: None,
        probe_image=lambda u: None, owner_is_eoa=lambda *a: True,
        name_is_unique=lambda *a: True, rng=random.Random(7))
    got = [f.draw()[0].slug for _ in range(400)]
    assert 150 < got.count("enum") < 250, got.count("enum")
    assert 150 < got.count("byid") < 250, got.count("byid")
    assert calls["index"] == got.count("enum"), "só o modo index vai ao índice"


# --------------------------------------------------------------------------- #
# O que ficou no SOURCES de produção                                            #
# --------------------------------------------------------------------------- #


def test_production_now_has_three_sources_and_one_of_them_draws_by_id():
    """Dois contratos era o que fazia os hunts #12 e #13 rimarem."""
    assert len(SOURCES) == 3
    modes = {s.slug: s.draw for s in SOURCES}
    assert modes["foundation"] == DRAW_INDEX
    assert modes["superrare2"] == DRAW_INDEX
    assert modes["superrare1"] == DRAW_ID


def test_production_sources_are_still_all_distinct_contracts():
    seen = {(s.chain, s.contract.lower()) for s in SOURCES}
    assert len(seen) == len(SOURCES)

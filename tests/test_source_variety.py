"""Variedade: uniforme entre fontes, e nunca o contrato do hunt anterior.

HUNTS #12 e #13 (18 e 22/09) saíram do MESMO contrato partilhado do
SuperRare; só mudou o tokenId. Nenhuma pista deu isso — e a meio da #13 já
havia dois jogadores a varrer o contrato da #12, porque quem repara deixa
de procurar "em qualquer NFT onchain" e passa a procurar num contrato só.

Duas causas, duas correcções:

1. O SORTEIO ERA PROPORCIONAL AO TAMANHO. Com 116k, 51k e 4,4k peças, a
   terceira fonte apareceria em 2,6% dos sorteios: acrescentá-la teria sido
   decorativo. A pergunta do jogo não é "que peça entre todas as peças" —
   é "de que contrato sai o próximo alvo". Passa a uniforme entre fontes.

2. O /PREPARE NÃO OLHAVA PARA TRÁS. Agora evita o contrato do último alvo
   consumido, que a própria despensa conhece — `used` é append-only e já
   está cifrado, portanto não é preciso coluna nova nem mais um sítio onde
   um id de alvo possa vazar.

E o limite que não se ultrapassa: A HUNT TEM DE CORRER (Pedro, 09/09). Se
evitar o contrato anterior esvaziar o balde, o filtro cai. Preferir repetir
um contrato a não haver hunt.
"""
from __future__ import annotations

import random

from finding_memeland.target.prepare import Candidate, Larder, Source, TargetFinder

A = "0x" + "aa" * 20
B = "0x" + "bb" * 20
C = "0x" + "cc" * 20


def _cand(contract: str, tid: int, chain: str = "ethereum") -> Candidate:
    return Candidate(
        chain=chain, contract=contract, token_id=tid, name="Two Words",
        name_onchain="Two Words", description="d", image="ipfs://x",
        token_uri="ipfs://y", artist="", metadata={})


# --------------------------------------------------------------------------- #
# 1. Uniforme entre fontes                                                      #
# --------------------------------------------------------------------------- #


def test_a_tiny_source_is_drawn_as_often_as_a_huge_one():
    """O TESTE. Com ponderação por tamanho, a fonte de 4k aparecia em 2,6%
    dos sorteios e a despensa continuava a ser dois contratos."""
    sizes = {"big": 116_168, "mid": 50_922, "small": 4_436}
    srcs = [Source("big", "ethereum", A), Source("mid", "ethereum", B),
            Source("small", "ethereum", C)]
    f = TargetFinder(
        sources=srcs, total_supply=lambda c, k: sizes[
            {A: "big", B: "mid", C: "small"}[k]],
        token_by_index=lambda c, k, i: i + 1, read_token=lambda *a: None,
        probe_image=lambda u: None, owner_is_eoa=lambda *a: True,
        name_is_unique=lambda *a: True, rng=random.Random(11))
    got = [f.draw()[0].slug for _ in range(900)]
    for slug in sizes:
        assert 250 < got.count(slug) < 350, (slug, got.count(slug))


def test_the_draw_is_stable_under_source_order():
    """A ordem em que as fontes estão escritas não pode mudar as
    probabilidades — senão reordenar o SOURCES é uma alteração de jogo."""
    srcs = [Source("x", "ethereum", A), Source("y", "ethereum", B)]
    def finder(order, seed):
        return TargetFinder(
            sources=order, total_supply=lambda c, k: 1000,
            token_by_index=lambda c, k, i: i + 1, read_token=lambda *a: None,
            probe_image=lambda u: None, owner_is_eoa=lambda *a: True,
            name_is_unique=lambda *a: True, rng=random.Random(seed))
    a = [finder(srcs, 4).draw()[0].slug for _ in range(200)]
    b = [finder(list(reversed(srcs)), 4).draw()[0].slug for _ in range(200)]
    assert a == b


# --------------------------------------------------------------------------- #
# 2. Nunca o contrato do hunt anterior                                          #
# --------------------------------------------------------------------------- #


def test_the_previous_contract_is_avoided():
    """Exactamente o que aconteceu entre a #12 e a #13."""
    lar = Larder(candidates=[_cand(A, 1), _cand(A, 2), _cand(B, 9)],
                 used=[f"ethereum:{A}:11385"])
    rng = random.Random(0)
    for _ in range(50):
        assert lar.take(rng, avoid_contract=lar.last_contract()).contract == B


def test_the_hunt_runs_even_when_avoiding_would_empty_the_larder():
    """A regra que manda em todas as outras: preferir repetir a não haver
    hunt. Uma guarda de variedade não pode deixar o jogo sem alvo."""
    lar = Larder(candidates=[_cand(A, 1), _cand(A, 2)],
                 used=[f"ethereum:{A}:11385"])
    got = lar.take(random.Random(0), avoid_contract=lar.last_contract())
    assert got is not None and got.contract == A


def test_the_same_contract_on_another_chain_is_not_the_same_contract():
    """Mesmos bytes, cadeia diferente: alvo distinto, e o jogador tem de
    descobrir a cadeia, que as pistas nunca dizem."""
    lar = Larder(candidates=[_cand(A, 1, chain="base")],
                 used=[f"ethereum:{A}:11385"])
    assert lar.take(random.Random(0),
                    avoid_contract=lar.last_contract()) is not None


def test_last_contract_is_empty_on_a_fresh_larder():
    """Primeira hunt: não há nada para evitar, e o filtro não pode inventar
    uma restrição."""
    lar = Larder(candidates=[_cand(A, 1)])
    assert lar.last_contract() == ""
    assert lar.take(random.Random(0), avoid_contract="") is not None


def test_a_malformed_used_entry_never_blocks_the_draw():
    """Se o histórico vier estranho, a resposta é sortear de tudo — nunca
    recusar. Fail-open, porque a hunt tem de correr."""
    lar = Larder(candidates=[_cand(A, 1)], used=["lixo"])
    assert lar.last_contract() == ""
    assert lar.take(random.Random(0), avoid_contract=lar.last_contract()) is not None


def test_casing_does_not_defeat_the_filter():
    """Um endereço em maiúsculas no histórico não pode passar por contrato
    diferente — seria a variedade a falhar exactamente onde parece funcionar."""
    lar = Larder(candidates=[_cand(A, 1), _cand(B, 2)],
                 used=[f"ethereum:{A.upper()}:11385"])
    rng = random.Random(0)
    assert all(lar.take(rng, avoid_contract=lar.last_contract()).contract == B
               for _ in range(30))


def test_an_empty_larder_still_returns_none():
    assert Larder().take(random.Random(0), avoid_contract="x:y") is None

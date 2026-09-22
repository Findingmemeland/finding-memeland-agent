"""A despensa mostra a FORMA, não só o tamanho — e continua sem nomes.

HUNTS #12 e #13 (18 e 22/09) saíram do MESMO contrato partilhado do
SuperRare, na mesma cadeia; só mudou o tokenId. Nenhuma pista deu isso:
foi a despensa. E ninguém — nem o operador — tinha como ver o padrão antes
de ele acontecer duas vezes, porque o /status só dizia quantos alvos havia.

Quem repara deixa de procurar "em qualquer NFT onchain" e passa a procurar
num contrato só. A meio da #13 já havia dois jogadores a varrer o contrato
da #12.

Antes de mexer na amostragem é preciso medir: se a despensa já estiver
concentrada, excluir os últimos três contratos faz o /prepare recusar quase
tudo, e a ordem certa passa a ser outra. Esta é a medição.
"""
from __future__ import annotations

from finding_memeland.target.prepare import Candidate, Larder

SR = "0xb932a70a57673d89f4acffbe830e8ed7f75fb9e0"   # SuperRare partilhado


def _cand(chain: str, contract: str, tid: int,
          name: str = "Placeholder") -> Candidate:
    return Candidate(
        chain=chain, contract=contract, token_id=tid,
        name=name, name_onchain=name, description="d", image="ipfs://x",
        token_uri="ipfs://y", artist="", metadata={"name": name})


def test_the_real_shape_of_hunts_12_and_13():
    """Dois alvos, um contrato: exactamente o que aconteceu."""
    lar = Larder(candidates=[_cand("ethereum", SR, 11385),
                             _cand("ethereum", SR, 34477)])
    sp = lar.spread()
    assert sp["total"] == 2
    assert sp["contracts"] == 1
    assert sp["biggest"] == 2
    assert sp["chains"] == {"ethereum": 2}


def test_variety_looks_like_variety():
    lar = Larder(candidates=[
        _cand("ethereum", SR, 1),
        _cand("base", "0x" + "aa" * 20, 2),
        _cand("polygon", "0x" + "bb" * 20, 3),
    ])
    sp = lar.spread()
    assert sp["contracts"] == 3
    assert sp["biggest"] == 1
    assert list(sp["chains"]) == ["ethereum", "base", "polygon"] or \
        set(sp["chains"]) == {"ethereum", "base", "polygon"}


def test_the_biggest_contract_is_what_the_warning_watches():
    """Não é o número de contratos que magoa — é um deles dominar. Dez
    contratos com um alvo cada e um com vinte continua a ser um hunt
    previsível."""
    lar = Larder(candidates=[_cand("ethereum", SR, i) for i in range(20)]
                 + [_cand("base", "0x" + f"{i:02x}" * 20, 1) for i in range(10)])
    sp = lar.spread()
    assert sp["contracts"] == 11
    assert sp["biggest"] == 20
    assert sp["biggest"] / sp["total"] > 0.34


def test_the_same_contract_in_different_casing_is_the_same_contract():
    """Um endereço em maiúsculas não é um contrato novo — se contasse como
    tal, a medição dizia 'variedade' precisamente onde não há."""
    lar = Larder(candidates=[_cand("ethereum", SR, 1),
                             _cand("ethereum", SR.upper(), 2)])
    assert lar.spread()["contracts"] == 1


def test_the_same_contract_on_two_chains_counts_twice():
    """Mesmos bytes, cadeias diferentes: são alvos distintos e o jogador
    tem de descobrir a cadeia, que as pistas nunca dizem."""
    lar = Larder(candidates=[_cand("ethereum", SR, 1), _cand("base", SR, 2)])
    assert lar.spread()["contracts"] == 2


def test_an_empty_larder_says_nothing_instead_of_dividing_by_zero():
    sp = Larder().spread()
    assert sp["total"] == 0 and sp["contracts"] == 0 and sp["biggest"] == 0
    assert sp["chains"] == {}


def test_the_spread_never_carries_a_name_or_a_token():
    """A disciplina de sempre: o operador vê a forma, nunca o conteúdo.
    Nomes, tokenIds e contratos ficam de fora — é isso que torna o
    contador seguro para ir a um /status que o operador lê em público."""
    lar = Larder(candidates=[_cand("ethereum", SR, 34477, "WAR PROPAGANDA")])
    blob = repr(lar.spread())
    assert "WAR" not in blob and "PROPAGANDA" not in blob
    assert "34477" not in blob
    assert SR not in blob.lower()

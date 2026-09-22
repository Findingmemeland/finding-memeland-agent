"""As guardas de RPC têm de olhar para AMBAS as listas de fontes.

O PROBLEMA (22/09). Há duas listas de fontes neste código:

    sources.py  EPOCH1_CLASSIC  → o snapshot antigo   → EPOCH1_CHAINS
    prepare.py  SOURCES         → a despensa (/fill, /prepare)

Duas guardas no wiring existem para impedir um modo de falha concreto: um
alvo numa cadeia para a qual não temos RPC. Uma exige RPC com chave, a
outra exige um provedor público para o live check. Ambas liam só o
EPOCH1_CHAINS — ou seja, só a PRIMEIRA lista.

Hoje isso não dá diferença nenhuma, porque está tudo em Ethereum. Mas a
tarefa aberta (#55) é acrescentar uma fonte noutra cadeia, e é ao acrescentá-
-la ao SOURCES que as guardas ficariam caladas sobre ela.

E o custo não seria um erro no arranque, que é barato. Seria: o /fill enche
a despensa de alvos nessa cadeia, o /prepare sela um, a hunt lança, e o live
check estoira com um KeyError a meio do jogo — com uma pista já publicada e
jogadores a responder. Cara demais para uma linha de configuração em falta.

Estes testes são um alarme para o futuro: se alguém acrescentar uma fonte
numa cadeia nova sem acrescentar o RPC, falham aqui, na bancada.
"""
from __future__ import annotations

from finding_memeland.target.prepare import SOURCES
from finding_memeland.target.sources import EPOCH1_CHAINS
from finding_memeland.target.wiring import _chains_we_read


def test_every_larder_source_chain_is_guarded():
    """O TESTE. Nenhuma cadeia do SOURCES pode ficar fora da guarda."""
    for s in SOURCES:
        assert s.chain in _chains_we_read(), (
            f"a fonte {s.slug!r} lê a cadeia {s.chain!r}, e nenhuma guarda de "
            "RPC a exige — acrescenta o RPC dessa cadeia ao wiring")


def test_the_snapshot_chains_are_still_guarded_too():
    """A união não pode ter perdido o que a guarda já cobria."""
    assert EPOCH1_CHAINS <= _chains_we_read()


def test_the_guard_is_the_union_and_not_just_one_of_the_lists():
    """Se um dia alguém voltar a pôr só uma das listas, este teste ainda
    passa por acaso enquanto estiverem todas em Ethereum — por isso a
    asserção é sobre a REGRA, não sobre o valor de hoje."""
    assert _chains_we_read() == (
        frozenset(EPOCH1_CHAINS) | frozenset(s.chain for s in SOURCES))


def test_today_it_is_all_ethereum_and_that_is_the_open_problem():
    """A medição, escrita para ser contrariada.

    Enquanto isto passar, a frase que o site publica — "the treasure can be
    anywhere onchain" — descreve o jogo que QUEREMOS, não o que a despensa
    consegue produzir. No dia em que uma fonte fora de Ethereum entrar, este
    teste falha, e falhar é o ponto: quem o corrigir tem de vir aqui ler
    porquê, e confirmar que o RPC dessa cadeia ficou configurado."""
    assert _chains_we_read() == frozenset({"ethereum"}), (
        "entrou uma cadeia nova — confirma que o RPC com chave E o provedor "
        "público dessa cadeia estão configurados (Doppler dev), e depois "
        "actualiza este teste")

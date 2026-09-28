"""A guarda do contrato anterior tem de sobreviver a uma rejeição.

O QUE ACONTECEU (28/09, Hunt #14). Três hunts seguidas saíram do MESMO
contrato do SuperRare — #12, #13 e #14 — com a guarda do `avoid_contract`
já instalada e sete testes verdes a cobri-la.

A causa: o `avoid_contract` era recalculado DENTRO do ciclo de tentativas,

    for attempt in ...:
        cand = larder.take(rng, avoid_contract=larder.last_contract())

e o `/prepare` chama `larder.consume()` em todos os caminhos de rejeição.
O `consume()` acrescenta ao `used`. Logo, bastava um candidato ser
rejeitado para o `used[-1]` deixar de ser o alvo da hunt anterior e passar
a ser o descarte acabado de fazer — e o contrato que se queria evitar
voltava a ser elegível na tentativa seguinte.

O /prepare da #14 selou à 2.ª tentativa. A guarda desarmou-se sozinha à
primeira vez que correu a sério.

PORQUE É QUE OS SETE TESTES NÃO APANHARAM ISTO. Todos exercitavam o
`Larder.take()` em isolamento, com um `used` que só continha alvos de
hunts passadas. Nenhum conduzia o `/prepare` por várias tentativas, que é
o único sítio onde o `used` cresce a meio da decisão. A unidade estava
certa; o que faltava era o caminho.

Por isso este teste conduz o `TargetPreparer` a sério, com um candidato
que morre de propósito à primeira, e mede o contrato do alvo selado.
"""
from __future__ import annotations

import pytest

from finding_memeland.target.prepare import Candidate, Larder
from test_target_prepare import World, _finder, _preparer

SR = "0xb932a70a57673d89f4acffbe830e8ed7f75fb9e0"   # o das #12, #13 e #14
FND = "0x3b3ee1931dc30c1957379fac9aba94d1c48a5405"


class _FirstOfPool:
    """Tira sempre o primeiro do balde, para a ordem ser a da lista e o
    teste falar de causalidade em vez de sorte."""

    def randrange(self, n):
        return 0

    def choice(self, seq):
        return seq[0]


def _cand(contract: str, tid: int) -> Candidate:
    meta = {"name": "Some Two Words", "image": f"ipfs://img{tid}",
            "description": "d"}
    return Candidate(
        chain="ethereum", contract=contract, token_id=tid,
        name="Some Two Words", name_onchain="Some Two Words",
        description="d", image=f"ipfs://img{tid}",
        token_uri=f"ipfs://Qm{tid}", artist="", metadata=meta)


def hunt14_larder() -> Larder:
    """A situação da #14: a hunt anterior saiu do SR, e o primeiro
    candidato que o /prepare vai tirar está morto."""
    return Larder(
        candidates=[_cand(FND, 1),      # imagem morta — vai ser rejeitado
                    _cand(FND, 2),      # o alvo certo
                    _cand(SR, 3), _cand(SR, 4)],
        used=[f"ethereum:{SR}:999"])    # o alvo da hunt anterior


def test_the_previous_contract_stays_excluded_after_a_rejection():
    """O TESTE. Sem a correcção, o alvo selado sai do SuperRare — que foi
    exactamente o que a #14 publicou."""
    world = World(dead={"ipfs://img1"})
    prep = _preparer(world, _finder(world))
    prep._rng = _FirstOfPool()

    prepared, larder = prep.prepare(hunt14_larder())

    assert prepared.target.contract.lower() != SR, (
        "o alvo saiu do contrato da hunt anterior — a guarda desarmou-se "
        "quando o primeiro candidato foi rejeitado")
    assert prepared.target.contract.lower() == FND
    assert prepared.target.token_id == 2


def test_the_rejected_candidate_is_still_consumed():
    """A correcção não pode ter desligado o descarte: um candidato morto
    tem de sair da despensa, senão o /prepare seguinte tropeça nele outra
    vez e a despensa nunca se limpa."""
    world = World(dead={"ipfs://img1"})
    prep = _preparer(world, _finder(world))
    prep._rng = _FirstOfPool()

    _prepared, larder = prep.prepare(hunt14_larder())

    assert not any(c.token_id == 1 for c in larder.candidates)
    assert f"ethereum:{FND}:1" in larder.used


def test_two_rejections_in_a_row_still_do_not_unlock_the_contract():
    """Uma rejeição desarmava a guarda; duas não podem desarmá-la melhor."""
    world = World(dead={"ipfs://img1", "ipfs://img2"})
    lar = Larder(
        candidates=[_cand(FND, 1), _cand(FND, 2), _cand(FND, 5),
                    _cand(SR, 3), _cand(SR, 4)],
        used=[f"ethereum:{SR}:999"])
    prep = _preparer(world, _finder(world))
    prep._rng = _FirstOfPool()

    prepared, _larder = prep.prepare(lar)

    assert prepared.target.contract.lower() == FND
    assert prepared.target.token_id == 5


def test_the_hunt_still_runs_when_only_the_avoided_contract_is_left():
    """O limite que manda em tudo (Pedro, 09/09): preferir repetir um
    contrato a não haver hunt. Se as rejeições esvaziarem o resto, o
    filtro cai — mas só aí."""
    world = World(dead={"ipfs://img1"})
    lar = Larder(candidates=[_cand(FND, 1), _cand(SR, 3)],
                 used=[f"ethereum:{SR}:999"])
    prep = _preparer(world, _finder(world))
    prep._rng = _FirstOfPool()

    prepared, _larder = prep.prepare(lar)

    assert prepared.target.contract.lower() == SR
    assert prepared.target.token_id == 3


def test_a_clean_first_attempt_is_untouched_by_the_fix():
    """O caminho de treze hunts — selar à primeira — não pode ter mudado."""
    world = World()
    prep = _preparer(world, _finder(world))
    prep._rng = _FirstOfPool()

    prepared, _larder = prep.prepare(hunt14_larder())

    assert prepared.target.contract.lower() == FND
    assert prepared.target.token_id == 1


@pytest.mark.parametrize("n_rejections", [0, 1, 2])
def test_the_sealed_contract_never_depends_on_how_many_died(n_rejections):
    """A propriedade, dita de uma vez: o contrato evitado é o da HUNT
    anterior, e o número de candidatos que morreram pelo caminho não tem
    nada a ver com isso."""
    dead = {f"ipfs://img{i}" for i in range(1, 1 + n_rejections)}
    lar = Larder(
        candidates=[_cand(FND, i) for i in range(1, 4)] + [_cand(SR, 9)],
        used=[f"ethereum:{SR}:999"])
    prep = _preparer(World(dead=dead), _finder(World(dead=dead)))
    prep._rng = _FirstOfPool()

    prepared, _larder = prep.prepare(lar)

    assert prepared.target.contract.lower() == FND

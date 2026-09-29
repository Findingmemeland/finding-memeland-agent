"""Porque morreu — "único" e "dono" pela razão, não por um número só.

29/09. Com a imagem resolvida, a colheita passou a morrer em "único" e
"dono", e esses dois números escondiam factos opostos:

  único  → não-único       outra peça tem o nome (do candidato)
           cheio-com-igual página cheia e há pelo menos um homónimo nela
           cheio-sem-igual página cheia, nenhum homónimo — o OpenSea só não
                           pôs a nossa entre as 50 (o nome não prova nada)
           índice-cego     a pesquisa não mostra a peça (o caçador também não)
           rede-NOSSO      o pedido falhou — nosso
  dono   → contrato        um contrato é dono (cofre, custódia, carteira-contrato)
           sem-veredicto   não se soube: RPC cego, token queimado, rede

SÓ MEDIÇÃO: nenhuma resposta muda (a guarda do nome continua a devolver None
nos dois "cheio"; a do dono continua False/None). Contagens, nunca nomes.
"""
from __future__ import annotations

import random

from finding_memeland.target.prepare import Larder, Source, TargetFinder
from finding_memeland.target.refresh import TokenRead
from finding_memeland.target.search_guard import FakeNameSearch, MarketNameUniqueness
from finding_memeland.target.sources import ChainEoaCheck, ChainRpc

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
TARGET = "ETHEREUM:0xaaa:7"


class _Guard:
    """Imita uma guarda com `stats`: devolve `answer` e mexe nos contadores
    `moves`, como as verdadeiras fazem."""

    def __init__(self, answer, *moves):
        self.answer, self.moves = answer, moves
        self.stats: dict = {}

    def __call__(self, *a):
        for m in self.moves:
            self.stats[m] = self.stats.get(m, 0) + 1
        return self.answer


def _deposit(*, owner=lambda *a: True, unique=lambda *a: True) -> str:
    f = TargetFinder(
        sources=[Source("x", "base", "0x" + "cd" * 20)],
        total_supply=lambda c, k: 10, token_by_index=lambda c, k, i: i + 1,
        read_token=lambda c, k, t: TokenRead(
            token_uri="ipfs://bafymeta",
            metadata={"name": "Some Two Words", "image": "ipfs://bafyimg"}),
        probe_image=lambda u: (PNG, len(PNG)), owner_is_eoa=owner,
        name_is_unique=unique, rng=random.Random(0))
    return f.deposit(Larder(), ["base:0x" + "ab" * 20 + ":1"],
                     chain_ok=lambda c: True).render()


# --------------------------------------------------------------------------- #
# único                                                                         #
# --------------------------------------------------------------------------- #


def test_unique_says_why_from_the_guard_s_own_counters():
    for answer, moves, label in [
            (False, ("not_unique",), "não-único"),
            (None, ("crowded", "crowded_same"), "cheio-com-igual"),
            (None, ("crowded",), "cheio-sem-igual"),
            (None, ("blind",), "índice-cego"),
            (None, ("transport",), "rede-NOSSO")]:
        out = _deposit(unique=_Guard(answer, *moves))
        assert f"único 1 ({label} 1)" in out, (moves, out)


def test_unique_without_counters_still_separates_a_verdict_from_none():
    assert "único 1 (não-único 1)" in _deposit(unique=lambda *a: False)
    assert "único 1 (sem-veredicto 1)" in _deposit(unique=lambda *a: None)


def _real_guard(rows, page=25):
    return MarketNameUniqueness(search=FakeNameSearch({"salt harbor": rows}),
                                page_size=page, retries=0, sleep_s=0.0)


def test_a_full_page_with_a_namesake_is_counted_as_such():
    rows = [(f"ETHEREUM:0xbbb:{i}", f"Blue Salt Harbor Dawn {i}") for i in range(24)]
    rows.append(("BASE:0xccc:1", "Salt Harbor"))
    u = _real_guard(rows)
    assert u("Salt Harbor", "ethereum", "0xaaa", 7) is None     # answer unchanged
    assert u.stats["crowded"] == 1 and u.stats["crowded_same"] == 1


def test_a_full_page_of_look_alikes_is_not_a_namesake():
    """O caso que a colheita mais viu: 50 peças com palavras parecidas, a
    nossa fora da página, NENHUMA com o mesmo nome."""
    rows = [(f"ETHEREUM:0xbbb:{i}", f"Salt Harbor Dawn {i}") for i in range(25)]
    u = _real_guard(rows)
    assert u("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert u.stats["crowded"] == 1 and u.stats["crowded_same"] == 0


# --------------------------------------------------------------------------- #
# dono                                                                          #
# --------------------------------------------------------------------------- #


def test_owner_says_why_from_the_check_s_own_counters():
    assert "dono 1 (contrato 1)" in _deposit(owner=_Guard(False, "contract"))
    assert "dono 1 (sem-veredicto 1)" in _deposit(owner=_Guard(None, "unverifiable"))


def test_owner_without_counters_still_separates_a_verdict_from_none():
    assert "dono 1 (contrato 1)" in _deposit(owner=lambda *a: False)
    assert "dono 1 (sem-veredicto 1)" in _deposit(owner=lambda *a: None)


def _eoa_check(owner_code: str, *, contract_code: str = "0x6080") -> ChainEoaCheck:
    owner = "0x" + "11" * 20
    rpc = ChainRpc(
        chain="base",
        eth_call=lambda to, data: "0x" + "00" * 12 + owner[2:],
        get_code=lambda addr: contract_code if addr != owner else owner_code)
    return ChainEoaCheck(rpcs={"base": rpc})


def test_the_eoa_check_counts_each_answer():
    c = _eoa_check("0x")
    assert c("base", "0x" + "cd" * 20, 1) is True
    c2 = _eoa_check("0x6080604052")
    assert c2("base", "0x" + "cd" * 20, 1) is False
    c3 = _eoa_check("0x", contract_code="0x")                 # RPC blind
    assert c3("base", "0x" + "cd" * 20, 1) is None
    assert c.stats["eoa"] == 1
    assert c2.stats["contract"] == 1
    assert c3.stats["unverifiable"] == 1


def test_an_eip7702_wallet_is_still_a_person():
    """Já era assim desde 06/09 (ChainRpc.is_eoa). Fica preso aqui porque a
    29/09 quase o dei como causa das recusas em Base — não é."""
    delegated = "0xef0100" + "22" * 20
    c = _eoa_check(delegated)
    assert c("base", "0x" + "cd" * 20, 1) is True
    assert c.stats["eoa"] == 1 and c.stats["contract"] == 0


def test_the_splits_never_name_the_candidate():
    out = _deposit(owner=_Guard(False, "contract"))
    out += _deposit(unique=_Guard(None, "blind"))
    assert "Some Two Words" not in out and "abab" not in out and "cdcd" not in out

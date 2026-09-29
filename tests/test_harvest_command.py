"""/harvest: da cadeia para a despensa, cadeia a cadeia.

Decisões do Pedro (28/09): só manual, 200 blocos, Ethereum + Base, e os
três filtros (domínios, percentagens/coordenadas, máximo 2 por contrato).

O que estes testes fixam é a ORQUESTRAÇÃO — o colector e o depósito já têm
testes próprios (test_harvest.py, test_deposit.py). Aqui prova-se que as
peças se ligam sem que uma falha numa cadeia contamine a outra, e que o
relatório que vai para o Telegram nunca transporta o que a despensa guarda.
"""
from __future__ import annotations

import random

from finding_memeland.target.harvest import (
    HarvestBlind,
    HarvestReport,
    MintHarvester,
    parse_canary,
)
from finding_memeland.target.prepare import Larder
from finding_memeland.target.wiring import TargetWiring
from test_target_prepare import World, _finder

A = "0x" + "a1" * 20
B = "0x" + "b2" * 20


class _Store:
    def __init__(self):
        self.larder = Larder()
        self.saves = 0

    def load(self):
        return self.larder

    def save(self, larder):
        self.larder = larder
        self.saves += 1


class _Harv:
    """Um colector de mentira: devolve refs fixas, ou rebenta."""

    def __init__(self, refs=(), *, blind=False, boom=None):
        self.refs = list(refs)
        self.blind = blind
        self.boom = boom
        self.calls = 0

    def harvest(self, n, **kw):
        self.calls += 1
        if self.blind:
            raise HarvestBlind("canário falhou em teste")
        if self.boom:
            raise self.boom
        rep = HarvestReport(blocks=n, mints=len(self.refs), named=len(self.refs),
                            kept=len(self.refs))
        rep.contracts = {r.split(":")[1] for r in self.refs}
        return list(self.refs), rep


def _wiring(harvesters, deposit_chains, world=None):
    world = world or World()
    store = _Store()
    tw = TargetWiring(
        ports=None, pipeline=None, epoch=None, snapshot_store=None,
        scan_blocks=0, writability_rates={}, uniqueness_rates={},
        finder=_finder(world), larder_store=store,
        harvesters=harvesters, deposit_chains=frozenset(deposit_chains))
    return tw, store


# --------------------------------------------------------------------------- #
# Uma cadeia não contamina a outra                                              #
# --------------------------------------------------------------------------- #


def test_a_chain_without_a_public_provider_is_skipped_before_any_call():
    """Colher alvos que o depósito vai recusar é gastar chamadas para nada.
    E o relatório diz QUAL variável falta, porque isso é configuração."""
    eth, base = _Harv([f"ethereum:{A}:1"]), _Harv([f"base:{B}:1"])
    tw, _ = _wiring({"ethereum": eth, "base": base}, {"ethereum"})
    out = tw.harvest(200)
    assert base.calls == 0, "Base foi varrida sem ter onde depositar"
    assert "TARGET_PUBLIC_RPCS_BASE" in out
    assert eth.calls == 1


def test_a_blind_chain_does_not_stop_the_other():
    """O canário de uma cadeia falhar não pode impedir a outra de colher."""
    eth, base = _Harv(blind=True), _Harv([f"base:{B}:7"])
    tw, store = _wiring({"ethereum": eth, "base": base}, {"ethereum", "base"})
    out = tw.harvest(200)
    assert "⛔" in out
    assert store.larder.size() == 1


def test_a_dead_rpc_is_ours_and_does_not_erase_the_other_report():
    """RPC em baixo numa cadeia: 'não medida', nunca 'não havia nada'. E o
    que a outra cadeia já fez fica no relatório e na despensa."""
    eth = _Harv([f"ethereum:{A}:1"])
    base = _Harv(boom=ConnectionError("rpc down"))
    tw, store = _wiring({"ethereum": eth, "base": base}, {"ethereum", "base"})
    out = tw.harvest(200)
    assert "NÃO MEDIDA" in out and "ConnectionError" in out
    assert "ethereum:" in out
    assert store.larder.size() == 1


# --------------------------------------------------------------------------- #
# O depósito                                                                    #
# --------------------------------------------------------------------------- #


def test_harvested_refs_go_through_the_deposit_and_land_in_the_larder():
    refs = [f"ethereum:{A}:{i}" for i in range(1, 4)]
    tw, store = _wiring({"ethereum": _Harv(refs)}, {"ethereum"})
    out = tw.harvest(200)
    assert store.larder.size() == 3
    assert "0 → 3 (+3)" in out


def test_the_larder_is_saved_after_each_chain():
    """A primeira corrida real teve de ser morta ao fim de uma hora (29/09),
    e com ela ia tudo o que Ethereum já tinha encontrado. Grava-se a cada
    cadeia, e outra vez no fim."""
    tw, store = _wiring({"ethereum": _Harv([f"ethereum:{A}:1"]),
                         "base": _Harv([f"base:{B}:2"])},
                        {"ethereum", "base"})
    tw.harvest(200)
    assert store.saves >= 2


def test_a_run_killed_after_the_first_chain_keeps_the_first_chain():
    """O que Ethereum encontrou fica gravado mesmo que Base rebente de uma
    forma que nem o except apanha."""
    class _Die(_Harv):
        def harvest(self, n, **kw):
            raise KeyboardInterrupt      # simula o processo a ser morto

    tw, store = _wiring({"ethereum": _Harv([f"ethereum:{A}:1"]),
                         "base": _Die()}, {"ethereum", "base"})
    try:
        tw.harvest(200)
    except KeyboardInterrupt:
        pass
    assert store.larder.size() == 1


def test_the_five_checks_still_apply_to_harvested_refs():
    """Um alvo colhido cujo nome não é único no mercado não entra. A
    colheita muda de onde vêm os candidatos, não o que se lhes exige."""
    world = World(unique=False)
    tw, store = _wiring({"ethereum": _Harv([f"ethereum:{A}:1"])},
                        {"ethereum"}, world=world)
    tw.harvest(200)
    assert store.larder.size() == 0


# --------------------------------------------------------------------------- #
# O relatório vai para o Telegram                                               #
# --------------------------------------------------------------------------- #


def test_the_report_never_carries_a_contract_or_a_token():
    """A despensa são as próximas respostas. O relatório fica no histórico
    do Telegram — contagens e causas, nunca um endereço nem um id."""
    refs = [f"ethereum:{A}:4242", f"ethereum:{B}:9999"]
    tw, _ = _wiring({"ethereum": _Harv(refs)}, {"ethereum"})
    out = tw.harvest(200).lower()
    assert A.lower() not in out and B.lower() not in out
    assert "4242" not in out and "9999" not in out


def test_without_harvesters_it_says_so_instead_of_pretending():
    tw, _ = _wiring({}, set())
    assert "não configurada" in tw.harvest(200)


# --------------------------------------------------------------------------- #
# Canário e intervalo                                                           #
# --------------------------------------------------------------------------- #


def test_parse_canary_reads_what_the_script_prints():
    assert parse_canary("15000000:12") == (15000000, 12)
    assert parse_canary("  42:3 ") == (42, 3)


def test_a_bad_canary_becomes_no_canary_which_means_no_harvest():
    """Uma config torta não pode virar uma colheita cega: (0, 0) é "sem
    canário", e sem canário o colector recusa-se a varrer."""
    for bad in ["", "lixo", "12", "12:0", "0:5", "-1:3", "a:b", "1:2:3"]:
        assert parse_canary(bad) == (0, 0), bad


class _Prov:
    def __init__(self, *chains):
        self.rpc_urls = {c: f"https://{c}.example" for c in chains}


def test_a_chain_only_some_providers_read_is_not_depositable():
    """O live check roda os provedores à vez. Com 3 provedores e só um a
    ler Base, um alvo de Base falhava duas leituras em cada três, a meio da
    hunt. A primeira versão testava a união e deixava isto passar."""
    from finding_memeland.target.wiring import chains_every_provider_reads
    provs = [_Prov("ethereum", "base"), _Prov("ethereum"), _Prov("ethereum")]
    assert chains_every_provider_reads(provs, ("ethereum", "base")) == {"ethereum"}


def test_a_chain_every_provider_reads_is_depositable():
    from finding_memeland.target.wiring import chains_every_provider_reads
    provs = [_Prov("ethereum", "base"), _Prov("ethereum", "base")]
    assert chains_every_provider_reads(provs, ("ethereum", "base")) == {
        "ethereum", "base"}


def test_no_providers_means_nothing_is_depositable():
    from finding_memeland.target.wiring import chains_every_provider_reads
    assert chains_every_provider_reads([], ("ethereum", "base")) == frozenset()


def test_the_skip_message_says_how_many_urls_are_needed():
    """A causa real é o NÚMERO de URLs, e é isso que a mensagem tem de dizer
    — senão o operador põe um URL, vê a cadeia continuar saltada, e não
    percebe porquê."""
    tw, _ = _wiring({"base": _Harv()}, set())
    out = tw.harvest(10)
    assert "TARGET_PUBLIC_RPCS_BASE" in out
    assert "TARGET_PUBLIC_RPCS_ETHEREUM" in out


def test_blocks_are_drawn_from_the_chain_s_span_start():
    """Em Ethereum os blocos antes dos NFTs só custam chamadas. O intervalo
    começa onde eles começam."""
    seen = []

    def get_logs(a, b):
        seen.append(a)
        return []

    h = MintHarvester(chain="ethereum", latest_block=lambda: 10_000,
                      get_logs=get_logs, read_meta=lambda c, t: None,
                      canary_block=0, canary_mints=0, span_start=9_000,
                      rng=random.Random(0))
    h.canary_passes = lambda: True          # o canário tem teste próprio
    h.harvest(200)
    assert seen and all(9_000 <= b < 10_000 for b in seen)

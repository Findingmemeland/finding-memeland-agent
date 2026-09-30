"""A metadata lê-se uma vez: a colheita passa a leitura ao depósito (30/09).

Nas colheitas de 29/09, 14 candidatos de Base caíram no depósito porque o
gateway falhou a servir, pela segunda vez, metadata que tinha servido minutos
antes. A metadata é endereçada por conteúdo — a segunda leitura trazia os
mesmos bytes e não verificava nada. Agora o depósito usa a da colheita; as
outras quatro verificações correm iguais, e o /prepare relê antes de selar.
"""
from __future__ import annotations

import random

from test_harvest_command import _Harv, _Store
from test_target_prepare import World, _finder, _preparer
from test_unavailable_split import IPFS_IMG, IPFS_URI, _mint

from finding_memeland.target.harvest import HarvestReport, MintHarvester
from finding_memeland.target.prepare import Larder
from finding_memeland.target.refresh import TokenRead
from finding_memeland.target.wiring import TargetWiring

A = "0x" + "a1" * 20
B = "0x" + "b2" * 20


def _read(name="Grease Pencil Gospel", image=IPFS_IMG):
    return TokenRead(token_uri=IPFS_URI, metadata={"name": name, "image": image})


def _no_second_read(*a):
    raise AssertionError("the deposit asked the gateway again")


# --------------------------------------------------------------------------- #
# 1. A colheita guarda a leitura de quem fica — e só de quem fica               #
# --------------------------------------------------------------------------- #


def _harvest_one_block(reads: dict):
    logs = {100: [_mint(A, 1)], 7: [_mint(A, 5), _mint(B, 9)]}
    h = MintHarvester(chain="ethereum", latest_block=lambda: 1_000_000,
                      get_logs=lambda a, b: logs.get(a, []),
                      read_meta=lambda c, t: reads[(c, t)],
                      canary_block=100, canary_mints=1, rng=random.Random(0))

    class _R:
        def randrange(self, lo, hi):
            return 7
    h._rng = _R()                                               # noqa: SLF001
    return h.harvest(1)


def test_the_harvest_keeps_the_read_of_every_ref_it_keeps():
    good = _read()
    refs, rep = _harvest_one_block({(A, 5): good, (B, 9): _read(name="#123")})
    assert refs == [f"ethereum:{A}:5"]
    assert rep.reads == {f"ethereum:{A}:5": good}       # the numbered one is not kept


def test_the_reads_never_reach_the_report():
    """Têm nomes. O relatório vai para o Telegram."""
    _refs, rep = _harvest_one_block({(A, 5): _read(), (B, 9): _read(name="#1")})
    assert rep.reads
    for text in (rep.render(), repr(rep)):
        assert "Grease Pencil" not in text and IPFS_IMG not in text


# --------------------------------------------------------------------------- #
# 2. O depósito usa-a                                                           #
# --------------------------------------------------------------------------- #


def _deposit(known, refs=None, **world_kw):
    world = World(**world_kw)
    finder = _finder(world)
    finder._read_token = _no_second_read                        # noqa: SLF001
    larder = Larder()
    rep = finder.deposit(larder, refs or list(known), chain_ok=lambda c: True,
                         known_reads=known)
    return rep, larder


def test_a_harvested_ref_is_deposited_without_a_second_read():
    rep, larder = _deposit({f"ethereum:{A}:5": _read(image="ipfs://img5")})
    assert rep.added == 1 and larder.size() == 1


def test_the_other_checks_still_run_on_the_harvest_s_read():
    """Imagem, dono e único correm iguais — só a leitura não se repete."""
    rep, larder = _deposit({f"ethereum:{A}:5": _read(image="ipfs://img5")},
                           dead={"ipfs://img5"})
    assert rep.added == 0 and rep.rejected.image == 1
    rep, _ = _deposit({f"ethereum:{A}:5": _read(image="ipfs://img5")}, eoa=False)
    assert rep.added == 0 and rep.rejected.owner == 1
    rep, _ = _deposit({f"ethereum:{A}:5": _read(image="ipfs://img5")}, unique=False)
    assert rep.added == 0 and rep.rejected.unique == 1


def test_the_name_check_still_runs_on_the_harvest_s_read():
    rep, _ = _deposit({f"ethereum:{A}:5": _read(name="Solo")})
    assert rep.added == 0 and rep.rejected.name == 1


def test_a_ref_without_a_harvest_read_is_read_as_before():
    world = World()
    finder = _finder(world)
    larder = Larder()
    rep = finder.deposit(larder, [f"ethereum:{A}:5"], chain_ok=lambda c: True,
                         known_reads={f"ethereum:{B}:9": _read()})
    assert rep.added == 1                     # World.read_token answered


def test_the_known_read_matches_a_checksummed_ref():
    """A ref pode vir com o endereço em maiúsculas; a chave é a mesma."""
    upper = "0x" + "A1" * 20
    rep, _ = _deposit({f"ethereum:{upper}:5": _read(image="ipfs://img5")},
                      refs=[f"ethereum:{A}:5"])
    assert rep.added == 1


# --------------------------------------------------------------------------- #
# 3. O /harvest liga uma coisa à outra; o /prepare relê sempre                  #
# --------------------------------------------------------------------------- #


def test_the_harvest_hands_its_reads_to_the_deposit():
    ref = f"ethereum:{A}:5"

    class _Reading(_Harv):
        def harvest(self, n, **kw):
            refs, rep = super().harvest(n, **kw)
            rep.reads = {ref: _read(image="ipfs://img5")}
            return refs, rep

    world = World()
    finder = _finder(world)
    finder._read_token = _no_second_read                        # noqa: SLF001
    store = _Store()
    tw = TargetWiring(
        ports=None, pipeline=None, epoch=None, snapshot_store=None,
        scan_blocks=0, writability_rates={}, uniqueness_rates={},
        finder=finder, larder_store=store,
        harvesters={"ethereum": _Reading([ref])},
        deposit_chains=frozenset({"ethereum"}))
    out = tw.harvest(200)
    assert store.larder.size() == 1, out


def test_prepare_still_re_reads_before_sealing():
    world = World()
    finder = _finder(world)
    larder = Larder()
    rep = finder.deposit(larder, [f"ethereum:{A}:5"], chain_ok=lambda c: True,
                         known_reads={f"ethereum:{A}:5": _read(image="ipfs://img5")})
    assert rep.added == 1
    reads = []

    def read_token(chain, contract, tid):
        reads.append((contract, tid))
        return world.read_token(chain, contract, tid)
    finder._read_token = read_token                             # noqa: SLF001
    _preparer(world, finder).prepare(larder)
    assert reads == [(A, 5)]


def test_an_empty_report_hands_nothing():
    assert HarvestReport().reads == {}

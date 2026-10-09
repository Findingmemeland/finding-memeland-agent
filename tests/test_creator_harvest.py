"""/harvest manifold [n] — colher dos contratos de criador (10/10, passo B).

Duas sondas mediram a Manifold em Base (210 contratos, 58 peças, 10 passariam
com Arweave). O Arweave foi aceite; isto é a sonda a GUARDAR. As decisões do
Pedro que estes testes fixam:

  1. `n` são contratos: 60 por omissão, 300 de máximo;
  2. com o Arweave desligado recusa à partida, sem varrer nada;
  3. o peso no sorteio da despensa não muda;
  4. um contrato que já tem alvos na despensa pode dar outra peça — até ao
     máximo de DOIS por contrato na despensa (a regra de 28/09);
  5. o sorteio guarda os blocos que já viu e o relatório diz quantas
     chamadas RPC fez.

E o que não é decisão, é disciplina: o sorteio é o da sonda e não escolhe;
os filtros de nome são os da colheita por blocos (o mesmo código); o
depósito é o de sempre; "0 alvos" tem de querer dizer que não havia (canário);
nada do que sai tem contrato, tokenId ou nome.
"""
from __future__ import annotations

import random
import re

import pytest
from test_harvest_command import _Store
from test_probe import IPFS_IMG, OT, PAD, D, FakeNode, addr
from test_target_prepare import World, _finder
from test_target_wiring import FakeRepo, rpc_ok, settings

from finding_memeland import main
from finding_memeland.target.adapters import GatewayTally
from finding_memeland.target.creator_harvest import (
    MANIFOLD_BASE_CANARY_CREATION,
    CallCounter,
    CreatorHarvester,
    CreatorHarvestReport,
)
from finding_memeland.target.exposed import ExposedList
from finding_memeland.target.harvest import (
    HARVEST_SOURCE_DEFAULT,
    HARVEST_SOURCE_MAX,
    HARVEST_SOURCES,
    HARVEST_USAGE,
    MAX_TARGETS_PER_CONTRACT,
    HarvestBlind,
    HarvestReport,
    MintHarvester,
    parse_harvest_command,
    sift,
)
from finding_memeland.target.prepare import Candidate, Larder
from finding_memeland.target.probe import DeployerSampler, ProbeBlind
from finding_memeland.target.refresh import TokenRead, arweave_ref, uri_is_content_addressed
from finding_memeland.target.sources import GatewayUnavailable, MetadataPinGone
from finding_memeland.target.wiring import TargetWiring, build_target

AR_URI = "ar://" + "abcDEF123_-" * 3 + "abcDEF1234"
AR_IMG = "ar://" + "Z" * 43
ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")


def _wide(uri: str) -> bool:
    return uri_is_content_addressed(uri) or arweave_ref(uri) is not None


# --------------------------------------------------------------------------- #
# 1. O comando                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("arg, want", [
    ("manifold", ("manifold", 60, None)), ("manifold 120", ("manifold", 120, None)),
    ("120 MANIFOLD", ("manifold", 120, None)), ("Manifold 1", ("manifold", 1, None)),
    ("manifold 300", ("manifold", 300, None)),
    # sem fonte: a colheita por blocos, tal como era
    ("", (None, 200, None)), ("50", (None, 50, None)), ("200 base", (None, 200, "base")),
    ("ethereum", (None, 200, "ethereum")), ("base 2000", (None, 2000, "base")),
])
def test_a_source_takes_contracts_and_blocks_stay_blocks(arg, want):
    assert parse_harvest_command(arg, default_blocks=200) == want


@pytest.mark.parametrize("arg", [
    "manifold 0", "manifold 301", "manifold 2000", "manifold base", "base manifold",
    "manifold 5 5", "manifold manifold", "manifold x", "zora", "manifold 0x" + "ab" * 20,
    "0x" + "ab" * 20, "manifold -1", "2001", "eth",
])
def test_anything_else_is_refused_with_the_usage_and_never_guessed(arg):
    with pytest.raises(ValueError) as e:
        parse_harvest_command(arg, default_blocks=200)
    assert str(e.value) == HARVEST_USAGE


def test_the_usage_names_both_forms_and_the_numbers_are_the_probe_s():
    assert "/harvest [1..2000] [cadeia]" in HARVEST_USAGE
    assert "/harvest <fonte> [1..300]" in HARVEST_USAGE and "manifold" in HARVEST_USAGE
    assert (HARVEST_SOURCES, HARVEST_SOURCE_DEFAULT, HARVEST_SOURCE_MAX) == (
        ("manifold",), 60, 300)
    assert not ADDRESS.search(HARVEST_USAGE)


# --------------------------------------------------------------------------- #
# 2. O sorteio guarda os blocos que já viu                                      #
# --------------------------------------------------------------------------- #


def _node(n=40, head=50_000_000, seed=5):
    rng = random.Random(seed)
    blocks = sorted(rng.sample(range(1_000, head - 1_000), n))
    return FakeNode([(b, addr(i + 1)) for i, b in enumerate(blocks)], head=head)


def _nonce_reads(node) -> int:
    return node.calls.count("eth_getTransactionCount")


def test_the_memory_changes_the_number_of_calls_never_the_answer():
    node, fresh_node = _node(), _node()
    shared = DeployerSampler(call=node.call, deployer=D)
    head = shared.head()
    order = random.Random(1).sample(range(1, 41), 40)
    with_memory = [shared.contract_of(k, head) for k in order]
    from_scratch = [DeployerSampler(call=fresh_node.call, deployer=D).contract_of(k, head)
                    for k in order]
    assert with_memory == from_scratch == [addr(k) for k in order]
    assert _nonce_reads(node) < _nonce_reads(fresh_node)


def test_what_was_asked_once_is_not_asked_again():
    node = _node()
    s = DeployerSampler(call=node.call, deployer=D)
    head = s.head()
    s.contract_of(7, head)
    first = _nonce_reads(node)
    assert first <= 30                                   # a bisection, as before
    s.contract_of(7, head)
    assert _nonce_reads(node) == first                   # every read was remembered
    s.contract_of(8, head)
    assert _nonce_reads(node) - first < first            # the neighbour shares the top


def test_what_is_remembered_serves_a_later_run_with_another_head():
    """O sampler vive enquanto o processo viver: uma corrida mais tarde, com
    a cadeia mais alta, usa o que a anterior aprendeu e responde o mesmo."""
    node = _node(head=50_000_000)
    s = DeployerSampler(call=node.call, deployer=D)
    for k in (40, 3, 17):
        assert s.contract_of(k, 50_000_000) == addr(k)
    known = _nonce_reads(node)
    node.head = 50_500_000                               # the chain moved on
    head = s.head()
    assert [s.contract_of(k, head) for k in (3, 17, 40, 22)] == [
        addr(3), addr(17), addr(40), addr(22)]
    assert _nonce_reads(node) - known < 30               # only the new one cost a search


def test_the_universe_and_the_blindness_checks_are_what_they_were():
    s = DeployerSampler(call=_node(n=4).call, deployer=D)
    assert s.total(s.head()) == 4
    blind = DeployerSampler(call=FakeNode([(100, addr(1))], archive=False).call, deployer=D)
    with pytest.raises(ProbeBlind):
        blind.total(blind.head())


# --------------------------------------------------------------------------- #
# 3. O colector                                                                 #
# --------------------------------------------------------------------------- #


class Draws:
    """Um deployer de mentira: a criação n.º k é `creations[k-1]` — um
    endereço, None (criação de outro tipo) ou uma excepção (o RPC)."""

    def __init__(self, creations, *, blind=None, head_error=None):
        self.creations = list(creations)
        self.blind = blind
        self.head_error = head_error
        self.asked: list[int] = []

    def head(self) -> int:
        if self.head_error:
            raise self.head_error
        return 1000

    def total(self, head: int) -> int:
        if self.blind:
            raise self.blind
        return len(self.creations)

    def contract_of(self, k: int, head: int):
        self.asked.append(k)
        got = self.creations[k - 1]
        if isinstance(got, BaseException):
            raise got
        return got


class Pieces:
    """Os contratos: {endereço: {"erc721": bool, "last": n, "name": …}}."""

    def __init__(self, contracts):
        self.contracts = contracts
        self.existence: list[tuple[str, int]] = []
        self.reads: list[tuple[str, int]] = []

    def eth_call(self, contract, data):
        spec = self.contracts[contract]
        if isinstance(spec.get("erc721"), BaseException):
            raise spec["erc721"]
        return "0x" + "0" * 63 + ("1" if spec.get("erc721", True) else "0")

    def token_uri(self, contract, tid):
        self.existence.append((contract, tid))
        return AR_URI if 1 <= tid <= self.contracts[contract].get("last", 1) else None

    def read(self, contract, tid):
        self.reads.append((contract, tid))
        spec = self.contracts[contract]
        if isinstance(spec.get("read"), BaseException):
            raise spec["read"]
        if spec.get("gone"):
            return None
        if spec.get("unresolved"):
            return TokenRead(token_uri="https://example.com/1.json", metadata=None)
        meta = {"image": spec.get("image", AR_IMG)}
        if spec.get("name", "Quiet Lantern Above") is not None:
            meta["name"] = spec.get("name", "Quiet Lantern Above")
        return TokenRead(token_uri=AR_URI, metadata=meta)


def _harvester(creations, contracts, *, canary=1, seed=3, **kw):
    draws, pieces = Draws(creations, **kw), Pieces(contracts)
    eth_call, token_uri = CallCounter(pieces.eth_call), CallCounter(pieces.token_uri)
    h = CreatorHarvester(
        source="manifold", chain="base", sampler=draws, eth_call=eth_call,
        token_uri=token_uri, read_meta=pieces.read, accepts_image=_wide,
        canary_creation=canary, counters=(eth_call, token_uri),
        rng=random.Random(seed))
    return h, draws, pieces


def test_one_piece_from_each_drawn_contract_none_drawn_twice():
    contracts = {addr(i): {"last": 9} for i in range(1, 7)}
    h, draws, pieces = _harvester(list(contracts), contracts)
    refs, rep = h.harvest(60)                            # more than there are
    assert rep.asked == rep.universe == 6 and rep.read == 6
    assert sorted(draws.asked[1:]) == [1, 2, 3, 4, 5, 6]     # after the canary, each once
    assert len(refs) == 6 and len({r.split(":")[1] for r in refs}) == 6
    assert all(r.startswith("base:") and 1 <= int(r.split(":")[2]) <= 9 for r in refs)
    assert len(pieces.reads) == 6                        # one read per contract
    assert set(rep.reads) == set(refs)                   # for the deposit: no second read


def test_only_n_contracts_are_drawn():
    contracts = {addr(i): {"last": 1} for i in range(1, 31)}
    h, draws, _ = _harvester(list(contracts), contracts)
    _refs, rep = h.harvest(8)
    assert rep.asked == 8 and rep.universe == 30
    assert len(set(draws.asked[1:])) == 8


def test_what_is_not_a_creator_s_erc721_with_pieces_is_counted_and_skipped():
    contracts = {addr(1): {"last": 3}, addr(2): {"erc721": False},
                 addr(3): {"last": 0}, addr(5): {"last": 2}}
    h, _, pieces = _harvester([addr(1), addr(2), addr(3), None, addr(5)], contracts)
    refs, rep = h.harvest(60)
    assert (rep.asked, rep.not_erc721, rep.empty, rep.no_event, rep.read) == (5, 1, 1, 1, 2)
    assert len(refs) == 2 and {c for c, _ in pieces.reads} == {addr(1), addr(5)}
    line = rep.render()
    assert line.startswith("manifold/base: 5 contrato(s) sorteado(s) de 5 criações")
    for part in ("não-ERC-721 1", "sem peças 1", "criação de outro tipo 1",
                 "2 peça(s) lida(s)", "2 alvo(s) de 2 contrato(s)"):
        assert part in line, (part, line)
    assert not ADDRESS.search(line)


def test_the_name_filters_are_the_block_harvest_s_with_the_same_causes():
    contracts = {
        addr(1): {"name": "Quiet Lantern Above"},          # stays
        addr(2): {"name": "Cool Cat #123"},                # numerados
        addr(3): {"name": "Untitled"},                     # nome recusado
        addr(4): {"name": None},                           # sem nome
        addr(5): {"gone": True},                           # queimados
        addr(6): {"unresolved": True},                     # tokenURI fora de IPFS
        addr(7): {"image": "https://example.com/1.png"},   # imagem fora de IPFS
        addr(8): {"read": GatewayUnavailable("x")},        # nosso
        addr(9): {"read": MetadataPinGone("x")},           # deles
    }
    h, _, _ = _harvester(list(contracts), contracts)
    refs, rep = h.harvest(60)
    assert len(refs) == 1 and rep.read == 9
    line = rep.render()
    for part in ("numerados 1", "nome recusado 1", "sem nome 1", "queimados 1",
                 "tokenURI fora de IPFS (http 1)", "imagem fora de IPFS (http 1)",
                 "indisponível-NOSSO 1 (gateway 1)", "defeito-DELES 1 (pin-morto 1)"):
        assert part in line, (part, line)
    # the SAME function decides for both harvests
    rep2 = HarvestReport()
    assert sift(rep2, chain="base", contract=addr(2), tid=1,
                read_meta=Pieces(contracts).read, accepts_image=_wide) is None
    assert rep2.series == 1


def test_the_image_rule_is_the_larder_s():
    contracts = {addr(1): {"image": AR_IMG}, addr(2): {"image": IPFS_IMG}}
    h, _, _ = _harvester(list(contracts), contracts)
    assert len(h.harvest(60)[0]) == 2
    strict = CreatorHarvester(source="manifold", chain="base", sampler=Draws(list(contracts)),
                              eth_call=Pieces(contracts).eth_call,
                              token_uri=Pieces(contracts).token_uri,
                              read_meta=Pieces(contracts).read, rng=random.Random(3))
    refs, rep = strict.harvest(60)                       # the default rule: no Arweave
    assert len(refs) == 1 and rep.pieces.image_not_ca == {"arweave": 1}


def test_an_rpc_failure_on_one_contract_is_ours_and_the_run_goes_on():
    contracts = {addr(1): {"last": 2}, addr(2): {"erc721": TimeoutError()}, addr(4): {"last": 2}}
    h, _, _ = _harvester([addr(1), addr(2), ConnectionError(), addr(4)], contracts, canary=0)
    refs, rep = h.harvest(60)
    assert len(refs) == 2 and rep.lost == {"TimeoutError": 1, "ConnectionError": 1}
    assert "não medidos-NOSSO 2 (ConnectionError 1, TimeoutError 1)" in rep.render()


def test_the_canary_must_read_before_anything_is_drawn():
    """"0 alvos" tem de querer dizer que não havia."""
    assert MANIFOLD_BASE_CANARY_CREATION == 1            # a NUMBER, never an address
    contracts = {addr(2): {"last": 2}}
    h, draws, pieces = _harvester([None, addr(2)], contracts)
    with pytest.raises(HarvestBlind) as e:
        h.harvest(60)
    assert draws.asked == [1] and pieces.reads == []     # the canary, and nothing else
    assert "não vê as criações" in str(e.value) and not ADDRESS.search(str(e.value))


def test_a_node_without_old_state_is_blind_not_empty():
    h, draws, _ = _harvester([addr(1)], {addr(1): {}}, blind=ProbeBlind("precisa de arquivo"))
    with pytest.raises(HarvestBlind) as e:
        h.harvest(60)
    assert "arquivo" in str(e.value) and draws.asked == []


def test_the_report_counts_the_draw_s_rpc_calls():
    contracts = {addr(1): {"last": 5}, addr(2): {"erc721": False}}
    h, _, pieces = _harvester(list(contracts), contracts)
    _refs, rep = h.harvest(60)
    # one ERC-165 call per contract, plus the existence reads of the one with pieces
    assert rep.rpc_calls == 2 + len(pieces.existence) and rep.rpc_calls > 4
    again = h.harvest(60)[1]
    assert again.rpc_calls == rep.rpc_calls              # this run's, not a running total


def test_progress_is_said_every_ten_contracts_and_the_summary_last():
    contracts = {addr(i): {"last": 1} for i in range(1, 26)}
    h, _, _ = _harvester(list(contracts), contracts)
    said: list[str] = []
    h.harvest(25, notify=said.append)
    assert [m for m in said if m.startswith("harvest manifold: ")] == [
        m for m in said[:-1]] and len(said) == 3
    assert said[0].startswith("harvest manifold: 10/25 contratos")
    assert said[-1].startswith("harvest: manifold/base: 25 contrato(s)")
    assert not any(ADDRESS.search(m) for m in said)


def test_the_report_never_shows_what_it_holds():
    rep = CreatorHarvestReport(source="manifold", chain="base")
    rep.pieces.reads[f"base:{addr(1)}:1"] = object()
    assert not ADDRESS.search(repr(rep.pieces)) and not ADDRESS.search(rep.render())


def test_a_harvester_needs_the_chain_it_harvests():
    with pytest.raises(ValueError):
        CreatorHarvester(source="manifold", chain="", sampler=Draws([]), eth_call=None,
                         token_uri=None, read_meta=None)


# --------------------------------------------------------------------------- #
# 4. O comando inteiro: recusas à partida, depósito, despensa                   #
# --------------------------------------------------------------------------- #


class _BaseWorld(World):
    """O mundo de test_target_prepare, a responder pelo contrato pedido."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.token_reads = 0

    def read_token(self, chain, contract, token_id):
        self.token_reads += 1
        return super().read_token(chain, contract, token_id)


def _command(creations, contracts, *, arweave=2, chains=("base",), world=None,
             exposed=None, **kw):
    world = world or _BaseWorld()
    draws, pieces = Draws(creations, **kw), Pieces(contracts)
    eth_call, token_uri = CallCounter(pieces.eth_call), CallCounter(pieces.token_uri)
    harvester = CreatorHarvester(
        source="manifold", chain="base", sampler=draws, eth_call=eth_call,
        token_uri=token_uri, accepts_image=_wide, counters=(eth_call, token_uri),
        read_meta=lambda c, t: world.read_token("base", c, t), rng=random.Random(3))
    store = _Store()
    tw = TargetWiring(
        ports=None, pipeline=None, epoch=None, snapshot_store=None, scan_blocks=0,
        writability_rates={}, uniqueness_rates={},
        finder=_finder(world, accepts_uri=_wide, is_exposed=exposed),
        larder_store=store, creator_harvesters={"manifold": harvester},
        deposit_chains=frozenset(chains), arweave_gateways=arweave,
        arweave_meta=GatewayTally(), arweave_image=GatewayTally())
    return tw, store, draws, world


def _held(contract: str, token_id: int) -> Candidate:
    return Candidate(chain="base", contract=contract, token_id=token_id,
                     name="Some Two Words", name_onchain="Some Two Words",
                     description="d", image="ipfs://img", token_uri="ipfs://x",
                     artist="", metadata={"name": "Some Two Words"})


def test_a_run_draws_reads_once_deposits_and_says_what_it_did():
    contracts = {addr(i): {"last": 4} for i in range(1, 6)}
    contracts[addr(3)] = {"erc721": False}
    tw, store, _, world = _command(list(contracts), contracts)
    out = tw.harvest_source("manifold", 60)
    assert store.larder.size() == 4 and store.saves == 1
    assert all(c.chain == "base" for c in store.larder.candidates)
    assert world.token_reads == 4                        # the deposit read nothing again
    assert out.startswith("harvest manifold:\nmanifold/base: 5 contrato(s) sorteado(s)")
    assert "→ depósito: 4 guardado(s) de 4" in out
    assert re.search(r" · RPC do sorteio: \d+ chamada\(s\)", out)
    assert out.rstrip().endswith("despensa 0 → 4 (+4)")
    assert not ADDRESS.search(out) and "Some Two Words" not in out


def test_with_arweave_off_it_refuses_before_drawing_anything():
    """57 das 58 peças das sondas guardam o tokenURI em Arweave: correr sem
    ele era gastar o sorteio para relatar zeros."""
    contracts = {addr(1): {"last": 4}}
    tw, store, draws, world = _command([addr(1)], contracts, arweave=0)
    out = tw.harvest_source("manifold", 60)
    assert "⛔ o Arweave está desligado" in out and "nada foi varrido" in out
    assert "TARGET_ARWEAVE_GATEWAYS" in out
    assert draws.asked == [] and world.token_reads == 0 and store.saves == 0


def test_a_chain_the_live_check_cannot_read_is_refused_before_drawing():
    tw, store, draws, _ = _command([addr(1)], {addr(1): {}}, chains=("ethereum",))
    out = tw.harvest_source("manifold", 60)
    assert "saltada" in out and "TARGET_PUBLIC_RPCS_BASE" in out
    assert draws.asked == [] and store.saves == 0


def test_an_unknown_source_and_a_missing_larder_are_said_plainly():
    tw, _, draws, _ = _command([addr(1)], {addr(1): {}})
    assert "fonte não configurada" in tw.harvest_source("zora", 60) and draws.asked == []
    kw = dict(ports=None, pipeline=None, epoch=None, snapshot_store=None, scan_blocks=0,
              writability_rates={}, uniqueness_rates={})
    assert TargetWiring(**kw).harvest_source("manifold", 60) == "despensa não configurada"


def test_a_blind_draw_and_a_dead_rpc_are_ours_and_nothing_is_concluded():
    tw, store, _, _ = _command([None, addr(2)], {addr(2): {}})
    out = tw.harvest_source("manifold", 60)
    assert out.startswith("harvest manifold: ⛔") and "nada foi varrido" in out
    tw, store2, _, _ = _command([addr(1)], {addr(1): {}}, head_error=TimeoutError("x"))
    out = tw.harvest_source("manifold", 60)
    assert "NÃO MEDIDA — o RPC falhou (TimeoutError)" in out
    assert store.saves == 0 and store2.saves == 0


def test_at_most_two_targets_per_contract_in_the_larder():
    """A regra de 28/09, lida pela despensa (decisão do Pedro, 10/10): um
    contrato com dois alvos à espera não dá um terceiro — e nem se lhe
    procura uma peça."""
    assert MAX_TARGETS_PER_CONTRACT == 2
    contracts = {addr(1): {"last": 50}, addr(2): {"last": 50}, addr(3): {"last": 50}}
    tw, store, _, world = _command(list(contracts), contracts)
    store.larder.add(_held(addr(1), 901))
    store.larder.add(_held(addr(1), 902))                # full
    store.larder.add(_held(addr(2), 903))                # one: may give another
    out = tw.harvest_source("manifold", 60)
    assert "contrato-cheio 1" in out and "2 peça(s) lida(s)" in out
    assert store.larder.count_of("base", addr(1)) == 2
    assert store.larder.count_of("base", addr(2)) == 2
    assert store.larder.count_of("base", addr(3)) == 1
    assert world.token_reads == 2                        # the full one was never read
    assert out.rstrip().endswith("despensa 3 → 5 (+2)")


def test_the_block_harvest_keeps_its_own_reading_of_the_rule():
    """Lá são dois por contrato POR CORRIDA — não mexido."""
    import inspect
    default = inspect.signature(MintHarvester.harvest).parameters["max_per_contract"].default
    assert default == MAX_TARGETS_PER_CONTRACT == 2


def test_counting_is_per_chain_and_per_contract():
    larder = Larder()
    larder.add(_held(addr(1), 1))
    larder.add(_held(addr(1).upper().replace("0X", "0x"), 2))
    assert larder.count_of("base", addr(1)) == 2
    assert larder.count_of("ethereum", addr(1)) == 0 and larder.count_of("base", addr(2)) == 0


def test_every_guard_of_the_deposit_stands_between_the_draw_and_the_larder():
    contracts = {addr(1): {"last": 1}, addr(2): {"last": 1}}
    exposed = ExposedList.of({(addr(1), 1)})
    tw, store, _, _ = _command(list(contracts), contracts, exposed=exposed.has)
    out = tw.harvest_source("manifold", 60)
    assert "exposto 1" in out and store.larder.size() == 1
    assert store.larder.candidates[0].contract == addr(2)
    # …and the name check that counts words, and the paid check last
    world = _BaseWorld(unique=False)
    tw, store, _, _ = _command(list(contracts), contracts, world=world)
    out = tw.harvest_source("manifold", 60)
    assert store.larder.size() == 0 and "único 2" in out


def test_whatever_went_in_is_saved_even_if_the_deposit_breaks():
    contracts = {addr(1): {"last": 1}}
    tw, store, _, _ = _command([addr(1)], contracts)

    def boom(*a, **kw):
        raise RuntimeError("x")
    tw.finder.deposit = boom
    with pytest.raises(RuntimeError):
        tw.harvest_source("manifold", 60)
    assert store.saves == 1


# --------------------------------------------------------------------------- #
# 5. Produção                                                                   #
# --------------------------------------------------------------------------- #


def _built(**over):
    return build_target(settings(harvest_canary_ethereum="100:1", **over),
                        anthropic=object(), repo=FakeRepo(),
                        http_get=lambda u, h: "{}", http_post=rpc_ok,
                        http_get_bytes=lambda u, h: b"")


def test_production_wires_the_manifold_harvester_on_base():
    on = _built(target_arweave_gateways="https://ar-um.example/,https://ar-dois.example/")
    h = on.creator_harvesters["manifold"]
    assert (h.source, h.chain) == ("manifold", "base")
    assert h._canary == MANIFOLD_BASE_CANARY_CREATION                # noqa: SLF001
    assert len(h._counters) == 3                                     # noqa: SLF001
    assert h._accepts_image(AR_IMG) is True                          # noqa: SLF001
    assert on.probes["manifold"]._sampler is not h._sampler          # noqa: SLF001
    off = _built()
    assert off.creator_harvesters["manifold"]._accepts_image(AR_IMG) is False  # noqa: SLF001
    assert "⛔ o Arweave está desligado" in off.harvest_source("manifold", 5)


def test_without_a_base_rpc_there_is_no_such_harvester():
    w = _built(base_rpc_url="")
    assert w.creator_harvesters == {}
    assert "fonte não configurada" in w.harvest_source("manifold", 5)


def test_the_telegram_command_routes_a_source_through_the_same_job():
    src = open(main.__file__, encoding="utf-8").read()
    assert "parse_harvest_command(" in src
    assert '"harvest", lambda: target_wiring.harvest_source(source, n))' in src
    assert 'lambda: target_wiring.harvest(n, only=chain))' in src


def test_the_fake_node_speaks_the_deployer_s_event():
    """O sorteio de produção é o `DeployerSampler` da sonda — aqui só se
    confirma que o colector o aceita tal como é."""
    node = FakeNode([(100, addr(1)), (250, addr(2))])
    contracts = {addr(1): {"last": 2}, addr(2): {"last": 2}}
    pieces = Pieces(contracts)
    counted = CallCounter(node.call)
    h = CreatorHarvester(source="manifold", chain="base",
                         sampler=DeployerSampler(call=counted, deployer=D),
                         eth_call=pieces.eth_call, token_uri=pieces.token_uri,
                         read_meta=pieces.read, accepts_image=_wide,
                         counters=(counted,), rng=random.Random(1))
    refs, rep = h.harvest(60)
    assert len(refs) == 2 and rep.universe == 2 and rep.rpc_calls == counted.count > 0
    assert OT and PAD                                    # the event the sampler decodes

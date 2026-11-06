"""O mesmo criador nas últimas hunts — pedido do Pedro a 05/11, aprovado a 06/11.

O QUE ACONTECEU. A #29 ("cuban secret") e a #32 ("venezuelan secret") são da
mesma série, do mesmo criador, em contratos diferentes. A regra de então só
evitava o contrato da hunt anterior.

MEDIDO A 05/11, nas 22 peças reveladas (#11–#32): o campo de artista da
metadata tem nome em 11, só um endereço em 1 e nada em 10 — incluindo a #29 e
a #32; o owner() do contrato responde em 21 e é o que junta as duas (o único
par de contratos diferentes com o mesmo owner); o deployer não se mede sem
indexador.

DECIDIDO (Pedro, 06/11): criador = owner() do contrato (sem resposta: o
próprio contrato); evitar os criadores das últimas 5 hunts; regra suave — sem
nenhum contrato de fora, vale a regra de antes (só o contrato anterior) e
depois tudo; uma falha de RPC nunca recusa o /prepare.

AS ÚLTIMAS 5 HUNTS, E NÃO OS ÚLTIMOS 5 IDS USADOS: o `used` da despensa também
guarda os candidatos que o /prepare deita fora e as preparações substituídas
antes do /launch. As impressões (HMAC) das linhas das hunts, que o /prepare já
lia para não repetir alvos, separam uns dos outros. Nada novo na base.

Endereços e nomes aqui são todos sintéticos.
"""
from __future__ import annotations

import inspect
import random

import pytest
from test_target_prepare import World, _finder, _preparer

from finding_memeland.target.adapters import (
    SEL_OWNER,
    RpcError,
    contract_owner,
    owner_reader,
)
from finding_memeland.target.prepare import (
    RECENT_CREATOR_HUNTS,
    Candidate,
    Larder,
    RecentCreators,
    used_hmac,
)
from finding_memeland.target.sources import ChainRpc, ChainUnavailable

KEY = "synthetic-pool-key"

A = "0x" + "a1" * 20          # a recent hunt's contract (owner X)
B = "0x" + "b2" * 20          # ANOTHER contract of X — the #29/#32 case
C = "0x" + "c3" * 20          # owner Y
D = "0x" + "d4" * 20          # owner Z
E = "0x" + "e5" * 20          # owner W
Q = "0x" + "f6" * 20          # no owner() at all
X, Y, Z, W = ("0x" + h * 20 for h in ("11", "22", "33", "44"))
OWNERS = {A: X, B: X, C: Y, D: Z, E: W, Q: None}


class _FirstOfPool:
    """Always the first in the pool: the order is the list's, and the test
    speaks of cause, not luck."""

    def randrange(self, n):
        return 0


def _cand(contract: str, tid: int, chain: str = "ethereum") -> Candidate:
    meta = {"name": "Some Two Words", "image": f"ipfs://img{tid}", "description": "d"}
    return Candidate(chain=chain, contract=contract, token_id=tid,
                     name="Some Two Words", name_onchain="Some Two Words",
                     description="d", image=f"ipfs://img{tid}",
                     token_uri=f"ipfs://Qm{tid}", artist="", metadata=meta)


def _id(contract: str, tid: int, chain: str = "ethereum") -> str:
    return f"{chain}:{contract.lower()}:{tid}"


class Owners:
    """owner_of(chain, contract) over a dict; counts every read; `down` =
    contracts whose read fails on OUR side."""

    def __init__(self, owners=None, *, down=()):
        self.owners = dict(OWNERS if owners is None else owners)
        self.down = set(down)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, chain, contract):
        self.calls.append((chain, contract))
        if contract in self.down or "*" in self.down:
            raise ChainUnavailable("rpc down")
        return self.owners.get(contract)


def _prepare(larder, *, ran=(), owners=None, rng=None, **kw):
    world = World()
    kw.setdefault("key", KEY)
    kw.setdefault("used_hmacs", lambda: [used_hmac(cid, KEY) for cid in ran])
    if owners is not None:
        kw["owner_of"] = owners
    prep = _preparer(world, _finder(world), **kw)
    prep._rng = rng or _FirstOfPool()
    return prep.prepare(larder)


# --------------------------------------------------------------------------- #
# The draw (Larder.take)                                                       #
# --------------------------------------------------------------------------- #


def _mixed_larder() -> Larder:
    return Larder(candidates=[_cand(A, 1), _cand(B, 2), _cand(B, 3), _cand(C, 4),
                              _cand(D, 5), _cand(D, 6), _cand(E, 7)])


def test_with_nothing_refused_the_draw_is_the_one_of_before():
    """Mesma semente, mesmo alvo: a regra só muda o sorteio quando recusa."""
    for seed in range(300):
        before = _mixed_larder().take(random.Random(seed), avoid_contract=f"ethereum:{A}")
        now = _mixed_larder().take(random.Random(seed), avoid_contract=f"ethereum:{A}",
                                   avoid_creator=lambda _k: False)
        assert now.id() == before.id(), seed


def test_a_recent_creators_contract_is_never_drawn():
    refused = {f"ethereum:{B}"}
    seen = set()
    for seed in range(500):
        c = _mixed_larder().take(random.Random(seed), avoid_contract=f"ethereum:{A}",
                                 avoid_creator=lambda k: k in refused)
        seen.add(c.contract)
    assert seen == {C, D, E}                 # never B (the creator), never A (previous)


def test_the_draw_stays_uniform_by_contract_among_the_rest():
    """Uniforme por CONTRATO (29/09) entre os que sobram: um contrato com dez
    peças pesa o mesmo que um com uma."""
    larder = Larder(candidates=[_cand(B, 1), *(_cand(C, 10 + i) for i in range(10)),
                                _cand(D, 30), _cand(E, 40), _cand(E, 41), _cand(E, 42),
                                _cand(Q, 50)])
    refused = {f"ethereum:{B}", f"ethereum:{Q}"}
    counts = {C: 0, D: 0, E: 0}
    n = 6000
    for seed in range(n):
        c = larder.take(random.Random(seed), avoid_creator=lambda k: k in refused)
        counts[c.contract] += 1
    for contract, k in counts.items():
        assert abs(k / n - 1 / 3) < 0.03, (contract, k)


def test_with_every_contract_refused_the_rule_of_before_holds():
    """A hunt tem de correr: sem contrato de fora, vale a regra de antes — o
    contrato anterior continua evitado — e só depois tudo."""
    larder = Larder(candidates=[_cand(A, 1), _cand(B, 2), _cand(B, 3)])
    for seed in range(100):
        c = larder.take(random.Random(seed), avoid_contract=f"ethereum:{A}",
                        avoid_creator=lambda _k: True)
        assert c.contract == B
    only_a = Larder(candidates=[_cand(A, 1)])
    assert only_a.take(random.Random(1), avoid_contract=f"ethereum:{A}",
                       avoid_creator=lambda _k: True).contract == A


def test_the_last_hunts_skip_discards_and_replaced_preparations():
    h = [_id(A, 1), _id(C, 2), _id(D, 3), _id(E, 4), _id(B, 5), _id(Q, 6), _id(C, 7)]
    used = [h[0], _id(B, 90), h[1], _id(D, 91), _id(E, 92), h[2], _id(C, 93)]
    ran = set(h)
    assert Larder(used=used).last_hunts(5, ran=ran.__contains__) == [h[2], h[1], h[0]]
    assert Larder(used=h).last_hunts(5, ran=ran.__contains__) == h[::-1][:5]
    assert Larder(used=used).last_hunts(5, ran=lambda _c: False) == []


def test_five_hunts():
    assert RECENT_CREATOR_HUNTS == 5


# --------------------------------------------------------------------------- #
# Who is a recent creator (RecentCreators)                                     #
# --------------------------------------------------------------------------- #


def test_the_creator_is_the_owner_and_without_one_the_contract():
    rc = RecentCreators(recent=[_id(A, 1), _id(Q, 2)], owner_of=Owners())
    assert (rc.hunts, rc.creators, rc.read, rc.no_owner, rc.ours) == (2, 2, 1, 1, 0)
    assert rc(f"ethereum:{B}") is True       # another contract of A's owner
    assert rc(f"ethereum:{A}") is True and rc(f"ethereum:{Q}") is True
    assert rc(f"ethereum:{C}") is False
    assert rc.refused == {f"ethereum:{A}", f"ethereum:{B}", f"ethereum:{Q}"}


def test_owners_are_compared_whatever_the_chain():
    rc = RecentCreators(recent=[_id(A, 1, "base")], owner_of=Owners())
    assert rc(f"ethereum:{B}") is True


def test_each_contract_is_read_once():
    owners = Owners()
    rc = RecentCreators(recent=[_id(A, 1), _id(A, 2)], owner_of=owners)
    for _ in range(3):
        rc(f"ethereum:{C}")
        rc(f"ethereum:{A}")
    assert sorted(owners.calls) == [("ethereum", A), ("ethereum", C)]
    assert (rc.hunts, rc.creators) == (2, 1)


def test_an_owner_we_could_not_read_stands_for_itself():
    owners = Owners(down={A, C})
    rc = RecentCreators(recent=[_id(A, 1)], owner_of=owners)
    assert (rc.read, rc.ours) == (0, 1)
    assert rc(f"ethereum:{A}") is True       # a recent contract, read or not
    assert rc(f"ethereum:{B}") is False      # its owner is unknown to us now
    assert rc(f"ethereum:{C}") is False and rc.draw_ours == 1
    rc(f"ethereum:{C}")
    assert rc.draw_ours == 1                 # counted once


def test_switched_off_it_refuses_nothing_and_says_why():
    rc = RecentCreators(off="sem RPC")
    assert rc(f"ethereum:{A}") is False
    assert rc.render() == "não medido (sem RPC)"


# --------------------------------------------------------------------------- #
# /prepare                                                                      #
# --------------------------------------------------------------------------- #


def test_the_case_of_29_and_32():
    """O alvo da hunt anterior saiu de A; B é outro contrato do mesmo owner e
    vem primeiro no sorteio. Sem a regra, sai B — foi o que a #32 publicou."""
    larder = lambda: Larder(candidates=[_cand(B, 2), _cand(B, 3), _cand(C, 4)],  # noqa: E731
                            used=[_id(A, 1)])
    prepared, _ = _prepare(larder(), ran=[_id(A, 1)], owners=Owners())
    assert prepared.target.contract.lower() == C
    assert prepared.creator_note == (
        "última hunt, 1 criador(es) (owner lido em 1 de 1) · "
        "1 contrato(s) recusado(s) no sorteio")
    before, _ = _prepare(larder(), ran=[_id(A, 1)])          # no owner reader
    assert before.target.contract.lower() == B
    assert before.creator_note == "não medido (sem RPC)"


def test_discards_do_not_push_a_hunt_out_of_the_last_five():
    """Com os últimos 5 ids do `used`, os seis descartes deixavam A de fora."""
    used = [_id(A, 1)] + [_id(D, 80 + i) for i in range(6)]
    larder = Larder(candidates=[_cand(B, 2), _cand(C, 4)], used=used)
    prepared, _ = _prepare(larder, ran=[_id(A, 1)], owners=Owners())
    assert prepared.target.contract.lower() == C


def test_a_replaced_preparation_is_not_a_hunt():
    """Uma preparação substituída nunca foi publicada: o criador dela não
    conta. (O contrato dela continua a ser "o anterior" — a regra de antes.)"""
    larder = Larder(candidates=[_cand(B, 2), _cand(C, 4), _cand(D, 6)],
                    used=[_id(C, 1), _id(A, 9)])      # A: prepared, then replaced
    prepared, _ = _prepare(larder, ran=[_id(C, 1)], owners=Owners())
    assert prepared.target.contract.lower() == B      # counted, B and C would go: D


def test_only_the_last_five_hunts_count():
    hunts = [_id(A, 1)] + [_id(D, 10 + i) for i in range(5)]    # A is the 6th back
    larder = Larder(candidates=[_cand(B, 2), _cand(C, 4)], used=hunts)
    prepared, _ = _prepare(larder, ran=hunts, owners=Owners())
    assert prepared.target.contract.lower() == B
    assert prepared.creator_note.startswith("últimas 5 hunts, 1 criador(es)")


def test_the_hunt_runs_when_every_contract_left_is_a_recent_creators():
    larder = Larder(candidates=[_cand(B, 2)], used=[_id(A, 1)])
    prepared, _ = _prepare(larder, ran=[_id(A, 1)], owners=Owners())
    assert prepared.target.contract.lower() == B
    assert prepared.creator_note.endswith(
        "todos os contratos que sobravam eram de criadores recentes — valeu a "
        "regra de antes")


def test_an_rpc_down_never_refuses_the_prepare():
    """Falha nossa: cada contrato conta por si. Os contratos das hunts
    recentes continuam evitados; os outros saem."""
    larder = Larder(candidates=[_cand(A, 2), _cand(C, 4)],
                    used=[_id(A, 1), _id(Q, 3)])
    prepared, _ = _prepare(larder, ran=[_id(A, 1), _id(Q, 3)], owners=Owners(down={"*"}))
    assert prepared.target.contract.lower() == C
    assert prepared.creator_note == (
        "últimas 2 hunts, 2 criador(es) (owner lido em 0 de 2, RPC NOSSO 2) · "
        "1 contrato(s) recusado(s) no sorteio · 1 contrato(s) sem owner lido "
        "(RPC NOSSO) — contaram por si")


@pytest.mark.parametrize("kw, why", [
    ({}, "sem RPC"),
    ({"owner_of": Owners(), "key": ""}, "sem chave"),
    ({"owner_of": Owners(), "used_hmacs": lambda: 1 / 0},
     "base de dados: ZeroDivisionError"),
])
def test_without_rpc_key_or_database_the_draw_is_the_one_of_before(kw, why):
    larder = lambda: Larder(candidates=[_cand(B, 2), _cand(C, 4)],  # noqa: E731
                            used=[_id(A, 1)])
    prepared, after = _prepare(larder(), ran=[_id(A, 1)], **kw)
    assert prepared.target.contract.lower() == B             # what the old draw takes
    assert prepared.creator_note == f"não medido ({why})"
    assert after.size() == 1                                  # nothing else consumed


def test_an_empty_history_switches_it_off():
    larder = Larder(candidates=[_cand(B, 2), _cand(C, 4)], used=[_id(D, 90)])
    prepared, _ = _prepare(larder, ran=[], owners=Owners())
    assert prepared.creator_note == "não medido (nenhuma hunt no histórico da despensa)"


def test_a_spent_target_is_still_taken_out_and_never_counts_as_recent():
    """A leitura única das impressões serve as duas coisas: o que já correu
    sai da despensa (como antes), e o `used` lê-se ANTES de esse descarte o
    pôr no fim."""
    larder = Larder(candidates=[_cand(D, 5), _cand(B, 2), _cand(C, 4)],
                    used=[_id(A, 1)])
    prepared, after = _prepare(larder, ran=[_id(A, 1), _id(D, 5)], owners=Owners())
    assert prepared.target.contract.lower() == C
    assert _id(D, 5) in after.used and not any(c.contract == D for c in after.candidates)
    assert prepared.creator_note.startswith("última hunt,")


def test_the_owner_reads_are_few():
    """Uma leitura por hunt recente e uma por contrato que sai no sorteio —
    nunca uma por contrato da despensa."""
    contracts = ["0x" + f"{i:040x}" for i in range(1, 41)]
    larder = Larder(candidates=[_cand(c, i) for i, c in enumerate(contracts)],
                    used=[_id(A, 1), _id(C, 2), _id(D, 3), _id(E, 4), _id(Q, 5)])
    owners = Owners({**OWNERS, **{c: "0x" + "77" * 20 for c in contracts}})
    _prepare(larder, ran=larder.used, owners=owners, rng=random.Random(5))
    assert len(owners.calls) == 6


def test_the_report_says_counts_and_never_an_address():
    larder = Larder(candidates=[_cand(B, 2), _cand(C, 4)], used=[_id(A, 1)])
    prepared, _ = _prepare(larder, ran=[_id(A, 1)], owners=Owners())
    note = prepared.creator_note
    assert "0x" not in note
    for secret in (A, B, C, X, Y, A[2:10], X[2:10], "Some Two Words"):
        assert secret.lower() not in note.lower()


def test_the_prepare_reply_carries_the_note():
    from finding_memeland.target.wiring import TargetWiring

    class _St:
        def load(self):
            return Larder()

        def save(self, obj):
            pass

    class _Prep:
        def prepare(self, larder):
            return type("P", (), {"attempts": 1, "rarity_note": "",
                                  "creator_note": "não medido (sem RPC)"})(), larder
    w = TargetWiring(ports=None, pipeline=None, epoch=None, snapshot_store=None,
                     scan_blocks=0, writability_rates={}, uniqueness_rates={},
                     larder_store=_St(), prepared_store=_St(), larder_preparer=_Prep())
    assert w.prepare().endswith(" · criador: não medido (sem RPC)")


def test_production_wires_the_owner_reader():
    from finding_memeland.target import wiring
    assert "owner_of=owner_reader(rpcs)" in inspect.getsource(wiring)


# --------------------------------------------------------------------------- #
# owner() on the chain (adapters)                                              #
# --------------------------------------------------------------------------- #


def _rpc(answer, calls=None) -> ChainRpc:
    def eth_call(to, data):
        if calls is not None:
            calls.append((to, data))
        if isinstance(answer, BaseException):
            raise answer
        return answer
    return ChainRpc(chain="ethereum", eth_call=eth_call, get_code=lambda _a: "0x")


def test_owner_is_read_with_the_owner_selector():
    assert SEL_OWNER == "0x8da5cb5b"
    calls = []
    rpc = _rpc("0x" + "0" * 24 + "AB" * 20, calls)
    assert contract_owner(rpc, C) == "0x" + "ab" * 20
    assert calls == [(C, "0x8da5cb5b")]


@pytest.mark.parametrize("answer", [
    "0x" + "0" * 64,                                   # the zero address
    "0x",                                              # an address without code
    RpcError(3, "execution reverted", revert=True),    # no owner() at all
    ValueError("reverted"),                            # a revert, by the ChainRpc contract
])
def test_no_owner_is_the_contracts_answer(answer):
    assert contract_owner(_rpc(answer), C) is None


@pytest.mark.parametrize("answer", [
    ChainUnavailable("timeout"),
    RpcError(-32603, "Internal error", revert=False),  # the node, not the contract
])
def test_a_read_that_failed_on_our_side_raises(answer):
    with pytest.raises(ChainUnavailable):
        contract_owner(_rpc(answer), C)


def test_a_chain_without_rpc_is_our_gap():
    owner_of = owner_reader({"ethereum": _rpc("0x" + "0" * 24 + "ab" * 20)})
    assert owner_of("ethereum", C) == "0x" + "ab" * 20
    with pytest.raises(ChainUnavailable):
        owner_of("base", C)

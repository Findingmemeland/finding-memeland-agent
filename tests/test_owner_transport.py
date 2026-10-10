"""A verificação do dono que NÃO CONSEGUIU PERGUNTAR — o mesmo buraco da
unicidade, no passo ao lado.

10/10. Ao corrigir o "rede-NOSSO" da unicidade apareceu o irmão dele: a
`ChainEoaCheck` devolvia None tanto para "o ownerOf reverteu" (a peça foi
queimada — é DELA) como para "o RPC falhou" (é NOSSO), e o /prepare gastava o
alvo nos dois casos. Medido offline, com a guarda e o preparador verdadeiros
e o RPC a falhar SÓ nessa verificação: despensa 8 → 2, relatório "dono 6
(sem-veredicto 6)". E o RPC da guarda não tem repetições: basta uma chamada
das três falhar uma vez.

O que isto fixa (decisão do Pedro, 10/10):

  · um REVERT do ownerOf é a resposta da peça — o /prepare gasta-a, como
    sempre; idem um dono que é um contrato, e um ownerOf sem endereço;
  · uma FALHA DE TRANSPORTE é nossa — o /prepare MANTÉM o alvo ("leitura
    indisponível (dono:<tipo>) — candidato MANTIDO"), como o gateway, a
    pesquisa e a visão;
  · o relatório diz o tipo: "dono 1 (rpc-NOSSO:timeout 1)", "dono 1
    (sem-dono:ownerOf-reverte 1)". Só contagens e palavras.

A resposta da guarda não muda (True / False / None) — a refresh continua a
ler None como sempre. A colheita NÃO volta a perguntar pelo dono (não foi
decidido): conta, com o tipo, e segue.
"""
from __future__ import annotations

import json
import random
import urllib.error

import pytest
from test_probe import AR_META, GOOD, Chain, _accepts, _Sampler, addr
from test_target_prepare import S, World, _finder, _preparer

from finding_memeland.target.adapters import RpcError, chain_rpc
from finding_memeland.target.prepare import (
    Larder,
    PrepareRefused,
    ReadUnavailable,
    Source,
    Tally,
    TargetFinder,
)
from finding_memeland.target.probe import ContractProbe
from finding_memeland.target.refresh import TokenRead
from finding_memeland.target.sources import (
    ChainEoaCheck,
    ChainRpc,
    ChainUnavailable,
    rpc_failure_kind,
)

OWNER = "0x" + "11" * 20
C = "0x" + "cd" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64
IPFS = "ipfs://bafkrei" + "a" * 52
KEY = "s3cretKEY"
OWNER_WORD = "0x" + "00" * 12 + OWNER[2:]
REVERT = {"code": 3, "message": "execution reverted: ERC721: invalid token ID",
          "data": "0x08c379a0" + "00" * 32}


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(f"https://rpc.example/v2/{KEY}", code, "x", {}, None)


def _rpc_error(err: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "error": err})


def node(chain: str = "base", **steps) -> ChainRpc:
    """O JsonRpc VERDADEIRO sobre um `http_post` de mentira. Três passos —
    `contract_code` (eth_getCode do contrato), `owner_of` (o eth_call) e
    `owner_code` (eth_getCode do dono) — e cada um é: ausente (responde
    bem), uma excepção (o transporte levanta-a), um texto (o corpo tal e
    qual) ou um dict (o `result`)."""
    def post(url, body, headers):
        req = json.loads(body)
        method, params = req["method"], req["params"]
        if method == "eth_getCode":
            name, good = (("owner_code", "0x") if params[0] == OWNER
                          else ("contract_code", "0x6080"))
        elif params[0]["to"] == OWNER:
            return json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": "0x"})
        else:
            name, good = "owner_of", OWNER_WORD
        step = steps.get(name)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, str):
            return step
        if isinstance(step, dict):
            good = step["result"]
        return json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": good})
    return chain_rpc(chain=chain, url=f"https://rpc.example/v2/{KEY}", http_post=post)


def check(**steps) -> ChainEoaCheck:
    return ChainEoaCheck(rpcs={"base": node(**steps)})


# --------------------------------------------------------------------------- #
# 1. A guarda diz PORQUE não soube                                              #
# --------------------------------------------------------------------------- #


def test_a_revert_of_owner_of_is_the_token_s_answer():
    c = check(owner_of=_rpc_error(REVERT))
    assert c("base", C, 1) is None                          # the answer is what it was
    assert c.stats["no_owner_revert"] == 1 and c.stats["unverifiable"] == 1
    assert c.stats["transport"] == 0 and c.last_transport == ()


@pytest.mark.parametrize("word", ["0x", "0x" + "00" * 32, "0x" + "zz" * 32])
def test_an_owner_of_without_an_address_is_the_token_s_answer_too(word):
    """Vazio, o endereço zero, ou uma palavra que nem é hexadecimal."""
    c = check(owner_of={"result": word})
    assert c("base", C, 1) is None
    assert c.stats["no_owner_address"] == 1 and c.stats["unverifiable"] == 1
    assert c.stats["transport"] == 0 and c.stats["no_owner_revert"] == 0


@pytest.mark.parametrize("step", ["contract_code", "owner_of", "owner_code"])
def test_an_rpc_failure_at_any_of_the_three_calls_is_ours(step):
    c = check(**{step: TimeoutError("timed out")})
    assert c("base", C, 1) is None
    assert c.stats["transport"] == 1 and c.last_transport == ("timeout",)
    assert c.stats["unverifiable"] == 1                     # the refresh still reads this
    assert c.stats["no_owner_revert"] == 0 and c.stats["no_owner_address"] == 0


@pytest.mark.parametrize("answer, how", [
    (TimeoutError("timed out"), "timeout"),
    (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
    (_http_error(429), "429"),
    (_http_error(503), "503"),
    (ConnectionResetError(54, "reset"), "ligação"),
    ("<html>Just a moment…</html>", "não-json"),
    ("[]", "resposta-malformada"),
    ('{"jsonrpc": "2.0", "id": 1}', "resposta-malformada"),
    (_rpc_error({"code": -32005, "message": "limit exceeded"}), "limitado"),
    (_rpc_error({"code": 429, "message": "Too Many Requests"}), "limitado"),
    # cloudflare-eth says this for ITS OWN trouble, ankr the next one: read
    # as a burn, either would spend an honest target
    (_rpc_error({"code": -32603, "message": "Internal error"}), "erro-rpc:-32603"),
    (_rpc_error({"code": -32000, "message": "Unauthorized"}), "erro-rpc:-32000"),
])
def test_how_the_rpc_failed_is_named(answer, how):
    c = check(owner_of=answer)
    assert c("base", C, 1) is None
    assert c.last_transport == (how,) and c.stats["transport"] == 1
    assert c.stats["no_owner_revert"] == 0


def test_only_owner_of_can_revert_a_node_error_elsewhere_is_ours():
    """eth_getCode não executa o contrato: um "revert" ali é o nó a falhar."""
    for step in ("contract_code", "owner_code"):
        c = check(**{step: _rpc_error(REVERT)})
        assert c("base", C, 1) is None
        assert c.stats["no_owner_revert"] == 0
        assert c.last_transport == ("erro-rpc:3",)


def test_a_chain_without_an_rpc_here_is_ours():
    c = check()
    assert c("ethereum", C, 1) is None and c.last_transport == ("sem-rpc",)
    # an adapter bound to another chain is no RPC for this one (R1)
    crossed = ChainEoaCheck(rpcs={"ethereum": node("base")})
    assert crossed("ethereum", C, 1) is None and crossed.last_transport == ("sem-rpc",)
    assert c.stats["transport"] == 1 and crossed.stats["transport"] == 1


def test_an_rpc_that_cannot_see_the_contract_is_ours():
    c = check(contract_code={"result": "0x"})
    assert c("base", C, 1) is None and c.last_transport == ("rpc-cego",)
    assert c.stats["transport"] == 1 and c.stats["no_owner_revert"] == 0


def test_the_answers_themselves_did_not_change():
    person = check()
    assert person("base", C, 1) is True and person.stats["eoa"] == 1
    vault = check(owner_code={"result": "0x6080604052"})
    assert vault("base", C, 1) is False
    assert vault.stats["contract"] == 1 and vault.stats["contract_empty"] == 1
    upgraded = check(owner_code={"result": "0xef0100" + "22" * 20})     # EIP-7702
    assert upgraded("base", C, 1) is True
    for c in (person, vault, upgraded):
        assert c.stats["transport"] == 0 and c.stats["unverifiable"] == 0
        assert c.last_transport == ()


def test_why_is_about_the_last_question_only():
    rpc = Rpc()
    rpc.first = [TimeoutError("timed out")]              # the first ownerOf, and only it
    c = ChainEoaCheck(rpcs={"base": ChainRpc(
        chain="base", eth_call=rpc.eth_call, get_code=rpc.get_code)})
    assert c("base", C, 1) is None and c.last_transport == ("timeout",)
    assert c("base", C, 2) is True and c.last_transport == ()
    assert c.stats["transport"] == 1 and c.stats["eoa"] == 1


def test_only_counts_and_words_leave_the_check():
    c = check(owner_of=_http_error(429))
    c("base", C, 1)
    c2 = check(owner_of=_rpc_error({"code": -32603, "message": f"Internal error at {C}"}))
    c2("base", C, 1)
    shown = repr(c.stats) + repr(c.last_transport) + repr(c2.stats) + repr(c2.last_transport)
    for leak in (KEY, "rpc.example", C, C[2:10], OWNER[2:10], "Internal"):
        assert leak not in shown, leak


@pytest.mark.parametrize("exc, how", [
    (TimeoutError("timed out"), "timeout"),                     # a bare transport error
    (ConnectionResetError(54, "reset"), "ligação"),
    (_http_error(502), "502"),
    (ChainUnavailable("rpc:base: throttled"), "outro:ChainUnavailable"),   # nobody said how
    (RpcError(-32603, "Internal error", revert=False), "erro-rpc:-32603"),
    (KeyError("result"), "outro:KeyError"),
])
def test_a_failure_nobody_labelled_still_gets_a_word(exc, how):
    assert rpc_failure_kind(exc) == how


def test_the_adapter_s_cause_is_read_when_it_did_not_say_how():
    try:
        try:
            raise _http_error(429)
        except urllib.error.HTTPError as inner:
            raise ChainUnavailable("gateway: HTTPError") from inner
    except ChainUnavailable as e:
        assert rpc_failure_kind(e) == "429"


# --------------------------------------------------------------------------- #
# 2. O relatório: "dono" pelo tipo                                              #
# --------------------------------------------------------------------------- #


def _deposit(owner, *, unique=lambda *a: True):
    slept: list[float] = []
    asked: list[str] = []

    def name_is_unique(base, *a):
        asked.append(base)
        return unique(base, *a)

    f = TargetFinder(
        sources=[Source("x", "base", C)],
        total_supply=lambda c, k: 10, token_by_index=lambda c, k, i: i + 1,
        read_token=lambda c, k, t: TokenRead(
            token_uri=IPFS, metadata={"name": "Some Two Words", "image": IPFS}),
        probe_image=lambda u: (PNG, len(PNG)), owner_is_eoa=owner,
        name_is_unique=name_is_unique, rng=random.Random(0), sleep=slept.append)
    larder = Larder()
    rep = f.deposit(larder, [f"base:{C}:1"], chain_ok=lambda c: True)
    return rep, larder, slept, asked


@pytest.mark.parametrize("steps, label", [
    ({"owner_of": _rpc_error(REVERT)}, "sem-dono:ownerOf-reverte"),
    ({"owner_of": {"result": "0x" + "00" * 32}}, "sem-dono:sem-endereço"),
    ({"owner_of": TimeoutError("timed out")}, "rpc-NOSSO:timeout"),
    ({"owner_code": _http_error(429)}, "rpc-NOSSO:429"),
    ({"contract_code": {"result": "0x"}}, "rpc-NOSSO:rpc-cego"),
    ({"owner_of": _rpc_error({"code": -32005, "message": "limit exceeded"})},
     "rpc-NOSSO:limitado"),
    ({"owner_code": {"result": "0x6080604052"}}, "contrato:vazio"),
])
def test_the_report_says_why_under_dono(steps, label):
    rep, larder, _slept, _asked = _deposit(check(**steps))
    assert larder.size() == 0
    assert f"dono 1 ({label} 1)" in rep.render()
    assert rep.rejected.owner_kinds == {label: 1}


def test_a_check_that_does_not_say_why_reads_as_before():
    class Mute:
        def __init__(self):
            self.stats = {"unverifiable": 0}

        def __call__(self, *a):
            self.stats["unverifiable"] += 1
            return None

    rep, _larder, _slept, _asked = _deposit(Mute())
    assert "dono 1 (sem-veredicto 1)" in rep.render()
    rep, _larder, _slept, _asked = _deposit(lambda *a: None)
    assert "dono 1 (sem-veredicto 1)" in rep.render()


def test_the_harvest_does_not_ask_the_owner_again_and_spends_no_search():
    """Não foi decidido repetir o dono na colheita: conta-se, com o tipo, e
    segue — sem segunda volta, sem espera, e sem gastar a pesquisa paga."""
    rep, larder, slept, asked = _deposit(check(owner_of=TimeoutError("timed out")))
    assert larder.size() == 0 and rep.search.second_asked == 0
    assert slept == [] and asked == []
    assert "2.ª volta" not in rep.render()


def test_the_report_never_shows_an_address_or_a_key():
    out = ""
    for steps in ({"owner_of": _http_error(429)}, {"owner_of": _rpc_error(REVERT)},
                  {"owner_of": _rpc_error({"code": -32603, "message": f"boom {C}"})}):
        out += _deposit(check(**steps))[0].render()
    for leak in ("0x", KEY, "rpc.example", "Some Two Words", "boom"):
        assert leak not in out, leak


def test_the_probe_shows_it_in_both_columns():
    c1 = addr(1)
    chain = Chain({c1: {"tokens": {1: AR_META}}}, arweave={AR_META: GOOD})
    world = World()
    world.owner_is_eoa = ChainEoaCheck(
        rpcs={"base": node(owner_of=TimeoutError("timed out"))})
    p = ContractProbe(
        source="manifold", chain="base", sampler=_Sampler([c1]),
        eth_call=chain.eth_call, token_uri=chain.token_uri,
        read_token=chain.read_token, arweave_json=chain.arweave_json,
        finder=_finder(world, accepts_uri=_accepts), rng=random.Random(3))
    out = p.run(1).render()
    assert "com Arweave: passariam 0 de 1 — dono 1 (rpc-NOSSO:timeout 1)" in out


# --------------------------------------------------------------------------- #
# 3. O /prepare: uma falha nossa MANTÉM, uma resposta da peça GASTA             #
# --------------------------------------------------------------------------- #


class Rpc:
    """O RPC da guarda, por guião. `first`: o que as próximas chamadas ao
    ownerOf fazem (uma excepção cada); depois, `then` (uma excepção, ou
    None para responder). `owner_code`: o código do dono ("0x" = pessoa)."""

    def __init__(self):
        self.first: list = []
        self.then = None
        self.owner_code = "0x"
        self.owner_of_calls = 0

    def eth_call(self, to, data):
        if to == OWNER:                               # the ERC-1271 question
            return "0x"
        self.owner_of_calls += 1
        step = self.first.pop(0) if self.first else self.then
        if isinstance(step, BaseException):
            raise step
        return OWNER_WORD

    def get_code(self, addr_):
        return self.owner_code if addr_ == OWNER else "0x6080"


def _larder_of(n: int):
    """Uma despensa com `n` alvos, verificados pela guarda verdadeira com o
    RPC a responder. Quem chama muda depois o guião do RPC."""
    world = World(names={i: f"Alpha{i} Beta Gamma" for i in range(1, 2001)})
    rpc = Rpc()
    guard = ChainEoaCheck(rpcs={S.chain: ChainRpc(
        chain=S.chain, eth_call=rpc.eth_call, get_code=rpc.get_code)})
    world.owner_is_eoa = guard
    f = _finder(world)
    larder = Larder()
    f.fill(larder, want=n, max_draws=400)
    assert larder.size() == n
    world.paid_calls = 0
    rpc.owner_of_calls = 0
    return world, rpc, guard, f, larder


def _down(how: str) -> ChainUnavailable:
    """O que o JsonRpc levanta quando a chamada não chega ao fim."""
    e = ChainUnavailable(f"rpc:{S.chain}: {how}")
    e.how = how
    return e


@pytest.mark.parametrize("make, how", [
    (lambda: TimeoutError("timed out"), "timeout"),
    (lambda: ConnectionResetError(54, "reset"), "ligação"),
    (lambda: _down("429"), "429"),
    (lambda: _down("limitado"), "limitado"),
    (lambda: RpcError(-32603, "Internal error", revert=False), "erro-rpc:-32603"),
])
def test_prepare_keeps_every_target_when_the_rpc_fails_only_at_the_owner(make, how):
    """O ensaio de 10/10. Antes: despensa 8 → 2, "dono 6 (sem-veredicto 6)"
    — seis alvos verificados dados como queimados por uma chamada falhada."""
    world, rpc, _guard, f, larder = _larder_of(8)
    before = sorted(c.id() for c in larder.candidates)
    rpc.then = make()
    said: list[str] = []
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, f, notify=said.append).prepare(larder)
    assert larder.size() == 8
    assert sorted(c.id() for c in larder.candidates) == before and larder.used == []
    assert "despensa INTACTA" in str(e.value)
    kept = [m for m in said if "MANTIDO" in m]
    assert len(kept) == 6                                   # every attempt, none spent
    assert all(m == f"prepare: leitura indisponível (dono:{how}) — candidato "
                    "MANTIDO na despensa, tento outro" for m in kept)
    assert not any("descartado" in m for m in said)
    assert world.paid_calls == 0                            # the paid search was never reached
    assert world.full_reads == []
    assert rpc.owner_of_calls == 6


def test_an_rpc_that_comes_back_costs_no_target():
    world, rpc, _guard, f, larder = _larder_of(8)
    rpc.first = [TimeoutError("timed out")]
    said: list[str] = []
    prepared, left = _preparer(world, f, notify=said.append).prepare(larder)
    assert prepared.attempts == 2 and left.size() == 7
    assert left.used == [prepared.id()]
    assert len([m for m in said if "MANTIDO" in m]) == 1


def test_a_burned_target_is_still_spent():
    """O revert do ownerOf é a resposta da peça: sai da despensa."""
    world, rpc, _guard, f, larder = _larder_of(3)
    rpc.then = RpcError(3, "execution reverted: ERC721: invalid token ID", revert=True)
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, f).prepare(larder)
    assert larder.size() == 0
    assert "dono 3 (sem-dono:ownerOf-reverte 3)" in str(e.value)
    assert "indisponível-NOSSO" not in str(e.value)


def test_a_target_sold_into_a_contract_is_still_spent():
    world, rpc, _guard, f, larder = _larder_of(3)
    rpc.owner_code = "0x6080604052"
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, f).prepare(larder)
    assert larder.size() == 0 and "dono 3 (contrato:vazio 3)" in str(e.value)


def test_the_refusal_separates_what_was_ours_from_what_was_the_token_s():
    """Cinco chamadas falhadas e depois uma peça queimada: cinco mantidos,
    um gasto, e o relatório diz as duas coisas pelo nome."""
    world, rpc, _guard, f, larder = _larder_of(8)
    rpc.first = [TimeoutError("timed out")] * 5 + [
        RpcError(3, "execution reverted", revert=True)]
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, f).prepare(larder)
    msg = str(e.value)
    assert larder.size() == 7
    assert "indisponível-NOSSO 5 (dono:timeout 5)" in msg
    assert "dono 1 (sem-dono:ownerOf-reverte 1)" in msg


def test_strict_raises_for_ours_and_only_for_ours():
    def finder_with(owner):
        return TargetFinder(
            sources=[Source("x", "base", C)],
            total_supply=lambda c, k: 10, token_by_index=lambda c, k, i: i + 1,
            read_token=lambda c, k, t: TokenRead(
                token_uri=IPFS, metadata={"name": "Some Two Words", "image": IPFS}),
            probe_image=lambda u: (PNG, len(PNG)), owner_is_eoa=owner,
            name_is_unique=lambda *a: True, rng=random.Random(0))

    src = Source("larder", "base", C)
    f = finder_with(check(owner_of=TimeoutError("timed out")))
    tally = Tally()
    read, base = f.named_token(src, 1, tally, strict=True)
    with pytest.raises(ReadUnavailable) as e:
        f.verify(src, 1, read, base, tally, strict=True)
    assert str(e.value) == "dono:timeout" and e.value.step == "dono:timeout"
    assert tally.owner == 0 and tally.owner_kinds == {}      # never against the piece

    burned = finder_with(check(owner_of=_rpc_error(REVERT)))
    tally = Tally()
    assert burned.verify(src, 1, read, base, tally, strict=True) is None
    assert tally.owner_kinds == {"sem-dono:ownerOf-reverte": 1}

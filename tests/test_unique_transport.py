"""A pesquisa do nome que NÃO RESPONDEU — falha nossa, nunca do candidato.

10/10. O "rede-NOSSO" voltou ao "único": 2 das 6 peças que lá chegaram numa
sonda, 4 numa colheita na véspera — depois de 0 em 23 e 0 em 35. O Pedro
perguntou que erro era (429, timeout, outro) e a resposta honesta foi "não se
sabe": a guarda apanhava qualquer excepção, tentava três vezes em ~6 s e
contava `transport`, deitando fora o tipo. E ao seguir o caminho apareceu
pior: no /prepare a mesma falha GASTAVA o alvo da despensa — medido offline,
com a guarda e o preparador verdadeiros: despensa 8 → 2 num só /prepare,
igual para timeout, 429 e 503.

O que isto fixa (as três decisões do Pedro, num commit):

  1. cada pedido falhado é contado pelo TIPO — código HTTP, timeout, ligação,
     corpo ilegível — incluindo os que uma repetição depois salvou; só
     contagens e palavras, nunca a mensagem (pode citar o nome pesquisado);
  2. no /prepare, uma unicidade que falha por nossa causa é "leitura
     indisponível — candidato MANTIDO", como o gateway, o RPC e a visão;
  3. na colheita e na sonda, quem falhou SÓ por nossa causa espera em
     memória e é perguntado outra vez no fim da corrida, uma vez — só a
     pesquisa, e nunca antes de passar a janela medida a 10/09 (60 s).

O tratamento por tipo (esperar num 429, não repetir um 400) fica para depois
do primeiro relatório que disser qual é o tipo.
"""
from __future__ import annotations

import http.client
import json
import random
import re
import time
import urllib.error

import pytest
from test_creator_harvest import _BaseWorld, _built, _command
from test_probe import AR_META, GOOD, IPFS_IMG, IPFS_META, Chain, _accepts, _Sampler, addr
from test_target_prepare import World, _finder, _preparer

from finding_memeland.target.prepare import (
    SECOND_PASS_GAP_S,
    THIRD_PASS_GAP_S,
    Larder,
    Pending,
    PrepareRefused,
    ReadUnavailable,
    SearchPasses,
    Source,
    Tally,
    TargetFinder,
)
from finding_memeland.target.probe import ContractProbe
from finding_memeland.target.refresh import TokenRead
from finding_memeland.target.search_guard import (
    SEARCH_CONTROL_QUERY,
    SLOW_FAILURE_S,
    MarketNameUniqueness,
    OpenSeaSearch,
    is_server_error,
    search_failure_kind,
)

A = "0x" + "aa" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64
IPFS = "ipfs://bafkrei" + "a" * 52
ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")
# what a transport's message can carry and a report never may
SECRET = "Quiet+Lantern+Above"


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        f"https://api.example/search?query={SECRET}", code, "x", {}, None)


def _timeout() -> TimeoutError:
    return TimeoutError(f"timed out reading https://api.example/search?query={SECRET}")


class Search:
    """A pesquisa do mercado, por guião. Cada PEDIDO consome um passo: uma
    excepção (levanta-a) ou uma palavra — "ok" (só a própria peça),
    "namesake" (a peça e outra com o mesmo nome), "blind" (nada). `first`
    gasta-se antes de tudo, seja qual for a peça; depois o guião da peça
    (pela chave: o tokenId ou o contrato); depois `then`."""

    def __init__(self, script=None, *, first=(), then="ok", control=()):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.first = list(first)
        self.then = then
        self.asking = ""           # "chain:contract:tokenId", posto pela guarda
        self.tids: list[int] = []            # um por pedido
        self.contracts: list[str] = []       # idem
        # a pesquisa de controlo (10/10): o seu guião (excepções a levantar,
        # uma por pedido; depois responde) e quantas vezes foi feita
        self.control = list(control)
        self.control_then = None
        self.controls = 0

    def named_items(self, base):
        if base == SEARCH_CONTROL_QUERY:
            self.controls += 1
            step = self.control.pop(0) if self.control else self.control_then
            if isinstance(step, BaseException):
                raise step
            return []
        _chain, contract, tid = self.asking.split(":")
        self.tids.append(int(tid))
        self.contracts.append(contract)
        if self.first:
            step = self.first.pop(0)
        else:
            steps = self.script.get(contract) or self.script.get(int(tid)) or []
            step = steps.pop(0) if steps else self.then
        if isinstance(step, BaseException):
            raise step
        if step == "blind":
            return []
        rows = [(self.asking, base)]
        if step == "namesake":
            rows.append(("BASE:0xccc:1", base))
        return rows


class Guard(MarketNameUniqueness):
    """A guarda VERDADEIRA. A única coisa a mais é dizer à pesquisa de
    mentira por que peça se está a perguntar."""

    def __call__(self, base, chain, contract, token_id):
        self._search.asking = f"{chain}:{contract}:{token_id}"
        return super().__call__(base, chain, contract, token_id)


def guard(script=None, *, first=(), then="ok", retries=2, control=(), **kw) -> Guard:
    """Três pedidos por pergunta, como em produção; sem esperar entre eles.
    `control`: o que a pesquisa de controlo faz, pedido a pedido (por
    omissão, responde sempre)."""
    return Guard(search=Search(script, first=first, then=then, control=control),
                 page_size=50, retries=retries, sleep_s=0.0, **kw)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def finder(g, *, clock=None, gap=0.0, third=0.0, image_takes=0.0):
    """Um finder com a guarda verdadeira. `image_takes`: quanto o relógio
    anda em cada teste de imagem — é assim que o tempo passa numa corrida."""
    clock = clock or Clock()
    calls: dict[str, list] = {"image": [], "eoa": []}

    def probe_image(uri):
        calls["image"].append(uri)
        clock.now += image_takes
        return PNG, len(PNG)

    def owner_is_eoa(chain, contract, tid):
        calls["eoa"].append(tid)
        return True

    f = TargetFinder(
        sources=[Source("x", "ethereum", A)],
        total_supply=lambda c, k: 1000, token_by_index=lambda c, k, i: i + 1,
        read_token=lambda c, k, t: TokenRead(
            token_uri=IPFS, metadata={"name": f"Name {t} Two", "image": IPFS}),
        probe_image=probe_image, owner_is_eoa=owner_is_eoa, name_is_unique=g,
        rng=random.Random(0), retry_gap_s=gap, third_gap_s=third,
        sleep=clock.sleep, clock=clock)
    return f, calls, clock


def ref(tid: int) -> str:
    return f"ethereum:{A}:{tid}"


def deposit(g, tids, **kw):
    f, calls, clock = finder(g, **kw)
    larder = Larder()
    said: list[str] = []
    rep = f.deposit(larder, [ref(t) for t in tids], chain_ok=lambda c: True,
                    notify=said.append)
    return rep, larder, calls, clock, said


# --------------------------------------------------------------------------- #
# 1. A guarda diz COMO o pedido falhou                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("exc, kind", [
    (_http_error(429), "429"),
    (_http_error(503), "503"),
    (_http_error(400), "400"),
    (_timeout(), "timeout"),
    (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
    (urllib.error.URLError(ConnectionRefusedError(61, "refused")), "ligação"),
    (http.client.RemoteDisconnected("closed without response"), "ligação"),
    (json.JSONDecodeError("Expecting value", "<html>", 0), "corpo-ilegível"),
    (ValueError("search response without a results list"), "corpo-ilegível"),
    (KeyError("nft"), "outro:KeyError"),
])
def test_a_search_that_failed_says_how(exc, kind):
    assert search_failure_kind(exc) == kind
    g = guard({7: [exc, exc, exc]})
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None        # the answer is what it was
    assert g.stats["transport"] == 1
    assert g.last_transport == (kind,)
    # every request, not every check — and a 5xx is asked once (see section 6)
    assert g.failed_requests == {kind: 1 if is_server_error(kind) else 3}


@pytest.mark.parametrize("body", ["<html>Just a moment…</html>", "", "[]", '{"errors": ["x"]}'])
def test_an_answer_the_real_adapter_cannot_read_is_named_as_such(body):
    """Pela OpenSeaSearch verdadeira: um 200 que não é o JSON que conhecemos."""
    search = OpenSeaSearch(http_get=lambda url, headers: body, api_key="k")
    g = MarketNameUniqueness(search=search, page_size=50, retries=0, sleep_s=0.0)
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert g.last_transport == ("corpo-ilegível",)


def test_the_real_adapter_s_http_error_keeps_its_code():
    def get(url, headers):
        raise _http_error(429)
    g = MarketNameUniqueness(search=OpenSeaSearch(http_get=get, api_key="k"),
                             page_size=50, retries=1, sleep_s=0.0)
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert g.last_transport == ("429",) and g.failed_requests == {"429": 2}


def test_attempts_that_failed_differently_are_all_named_in_order():
    g = guard({7: [_http_error(429), _timeout(), _timeout()]})
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert g.last_transport == ("429", "timeout")
    assert g.failed_requests == {"429": 1, "timeout": 2}


def test_a_failure_the_retry_recovered_from_is_still_counted():
    """Sem isto ficava invisível: a pergunta passa, e ninguém sabe que o
    mercado nos limitou pelo caminho."""
    g = guard({7: [_http_error(429)]})
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is True
    assert g.stats["transport"] == 0 and g.stats["unique"] == 1
    assert g.last_transport == ()
    assert g.failed_requests == {"429": 1}


def test_how_it_failed_is_about_the_last_question_only():
    g = guard({7: [_timeout()] * 3})
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert g.last_transport == ("timeout",)
    assert g("Salt Harbor", "ethereum", "0xaaa", 8) is True
    assert g.last_transport == ()
    assert g("Salt Harbor", "ethereum", "0xaaa", 9) is True and g.stats["transport"] == 1


def test_a_verdict_is_never_mistaken_for_a_failure_of_ours():
    for then, answer, moved in (("namesake", False, "not_unique"), ("blind", None, "blind")):
        g = guard(then=then)
        assert g("Salt Harbor", "ethereum", "0xaaa", 7) is answer
        assert g.stats[moved] == 1 and g.stats["transport"] == 0
        assert g.last_transport == () and g.failed_requests == {}


def test_only_counts_and_words_leave_the_guard():
    g = guard({7: [_http_error(429), _timeout(), KeyError(SECRET)]})
    g("Salt Harbor", "ethereum", "0xaaa", 7)
    shown = repr(g.stats) + repr(g.failed_requests) + repr(g.last_transport)
    for leak in (SECRET, "Salt", "Harbor", "0xaaa", "api.example", "query="):
        assert leak not in shown, leak


# --------------------------------------------------------------------------- #
# 2. O /prepare: a despensa fica INTACTA                                        #
# --------------------------------------------------------------------------- #


def _larder_of(n: int, **search_kw):
    """Uma despensa com `n` alvos, verificados pela guarda verdadeira com a
    pesquisa a responder — e depois a pesquisa passa a seguir o guião."""
    world = World(names={i: f"Alpha{i} Beta Gamma" for i in range(1, 2001)})
    g = guard()
    world.name_is_unique = g
    f = _finder(world)
    larder = Larder()
    f.fill(larder, want=n, max_draws=400)
    assert larder.size() == n
    search = g._search
    search.first = list(search_kw.get("first", ()))
    search.then = search_kw.get("then", "ok")
    search.tids.clear()
    return world, g, f, larder


@pytest.mark.parametrize("make, how", [
    (_timeout, "timeout"),
    (lambda: _http_error(429), "429"),
    (lambda: _http_error(503), "503"),
])
def test_prepare_keeps_every_target_when_the_search_is_down(make, how):
    """O ensaio de 10/10. Antes: despensa 8 → 2, seis alvos verificados
    gastos por uma falha que não dizia nada sobre nenhum deles."""
    world, g, f, larder = _larder_of(8, then=make())
    before = sorted(c.id() for c in larder.candidates)
    said: list[str] = []
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, f, notify=said.append).prepare(larder)
    assert larder.size() == 8
    assert sorted(c.id() for c in larder.candidates) == before and larder.used == []
    assert "despensa INTACTA" in str(e.value) and "pesquisa" in str(e.value)
    kept = [m for m in said if "MANTIDO" in m]
    assert len(kept) == 6                                   # every attempt, none spent
    assert all(m == f"prepare: leitura indisponível (unicidade:{how}) — candidato "
                    "MANTIDO na despensa, tento outro" for m in kept)
    assert not any("descartado" in m for m in said)
    assert world.full_reads == []                           # nothing went on to the artwork
    assert g.stats["transport"] == 6
    assert len(g._search.tids) == 6 * (1 if is_server_error(how) else 3)


def test_a_search_that_comes_back_costs_no_target():
    """A primeira pergunta fica sem resposta e o candidato FICA; a seguinte
    responde e sela. A despensa perde um alvo: o que foi selado."""
    world, _g, f, larder = _larder_of(8, first=[_timeout()] * 3)
    said: list[str] = []
    prepared, left = _preparer(world, f, notify=said.append).prepare(larder)
    assert prepared.attempts == 2 and left.size() == 7
    assert left.used == [prepared.id()]
    assert len([m for m in said if "MANTIDO" in m]) == 1


def test_a_verdict_at_prepare_still_spends_the_target():
    """"Names stop being unique" continua a ser uma resposta sobre o alvo:
    só a falha NOSSA é que o mantém."""
    world, _g, f, larder = _larder_of(3, then="namesake")
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, f).prepare(larder)
    assert larder.size() == 0 and "único 3 (não-único 3)" in str(e.value)


def test_the_refusal_says_what_was_ours_and_how():
    """Cinco perguntas sem resposta (15 pedidos) e depois um homónimo: cinco
    mantidos, um gasto — e o relatório separa as duas coisas."""
    world, _g, f, larder = _larder_of(8, first=[_http_error(429)] * 15 + ["namesake"])
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, f).prepare(larder)
    msg = str(e.value)
    assert larder.size() == 7
    assert "indisponível-NOSSO 5 (unicidade:429 5)" in msg
    assert "único 1 (não-único 1)" in msg
    assert "as leituras NOSSAS falharam" in msg


def test_strict_raises_and_never_tallies_it_against_the_candidate():
    g = guard(then=_timeout())
    f, _calls, _clock = finder(g)
    tally = Tally()
    src = Source("larder", "ethereum", A)
    read, base = f.named_token(src, 5, tally, strict=True)
    with pytest.raises(ReadUnavailable) as e:
        f.verify(src, 5, read, base, tally, strict=True)
    assert str(e.value) == "unicidade:timeout" and e.value.step == "unicidade:timeout"
    assert tally.unique == 0 and tally.unique_kinds == {}
    # strict wins over defer: /prepare never gets a Pending back
    with pytest.raises(ReadUnavailable):
        f.verify(src, 5, read, base, tally, strict=True, defer=True)


def test_any_other_read_unavailable_is_still_a_read():
    assert ReadUnavailable("TimeoutError").step == "leitura"


# --------------------------------------------------------------------------- #
# 3. A colheita: segunda volta no fim, uma vez, em memória                      #
# --------------------------------------------------------------------------- #


def test_a_candidate_the_search_did_not_answer_is_asked_again_at_the_end():
    g = guard({2: [_timeout()] * 3})
    rep, larder, calls, _clock, _said = deposit(g, [1, 2, 3])
    assert rep.added == 3 and larder.size() == 3            # it used to be 2: a good one lost
    assert rep.search.second_asked == 1 and rep.search.second_rescued == 1
    assert rep.search.failed == {"timeout": 3}
    assert rep.rejected.unique == 0 and rep.rejected.found == 3
    # the order: 1, then 2 (three requests, no answer), then 3 — and ONLY THEN 2 again
    assert g._search.tids == [1, 2, 2, 2, 3, 2]
    out = rep.render()
    assert out.startswith("3 guardado(s) de 3")
    assert "pesquisa do único: 3 pedido(s) falhado(s) (timeout 3)" in out
    assert "2.ª volta: 1 repetido(s), 1 salvo(s)" in out
    assert "rede-NOSSO" not in out                          # nobody was lost to it


def test_the_free_checks_are_not_repeated_only_the_search_is():
    g = guard({2: [_timeout()] * 3})
    _rep, _larder, calls, _clock, _said = deposit(g, [1, 2, 3])
    assert len(calls["image"]) == 3 and calls["eoa"] == [1, 2, 3]


def test_a_second_failure_gets_a_third_and_last_pass():
    g = guard({2: [_http_error(429)] * 3 + [_timeout()] * 3})     # then it answers
    rep, larder, calls, _clock, _said = deposit(g, [1, 2, 3])
    assert rep.added == 3 and larder.size() == 3
    assert (rep.search.second_asked, rep.search.second_rescued) == (1, 0)
    assert (rep.search.third_asked, rep.search.third_rescued) == (1, 1)
    assert g._search.tids == [1, 2, 2, 2, 3, 2, 2, 2, 2]
    assert rep.rejected.unique == 0 and rep.rejected.found == 3
    assert len(calls["image"]) == 3 and calls["eoa"] == [1, 2, 3]   # still only the search
    out = rep.render()
    assert "pesquisa do único: 6 pedido(s) falhado(s) (429 3, timeout 3)" in out
    assert "2.ª volta: 1 repetido(s), 0 salvo(s) · 3.ª volta: 1 repetido(s), 1 salvo(s)" in out
    assert "rede-NOSSO" not in out and "índice-cego" not in out


def test_a_third_failure_is_final_and_there_is_never_a_fourth_pass():
    g = guard({2: [_timeout()] * 9 + ["ok"]}, control=[_timeout()])
    rep, larder, _calls, _clock, _said = deposit(g, [1, 2, 3])
    assert larder.size() == 2
    assert g._search.tids == [1, 2, 2, 2, 3] + [2] * 6            # nine in all, never a tenth
    assert (rep.search.third_asked, rep.search.third_rescued) == (1, 0)
    assert rep.rejected.unique == 1
    assert rep.rejected.unique_kinds == {"rede-NOSSO:timeout": 1}


def test_a_verdict_on_the_second_pass_is_the_verdict_it_is():
    g = guard({2: [_timeout()] * 3 + ["namesake"]})
    rep, larder, _calls, _clock, _said = deposit(g, [1, 2, 3])
    assert larder.size() == 2 and rep.search.second_rescued == 0
    assert rep.rejected.unique_kinds == {"não-único": 1}


def test_a_candidate_with_a_verdict_is_never_asked_again():
    for then, kind in (("namesake", "não-único"), ("blind", "índice-cego")):
        g = guard({2: [then]})
        rep, larder, _calls, clock, said = deposit(g, [1, 2, 3], gap=60.0)
        assert larder.size() == 2 and rep.rejected.unique_kinds == {kind: 1}
        assert rep.search.second_asked == 0 and g._search.tids == [1, 2, 3]
        assert clock.slept == [] and rep.search.render() == ""
        assert not any("volto a perguntar" in m for m in said)


def test_the_second_pass_waits_for_the_window_and_no_longer():
    """A falha foi aos 10 s; a corrida acabou aos 30 s; a janela são 60 s:
    espera 40 e pergunta."""
    g = guard({1: [_timeout()] * 3})
    rep, _larder, _calls, clock, _said = deposit(g, [1, 2, 3], gap=60.0, image_takes=10.0)
    assert clock.slept == [40.0] and rep.search.second_rescued == 1


def test_a_run_longer_than_the_window_does_not_wait_at_all():
    g = guard({1: [_timeout()] * 3})
    rep, _larder, _calls, clock, _said = deposit(g, list(range(1, 10)), gap=60.0,
                                                 image_takes=10.0)
    assert clock.slept == [] and rep.search.second_rescued == 1


def test_several_waiting_never_add_up_to_more_than_one_window():
    g = guard({1: [_timeout()] * 3, 2: [_timeout()] * 3})
    rep, _larder, _calls, clock, _said = deposit(g, [1, 2, 3], gap=60.0, image_takes=10.0)
    assert clock.slept == [40.0, 10.0] and sum(clock.slept) <= 60.0
    assert rep.search.second_asked == 2 and rep.search.second_rescued == 2


def test_the_window_is_the_one_measured_and_production_does_not_shorten_it():
    assert SECOND_PASS_GAP_S == 60.0
    assert _finder(World())._retry_gap == 60.0                       # noqa: SLF001
    # the wiring as production builds it: the larder's finder and the probe's,
    # both over the REAL guard — the one that says how a request failed
    w = _built()
    probe_finder = w.probes["manifold"]._finder                      # noqa: SLF001
    for f in (w.finder, probe_finder):
        assert f._retry_gap == 60.0 and f._sleep is time.sleep       # noqa: SLF001
        assert isinstance(f._name_is_unique, MarketNameUniqueness)   # noqa: SLF001
        assert f.search_counters() == {"failed": {}, "speed": {}, "control": {}}


def test_the_operator_is_told_once_in_counts():
    g = guard({1: [_timeout()] * 3, 3: [_timeout()] * 3})
    _rep, _larder, _calls, _clock, said = deposit(g, [1, 2, 3])
    (line,) = [m for m in said if "volto a perguntar" in m]
    assert line.startswith("deposit: 2 candidato(s) sem resposta da pesquisa do nome")
    assert not ADDRESS.search(" ".join(said)) and "Name" not in " ".join(said)


def test_what_waits_is_kept_in_memory_and_never_shown():
    g = guard(then=_timeout())
    f, _calls, _clock = finder(g)
    tally = Tally()
    src = Source("deposit", "ethereum", A)
    read, base = f.named_token(src, 5, tally)
    waiting = f.verify(src, 5, read, base, tally, defer=True)
    assert isinstance(waiting, Pending) and waiting.how == "timeout"
    assert tally.unique == 0                                # not a cause yet
    for shown in (repr(waiting), str(waiting), f"{waiting}"):
        assert shown == "Pending(<candidate>)"
    # …and nothing of it is left in what the run reports
    rep, _larder, _c, _k, said = deposit(guard({2: [_timeout()] * 6}), [1, 2, 3])
    assert not any(isinstance(v, Pending) for v in vars(rep).values())
    text = rep.render() + " ".join(said)
    assert not ADDRESS.search(text) and "Name" not in text and SECRET not in text


def test_without_defer_nothing_is_ever_handed_back_to_wait():
    """O /fill: um sorteio perdido é um sorteio repetido — conta-se, com o
    tipo, e não espera por ninguém."""
    g = guard(then=_http_error(503))
    f, _calls, clock = finder(g, gap=60.0)
    larder = Larder()
    tally = f.fill(larder, want=1, max_draws=3)
    assert larder.size() == 0 and tally.unique == 3
    assert tally.unique_kinds == {"rede-NOSSO:503": 3}
    assert clock.slept == []


def test_a_guard_that_does_not_say_how_is_still_ours():
    """Outra superfície, ou uma guarda de mentira: o contador `transport`
    chega para saber que é nosso; o rótulo fica como era."""
    class Mute:
        def __init__(self):
            self.stats = {"transport": 0}

        def __call__(self, *a):
            self.stats["transport"] += 1
            return None

    rep, larder, _calls, _clock, _said = deposit(Mute(), [1])
    assert larder.size() == 0
    assert (rep.search.second_asked, rep.search.third_asked) == (1, 1)
    # no control, so nothing says "it is the name": it stays ours to the end
    assert rep.rejected.unique_kinds == {"rede-NOSSO": 1}
    assert rep.search.render() == ("2.ª volta: 1 repetido(s), 0 salvo(s) · "
                                   "3.ª volta: 1 repetido(s), 0 salvo(s)")


def test_a_none_nobody_can_explain_is_not_asked_again():
    rep, larder, _calls, _clock, _said = deposit(lambda *a: None, [1])
    assert larder.size() == 0 and rep.search.second_asked == 0
    assert rep.rejected.unique_kinds == {"sem-veredicto": 1}


def test_the_search_line_is_empty_when_nothing_failed():
    rep, _larder, _calls, _clock, _said = deposit(guard(), [1, 2])
    assert rep.search == SearchPasses() and "pesquisa" not in rep.render()
    assert "2.ª volta" not in rep.render()


def test_a_recovered_request_shows_in_the_report_without_a_second_pass():
    rep, larder, _calls, _clock, _said = deposit(guard({2: [_http_error(429)]}), [1, 2])
    assert larder.size() == 2 and rep.search.second_asked == 0
    assert rep.render().endswith(
        "pesquisa do único: 1 pedido(s) falhado(s) (429 1) — rápidos 1, lentos 0")
    assert rep.search.control == {}                    # the question was answered: no control


def test_each_run_reports_its_own_failures_not_the_process_s():
    g = guard({1: [_timeout()] * 3})
    f, _calls, _clock = finder(g)
    first = f.deposit(Larder(), [ref(1)], chain_ok=lambda c: True)
    second = f.deposit(Larder(), [ref(2)], chain_ok=lambda c: True)
    assert first.search.failed == {"timeout": 3} and second.search.failed == {}
    # the same for what the control answered and for how fast the requests failed
    assert first.search.control == {"respondeu": 1} and second.search.control == {}
    assert first.search.speed == {"rápidos": 3} and second.search.speed == {}
    assert second.search == SearchPasses()


# --------------------------------------------------------------------------- #
# 4. /harvest manifold, de ponta a ponta                                        #
# --------------------------------------------------------------------------- #


def test_harvest_manifold_asks_again_after_the_window_and_says_so(monkeypatch):
    """O comando inteiro, com o finder como a produção o constrói: a espera
    é a verdadeira (60 s) — só o `sleep` é de mentira."""
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    contracts = {addr(i): {"last": 1} for i in range(1, 5)}
    world = _BaseWorld()
    g = guard({addr(2): [_http_error(429)] * 3})
    world.name_is_unique = g
    tw, store, _draws, _world = _command(list(contracts), contracts, world=world)
    out = tw.harvest_source("manifold", 60)
    assert store.larder.size() == 4                         # the one that got no answer is in
    # the guard's own pauses between attempts (zero here), then ONE wait: the window
    assert slept[:-1] == [0.0, 0.0] and 55.0 < slept[-1] <= 60.0
    assert g._search.contracts.count(addr(2)) == 4          # three requests, then one more
    assert g._search.contracts[-1] == addr(2)               # …and it was the last thing asked
    assert "→ depósito: 4 guardado(s) de 4" in out
    assert "pesquisa do único: 3 pedido(s) falhado(s) (429 3)" in out
    assert "2.ª volta: 1 repetido(s), 1 salvo(s)" in out
    assert not ADDRESS.search(out) and "Some Two Words" not in out


# --------------------------------------------------------------------------- #
# 5. A sonda: a mesma segunda volta, sem guardar nada                           #
# --------------------------------------------------------------------------- #


def probe(chain, order, g, *, gap=0.0):
    world = World()
    world.name_is_unique = g
    clock = Clock()
    f = _finder(world, accepts_uri=_accepts, retry_gap_s=gap, sleep=clock.sleep,
                clock=clock)
    return ContractProbe(
        source="manifold", chain="base", sampler=_Sampler(order),
        eth_call=chain.eth_call, token_uri=chain.token_uri,
        read_token=chain.read_token, arweave_json=chain.arweave_json,
        finder=f, rng=random.Random(3)), clock


def _three(uri=AR_META):
    order = [addr(1), addr(2), addr(3)]
    return Chain({c: {"tokens": {1: uri}} for c in order},
                 metadata={IPFS_META: {"name": "Grease Pencil Gospel", "image": IPFS_IMG}},
                 arweave={AR_META: GOOD}), order


def test_the_probe_asks_again_at_the_end_and_the_columns_still_add_up():
    chain, order = _three()
    g = guard({addr(2): [_timeout()] * 3})
    p, _clock = probe(chain, order, g)
    said: list[str] = []
    rep = p.run(3, notify=said.append)
    assert rep.tested == 3 and rep.with_arweave.passed == 3
    # "como hoje" keeps what stops the piece TODAY, also for the one that waited
    assert rep.today.passed == 0
    assert rep.today.causes == {("tokenURI fora de IPFS", "arweave"): 3}
    assert rep.search.second_asked == 1 and rep.search.second_rescued == 1
    assert rep.search.failed == {"timeout": 3}
    assert g._search.contracts == [addr(1), addr(2), addr(2), addr(2), addr(3), addr(2)]
    out = rep.render()
    assert out.splitlines()[-1] == (
        "pesquisa do único: 3 pedido(s) falhado(s) (timeout 3) — rápidos 3, lentos 0"
        " · controlo: respondeu 1, falhou 0 · 2.ª volta: 1 repetido(s), 1 salvo(s)")
    (line,) = [m for m in said if "volto a perguntar" in m]
    assert line.startswith("probe manifold: 1 peça(s) sem resposta da pesquisa do nome")
    assert not ADDRESS.search(out + line) and "Grease" not in out + line


def test_a_piece_the_larder_accepts_today_passes_in_both_columns_when_rescued():
    chain, order = _three(IPFS_META)
    p, _clock = probe(chain, order, guard({addr(3): [_http_error(503)]}))
    rep = p.run(3)
    assert rep.today.passed == 3 and rep.with_arweave.passed == 3
    assert rep.search.second_rescued == 1 and not rep.today.causes


def test_the_probe_names_what_failed_three_times_with_its_kind():
    chain, order = _three()
    g = guard({addr(2): [_http_error(429)] * 9}, control=[_http_error(429)])
    p, _clock = probe(chain, order, g)
    said: list[str] = []
    rep = p.run(3, notify=said.append)
    assert rep.with_arweave.passed == 2
    assert rep.with_arweave.causes == {("único", "rede-NOSSO:429"): 1}
    assert sum(rep.with_arweave.causes.values()) + rep.with_arweave.passed == rep.tested
    assert sum(rep.today.causes.values()) + rep.today.passed == rep.tested
    out = rep.render()
    assert "com Arweave: passariam 2 de 3 — único 1 (rede-NOSSO:429 1)" in out
    assert "2.ª volta: 1 repetido(s), 0 salvo(s) · 3.ª volta: 1 repetido(s), 0 salvo(s)" in out
    assert "controlo: respondeu 2, falhou 1" in out
    (third,) = [m for m in said if "3.ª e última volta" in m]
    assert third.startswith("probe manifold: 1 peça(s) sem resposta outra vez")


def test_the_probe_calls_it_a_blind_index_when_it_is_the_name():
    chain, order = _three()
    p, _clock = probe(chain, order, guard({addr(2): [_http_error(503)] * 3}))
    rep = p.run(3)
    cause = {("único", "índice-cego:pesquisa-503"): 1}
    assert rep.with_arweave.causes == cause and rep.with_arweave.passed == 2
    assert "único 1 (índice-cego:pesquisa-503 1)" in rep.render()


def test_the_probe_waits_for_the_window_too():
    chain, order = _three()
    p, clock = probe(chain, order, guard({addr(3): [_timeout()] * 3}), gap=60.0)
    rep = p.run(3)
    assert clock.slept == [60.0] and rep.search.second_rescued == 1


def test_a_probe_where_nothing_failed_reads_as_before():
    chain, order = _three()
    p, clock = probe(chain, order, guard(), gap=60.0)
    rep = p.run(3)
    assert rep.search == SearchPasses() and clock.slept == []
    assert len(rep.render().splitlines()) == 3              # the head and the two columns


# --------------------------------------------------------------------------- #
# 6. O 503 (10/10, depois de medido): controlo, sem repetições rápidas, 3.ª    #
#    volta — e "é o nome" passa a índice-cego                                  #
# --------------------------------------------------------------------------- #
#
# O 1.º /harvest manifold 150 disse o tipo: 25 pedidos falhados, todos 503; 24
# deles em 8 perguntas que falharam nas três tentativas (5 na 1.ª volta, 3 na
# 2.ª). As duas repetições rápidas salvaram, no máximo, 1 pergunta em 9;
# esperar um minuto deu resposta a 2 em 5. E os números não separavam o NOME
# (a pesquisa a falhar para aquela pergunta) do MOMENTO (a pesquisa em baixo
# para toda a gente). Decisões do Pedro:
#   · uma pesquisa de CONTROLO depois de cada pergunta falhada;
#   · num 5xx, NÃO repetir dentro da pergunta — a espera é a das voltas;
#   · uma 3.ª e última volta, cinco minutos depois, para quem falhou duas;
#   · se o controlo respondeu sempre e a 3.ª volta não salvou: é o nome —
#     conta como índice-cego (o mercado não pesquisa aquele nome).


@pytest.mark.parametrize("kind, yes", [
    ("500", True), ("502", True), ("503", True), ("599", True),
    ("429", False), ("400", False), ("404", False), ("timeout", False),
    ("ligação", False), ("corpo-ilegível", False), ("5030", False), ("50", False),
    ("", False), ("outro:KeyError", False),
])
def test_a_server_error_is_a_5xx_and_nothing_else(kind, yes):
    assert is_server_error(kind) is yes


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_a_5xx_is_asked_once_inside_the_question(code):
    g = guard(then=_http_error(code))
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert g._search.tids == [7]                              # one request, no quick retry
    assert g.failed_requests == {str(code): 1} and g.last_transport == (str(code),)


def test_what_is_not_a_5xx_keeps_its_three_attempts():
    for exc, kind in ((_http_error(429), "429"), (_timeout(), "timeout"),
                      (_http_error(400), "400")):
        g = guard(then=exc)
        assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
        assert g._search.tids == [7, 7, 7] and g.failed_requests == {kind: 3}


def test_a_5xx_ends_the_question_wherever_it_comes():
    g = guard({7: [_http_error(429), _http_error(503), "ok"]})
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert g._search.tids == [7, 7]                           # the "ok" was never asked for
    assert g.last_transport == ("429", "503")
    assert g.failed_requests == {"429": 1, "503": 1}


def test_the_guard_s_pauses_are_not_spent_on_a_5xx(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    g = Guard(search=Search(then=_http_error(503)), page_size=50)       # production pauses
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None and slept == []
    g = Guard(search=Search(then=_timeout()), page_size=50)
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None and slept == [2.0, 4.0]


def test_the_control_is_asked_once_after_a_question_that_failed():
    g = guard(then=_http_error(503))
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert g._search.controls == 1 and g.last_control is True         # it answered: the name
    assert g.control == {"respondeu": 1}
    assert g.failed_requests == {"503": 1}                            # the control is not a failure


def test_a_control_that_fails_too_says_it_is_the_moment():
    g = guard(then=_http_error(503), control=[_http_error(503)])
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert g.last_control is False and g.control == {"falhou": 1}
    assert g._search.controls == 1                                    # never retried
    assert g.failed_requests == {"503": 1}                            # only the name's request


def test_no_control_is_asked_when_the_question_was_answered():
    for then in ("ok", "namesake", "blind"):
        g = guard(then=then)
        g("Salt Harbor", "ethereum", "0xaaa", 7)
        assert g._search.controls == 0 and g.last_control is None and g.control == {}
    recovered = guard({7: [_http_error(429)]})
    assert recovered("Salt Harbor", "ethereum", "0xaaa", 7) is True
    assert recovered._search.controls == 0 and recovered.last_control is None


def test_the_control_s_answer_is_about_the_last_question_only():
    g = guard({7: [_http_error(503)]})
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None and g.last_control is True
    assert g("Salt Harbor", "ethereum", "0xaaa", 8) is True and g.last_control is None


def test_a_guard_without_a_control_asks_nothing():
    g = guard(then=_http_error(503), control_query=None)
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert g._search.controls == 0 and g.last_control is None and g.control == {}


def test_the_control_is_a_fixed_text_and_nobody_s_name():
    assert SEARCH_CONTROL_QUERY == "finding memeland"
    asked: list[str] = []

    class Recording(Search):
        def named_items(self, base):
            asked.append(base)
            return super().named_items(base)

    g = Guard(search=Recording(then=_http_error(503)), page_size=50, sleep_s=0.0)
    g("Quiet Lantern Above", "ethereum", "0xaaa", 7)
    assert asked == ["Quiet Lantern Above", SEARCH_CONTROL_QUERY]


def test_a_control_that_blows_up_never_breaks_the_verdict_nor_leaks():
    g = guard(then=_http_error(503), control=[KeyError(SECRET)])
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
    shown = repr(g.stats) + repr(g.control) + repr(g.failed_requests) + repr(g.failed_speed)
    assert g.last_control is False and SECRET not in shown


def test_a_failed_request_is_counted_as_fast_or_slow():
    clock = Clock()

    class Slow(Search):
        def named_items(self, base):
            clock.now += self.takes
            return super().named_items(base)

    search = Slow(then=_http_error(503))
    search.takes = SLOW_FAILURE_S - 0.5
    g = Guard(search=search, page_size=50, sleep_s=0.0, clock=clock)
    g("Salt Harbor", "ethereum", "0xaaa", 7)
    assert g.failed_speed == {"rápidos": 1}
    search.takes = SLOW_FAILURE_S
    g("Salt Harbor", "ethereum", "0xaaa", 8)
    assert g.failed_speed == {"rápidos": 1, "lentos": 1}
    assert sum(g.failed_speed.values()) == sum(g.failed_requests.values())


# -- as três voltas ---------------------------------------------------------- #


def test_three_failures_with_the_control_answering_is_the_name_a_blind_index():
    g = guard({2: [_http_error(503)] * 3})
    rep, larder, _calls, _clock, _said = deposit(g, [1, 2, 3])
    assert larder.size() == 2 and g._search.tids == [1, 2, 3, 2, 2]
    assert g._search.controls == 3
    assert rep.rejected.unique_kinds == {"índice-cego:pesquisa-503": 1}
    out = rep.render()
    assert "único 1 (índice-cego:pesquisa-503 1)" in out and "rede-NOSSO" not in out
    assert out.endswith(
        "pesquisa do único: 3 pedido(s) falhado(s) (503 3) — rápidos 3, lentos 0 · "
        "controlo: respondeu 3, falhou 0 · 2.ª volta: 1 repetido(s), 0 salvo(s) · "
        "3.ª volta: 1 repetido(s), 0 salvo(s)")


@pytest.mark.parametrize("control", [
    [_http_error(503)],                        # the control failed after the 1st failure
    [None, _http_error(503)],                  # …after the 2nd
    [None, None, _http_error(503)],            # …after the 3rd
    [_timeout(), _timeout(), _timeout()],      # every time
])
def test_one_failed_control_is_enough_to_keep_it_ours(control):
    """Se a pesquisa esteve em baixo para todos em qualquer das três, nada
    se soube sobre a peça: fica nosso."""
    g = guard({2: [_http_error(503)] * 3}, control=control)
    rep, larder, _calls, _clock, _said = deposit(g, [1, 2, 3])
    assert larder.size() == 2
    assert rep.rejected.unique_kinds == {"rede-NOSSO:503": 1}
    assert "índice-cego" not in rep.render()


def test_without_a_control_nothing_ever_becomes_a_blind_index():
    g = guard({2: [_http_error(503)] * 3}, control_query=None)
    rep, _larder, _calls, _clock, _said = deposit(g, [1, 2, 3])
    assert rep.rejected.unique_kinds == {"rede-NOSSO:503": 1}
    assert "controlo" not in rep.render()


def test_a_candidate_rescued_on_the_third_pass_is_no_blind_index():
    g = guard({2: [_http_error(503)] * 2})
    rep, larder, _calls, _clock, _said = deposit(g, [1, 2, 3])
    assert larder.size() == 3 and rep.rejected.unique == 0
    assert (rep.search.third_asked, rep.search.third_rescued) == (1, 1)
    assert rep.search.control == {"respondeu": 2}


def test_a_verdict_on_the_third_pass_is_the_verdict_it_is():
    g = guard({2: [_http_error(503)] * 2 + ["namesake"]})
    rep, larder, _calls, _clock, _said = deposit(g, [1, 2, 3])
    assert larder.size() == 2 and rep.rejected.unique_kinds == {"não-único": 1}


def test_the_third_pass_waits_five_minutes_from_the_second_failure():
    """A falha foi aos 10 s, a corrida acabou aos 30 s: espera 40 e pergunta
    (2.ª volta, aos 70 s); falha outra vez: espera 300 e pergunta (3.ª)."""
    g = guard({1: [_http_error(503)] * 2})
    rep, _larder, _calls, clock, _said = deposit(g, [1, 2, 3], gap=60.0, third=300.0,
                                                 image_takes=10.0)
    assert clock.slept == [40.0, 300.0] and rep.search.third_rescued == 1


def test_several_waiting_for_the_third_pass_wait_once_between_them():
    g = guard({1: [_http_error(503)] * 2, 2: [_http_error(503)] * 2})
    rep, _larder, _calls, clock, _said = deposit(g, [1, 2, 3], gap=60.0, third=300.0,
                                                 image_takes=10.0)
    assert rep.search.third_rescued == 2
    # each one five minutes after ITS second failure: together, one window
    assert clock.slept == [40.0, 10.0, 290.0, 10.0]
    assert sum(clock.slept[:2]) <= 60.0 and sum(clock.slept[2:]) == 300.0


def test_the_gaps_are_the_ones_decided_and_production_does_not_shorten_them():
    assert THIRD_PASS_GAP_S == 300.0 and SECOND_PASS_GAP_S == 60.0
    w = _built()
    for f in (w.finder, w.probes["manifold"]._finder):               # noqa: SLF001
        assert (f._retry_gap, f._third_gap) == (60.0, 300.0)         # noqa: SLF001
        assert f._name_is_unique._control_query == SEARCH_CONTROL_QUERY   # noqa: SLF001


def test_the_operator_is_told_of_each_pass_in_counts():
    g = guard({1: [_http_error(503)] * 3, 3: [_http_error(503)]})
    _rep, _larder, _calls, _clock, said = deposit(g, [1, 2, 3])
    (second,) = [m for m in said if "volto a perguntar" in m]
    (third,) = [m for m in said if "3.ª e última volta" in m]
    assert second.startswith("deposit: 2 candidato(s) sem resposta da pesquisa do nome")
    assert third.startswith("deposit: 1 candidato(s) sem resposta outra vez")
    assert said.index(second) < said.index(third)
    assert not ADDRESS.search(" ".join(said)) and "Name" not in " ".join(said)


def test_what_waits_carries_what_the_control_said_and_still_shows_nothing():
    g = guard(then=_http_error(503), control=[None, _http_error(503)])
    f, _calls, _clock = finder(g)
    src = Source("deposit", "ethereum", A)
    tally = Tally()
    read, base = f.named_token(src, 5, tally)
    first = f.verify(src, 5, read, base, tally, defer=True)
    assert (first.passes, first.controls, first.how) == (1, (True,), "503")
    second = f.ask_again(first, tally, last=False)
    assert isinstance(second, Pending)
    assert (second.passes, second.controls) == (2, (True, False))
    assert tally.unique == 0                                 # still no cause
    assert f.ask_again(second, tally, last=True) is None
    assert tally.unique_kinds == {"rede-NOSSO:503": 1}       # one control failed: ours
    assert repr(second) == "Pending(<candidate>)"


def test_two_passes_alone_never_make_a_blind_index():
    """A regra pede as TRÊS falhas com o controlo a responder: quem chamar a
    última volta mais cedo não condena ninguém."""
    g = guard(then=_http_error(503))
    f, _calls, _clock = finder(g)
    src = Source("deposit", "ethereum", A)
    tally = Tally()
    read, base = f.named_token(src, 5, tally)
    first = f.verify(src, 5, read, base, tally, defer=True)
    assert f.ask_again(first, tally, last=True) is None
    assert tally.unique_kinds == {"rede-NOSSO:503": 1}


# -- o que NÃO mudou --------------------------------------------------------- #


def test_prepare_keeps_the_target_whatever_the_control_says():
    """No /prepare não há voltas: um 503 mantém o alvo, e o controlo é só
    medição — "é o nome" nunca gasta um alvo da despensa."""
    for control in ((), [_http_error(503)] * 6):
        world, g, f, larder = _larder_of(8, then=_http_error(503))
        g._search.control = list(control)
        with pytest.raises(PrepareRefused) as e:
            _preparer(world, f).prepare(larder)
        assert larder.size() == 8 and "despensa INTACTA" in str(e.value)
        assert g._search.controls == 6


def test_the_fill_counts_a_lost_draw_as_ours_whatever_the_control_says():
    g = guard(then=_http_error(503))
    f, _calls, clock = finder(g, gap=60.0, third=300.0)
    tally = f.fill(Larder(), want=1, max_draws=3)
    assert tally.unique_kinds == {"rede-NOSSO:503": 3} and clock.slept == []


def test_the_probe_gives_each_waiting_piece_its_own_outcome():
    """Três peças à espera, três fins diferentes: salva na 2.ª volta, salva
    na 3.ª, e a que nunca responde — cada uma na sua coluna, pela ordem."""
    chain, order = _three()
    g = guard({addr(1): [_http_error(503)], addr(2): [_http_error(503)] * 2,
               addr(3): [_http_error(503)] * 3})
    p, _clock = probe(chain, order, g)
    rep = p.run(3)
    assert rep.with_arweave.passed == 2
    assert rep.with_arweave.causes == {("único", "índice-cego:pesquisa-503"): 1}
    assert (rep.search.second_asked, rep.search.second_rescued) == (3, 1)
    assert (rep.search.third_asked, rep.search.third_rescued) == (2, 1)
    assert rep.search.failed == {"503": 6} and rep.search.control == {"respondeu": 6}
    assert sum(rep.today.causes.values()) + rep.today.passed == rep.tested == 3


def test_the_deposit_gives_each_waiting_candidate_its_own_outcome():
    g = guard({1: [_http_error(503)], 2: [_http_error(503)] * 2,
               3: [_http_error(503)] * 3, 4: ["namesake"]})
    rep, larder, _calls, _clock, _said = deposit(g, [1, 2, 3, 4, 5])
    assert sorted(c.token_id for c in larder.candidates) == [1, 2, 5]
    assert rep.added == 3 and rep.rejected.found == 3
    assert rep.rejected.unique_kinds == {"não-único": 1, "índice-cego:pesquisa-503": 1}
    assert (rep.search.second_asked, rep.search.second_rescued) == (3, 1)
    assert (rep.search.third_asked, rep.search.third_rescued) == (2, 1)

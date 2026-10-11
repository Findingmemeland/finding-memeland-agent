"""A pesquisa do mercado responde AGORA? — a linha do /status, e o que o
/launch diz quando ela não responde.

10/10. Duas colheitas perderam candidatos para rajadas de 503 na pesquisa do
OpenSea — na segunda, 15 pedidos em 15, todos lentos, com o controlo a falhar
também, durante mais de seis minutos — e a página de estado deles dizia
"operational". Sem chave a pesquisa responde 401 à porta, por isso não há
como a testar de fora. O Pedro quer VER, antes de cada hunt:

  · uma pesquisa de controlo, com o que respondeu e em quanto tempo — pelo
    transporte das pesquisas a sério, com o tempo limite delas (25 s);
  · se falhar, a mesma com página de 1, na mesma linha: se ESSA responder,
    é a forma do nosso pedido que pesa;
  · os contadores da guarda de unicidade desde o arranque.

Corrigido no mesmo dia, depois de medir com a pesquisa em baixo (12:12–12:17
UTC): (1) a Cloudflare guarda as respostas boas — o mesmo pedido, repetido um
minuto depois, voltou em 0,1 s sem tocar na pesquisa — por isso o controlo
passa a furar a cache; (2) a linha dizia "um /launch seria recusado; uma hunt
no puzzle ficava em hold" sem o ter medido: o /launch e as pistas perguntam
POR CADEIA, e essa forma estava a responder. A linha mede agora as duas
formas, cada resposta com a contagem de resultados ao lado (com a mesma
página de 50, filtrada por uma cadeia, a pesquisa respondeu a um texto sem
resultados e deu 503 a um texto com a página cheia), e não diz o que não
mediu.

E o texto do /launch: quando a pesquisa não respondia, a recusa mandava
correr /prepare outra vez — o conselho de "a peça tornou-se pesquisável".
Segui-lo com a pesquisa de volta selava OUTRO alvo e deitava fora uma
preparação válida. Passa a dizer o que é: a pesquisa não respondeu, a
preparação mantém-se, repete-se o /launch.
"""
from __future__ import annotations

import inspect
import threading
import urllib.error

import pytest
from test_creator_harvest import _built

from finding_memeland import main
from finding_memeland.target import dryrun
from finding_memeland.target.dryrun import TargetWorld
from finding_memeland.target.hunt import LaunchRefused
from finding_memeland.target.integration import LAUNCH_SEARCH_SILENT
from finding_memeland.target.search_guard import (
    SEARCH_CONTROL_QUERY,
    MarketNameUniqueness,
    OpenSeaSearch,
    SearchProbe,
    probe_search,
)
from finding_memeland.target.wiring import TargetWiring

SECRET = "Quiet+Lantern+Above"


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        f"https://api.example/search?query={SECRET}", code, "x", {}, None)


class Clock:
    """Um relógio de mentira POR THREAD: as medições do /status correm ao
    mesmo tempo, cada uma na sua, e o tempo que uma gasta não pode aparecer
    na conta da outra."""

    def __init__(self):
        self._local = threading.local()

    @property
    def now(self) -> float:
        return getattr(self._local, "now", 100.0)

    @now.setter
    def now(self, value: float) -> None:
        self._local.now = value

    def __call__(self) -> float:
        return self.now


class Search:
    """Uma pesquisa de mentira: cada pedido demora `takes` segundos (no
    relógio de mentira) e responde, ou levanta `fails`. Perguntada de várias
    threads ao mesmo tempo: nada aqui muda a meio de um pedido."""

    def __init__(self, clock: Clock | None = None, *, takes: float = 0.0, fails=None,
                 rows=()):
        self.clock, self.takes, self.fails, self.rows = clock, takes, fails, list(rows)
        self.asked: list[str] = []
        self.chains: list[str] = []          # one per chain-filtered question

    def _answer(self, text, *, takes=None, fails=None):
        self.asked.append(text)
        if self.clock is not None:
            self.clock.now += self.takes if takes is None else takes
        fails = self.fails if fails is None else fails
        if fails is not None:
            raise fails
        return list(self.rows)

    def named_items(self, text):
        return self._answer(text)

    def item_ids(self, text, *, chain):
        self.chains.append(chain)
        return {ident for ident, _name in self._answer(text)}


def wiring(**kw) -> TargetWiring:
    kw.setdefault("market_surface", "opensea")
    return TargetWiring(ports=None, pipeline=None, epoch=None, snapshot_store=None,
                        scan_blocks=0, writability_rates={}, uniqueness_rates={}, **kw)


# --------------------------------------------------------------------------- #
# 1. A pesquisa de controlo                                                     #
# --------------------------------------------------------------------------- #


def test_a_control_that_answers_says_so_and_how_long_it_took():
    clock = Clock()
    search = Search(clock, takes=0.64)
    got = probe_search(search, clock=clock)
    assert got == SearchProbe(ok=True, kind="", seconds=pytest.approx(0.64), results=0)
    assert got.render() == "respondeu em 0,6 s (0 resultados)"
    assert search.asked == [SEARCH_CONTROL_QUERY]            # one request, the fixed text


@pytest.mark.parametrize("n, text", [(0, "(0 resultados)"), (1, "(1 resultado)"),
                                     (3, "(3 resultados)"), (50, "(50 resultados)")])
def test_an_answer_says_how_many_results_it_brought(n, text):
    """Medido a 10/10, por cadeia e com a mesma página: sem resultados a
    pesquisa respondeu, com a página cheia deu 503. "Respondeu" com nenhum
    resultado diz pouco sobre uma pergunta que tem cinquenta."""
    rows = [(f"ETHEREUM:0xbbb:{i}", "x") for i in range(n)]
    assert probe_search(Search(rows=rows)).render().endswith(text)


def test_the_control_can_be_asked_by_chain_as_the_clues_and_the_launch_ask():
    search = Search(rows=[("BASE:0xbbb:1", "x")])
    got = probe_search(search, chain="base")
    assert got.ok and got.results == 1
    assert search.chains == ["base"] and search.asked == [SEARCH_CONTROL_QUERY]


@pytest.mark.parametrize("exc, takes, text", [
    (_http_error(503), 14.2, "503 em 14,2 s"),
    (_http_error(429), 0.3, "429 em 0,3 s"),
    (_http_error(401), 0.2, "401 em 0,2 s"),
    (TimeoutError("timed out"), 25.0, "sem resposta em 25,0 s (timeout)"),
    (urllib.error.URLError(TimeoutError("timed out")), 25.04, "sem resposta em 25,0 s (timeout)"),
    (ConnectionResetError(54, "reset"), 1.0, "ligação em 1,0 s"),
    (ValueError("search response without a results list"), 0.5, "corpo-ilegível em 0,5 s"),
    (KeyError(SECRET), 0.1, "outro:KeyError em 0,1 s"),
])
def test_a_control_that_fails_says_how_and_how_long_it_took(exc, takes, text):
    clock = Clock()
    search = Search(clock, takes=takes, fails=exc)
    got = probe_search(search, clock=clock)                 # never raises
    assert got.ok is False and got.render() == text
    assert search.asked == [SEARCH_CONTROL_QUERY]            # never retried
    assert SECRET not in got.render() and "api.example" not in got.render()


def _recording():
    seen: list[str] = []

    def get(url, headers):
        seen.append(url)
        return '{"results": []}'

    return get, seen


def test_the_real_adapter_is_asked_the_control_with_the_page_it_was_built_with():
    get, seen = _recording()
    assert probe_search(OpenSeaSearch(http_get=get, api_key="k", size=50)).ok
    assert probe_search(OpenSeaSearch(http_get=get, api_key="k", size=1)).ok
    big, small = seen
    assert "query=finding%20memeland" in big and "&limit=50&cb=" in big
    assert "query=finding%20memeland" in small and "&limit=1&cb=" in small
    assert "chains=" not in big                              # unfiltered, as the harvest asks


def test_no_cache_can_answer_a_control():
    """Medido a 10/10 com a pesquisa em baixo: o mesmo pedido, repetido um
    minuto depois, voltou em 0,1 s com `cf-cache-status: HIT`. Um parâmetro
    que a API não conhece faz de cada controlo um pedido novo."""
    get, seen = _recording()
    search = OpenSeaSearch(http_get=get, api_key="k", size=50)
    for _ in range(3):
        probe_search(search)
    probe_search(search, chain="base")
    nonces = [u.rsplit("&cb=", 1)[1] for u in seen]
    assert len(set(nonces)) == 4 and all(n and n.isalnum() for n in nonces)
    assert "&chains=base&cb=" in seen[3]                     # filtered, and still no cache
    assert all(SEARCH_CONTROL_QUERY.replace(" ", "%20") in u for u in seen)


def test_a_real_search_stays_cacheable():
    """Uma resposta guardada à MESMA pergunta continua a ser a resposta."""
    get, seen = _recording()
    search = OpenSeaSearch(http_get=get, api_key="k", size=50)
    search.named_items("Salt Harbor")
    search.item_ids("Salt Harbor", chain="base")
    assert all("cb=" not in u for u in seen)
    assert seen[0].endswith("&limit=50") and seen[1].endswith("&chains=base")


def test_the_guard_s_own_control_cannot_come_from_a_cache_either():
    """Na colheita, o controlo decide "é o nome" ou "é o momento": vindo de
    uma cache, dizia "é o nome" de uma pesquisa em baixo."""
    seen: list[str] = []

    def get(url, headers):
        seen.append(url)
        if "cb=" in url:
            return '{"results": []}'
        raise _http_error(503)

    g = MarketNameUniqueness(search=OpenSeaSearch(http_get=get, api_key="k", size=50),
                             page_size=50, sleep_s=0.0)
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
    question, control = seen
    assert "cb=" not in question and "query=Salt%20Harbor" in question
    assert "query=finding%20memeland" in control and "&cb=" in control
    assert g.last_control is True


def test_a_chain_this_surface_cannot_search_fails_the_probe_and_asks_nothing():
    get, seen = _recording()
    got = probe_search(OpenSeaSearch(http_get=get, api_key="k"), chain="tezos")
    assert got.ok is False and seen == []


# --------------------------------------------------------------------------- #
# 2. A linha do /status                                                         #
# --------------------------------------------------------------------------- #


CHAINS = frozenset({"ethereum", "base"})
ROWS3 = [(f"ZORA:0xbbb:{i}", "x") for i in range(3)]


def test_one_request_and_one_plain_line_when_the_search_answers():
    clock = Clock()
    market, small = Search(clock, takes=0.6, rows=ROWS3), Search(clock, takes=0.4)
    line = wiring(market=market, market_small=small).search_line(clock=clock)
    assert line == ("pesquisa OpenSea, sem filtro: respondeu em 0,6 s (3 resultados)\n"
                    "  unicidade desde o arranque: nenhuma pergunta")
    assert len(market.asked) == 1 and small.asked == []      # the page of 1 is not spent


def test_the_line_measures_the_search_by_chain_too_one_question_per_chain():
    """A forma em que as pistas e o /launch perguntam: uma cadeia de cada
    vez, pelas cadeias em que o jogo guarda alvos — sempre as mesmas, por
    ordem, seja qual for a cadeia do alvo preparado."""
    clock = Clock()
    market = Search(clock, takes=0.5)
    lines = wiring(market=market, market_small=Search(),
                   deposit_chains=CHAINS).search_line(clock=clock).split("\n")
    assert lines == [
        "pesquisa OpenSea, sem filtro: respondeu em 0,5 s (0 resultados)",
        "  por cadeia: base respondeu em 0,5 s (0 resultados) · "
        "ethereum respondeu em 0,5 s (0 resultados)",
        "  unicidade desde o arranque: nenhuma pergunta",
    ]
    assert sorted(market.chains) == ["base", "ethereum"] and len(market.asked) == 3
    # the line reads by name, whatever order the set holds them in and
    # whichever answered first
    line = wiring(market=Search(), deposit_chains=frozenset(
        {"zora", "ethereum", "optimism", "base", "arbitrum", "polygon"})
        ).search_line().split("\n")[1]
    assert [part.split(" ")[0] for part in line[len("  por cadeia: "):].split(" · ")] \
        == ["arbitrum", "base", "ethereum", "optimism", "polygon", "zora"]


def test_a_failed_control_is_followed_by_the_page_of_one_on_the_same_line():
    clock = Clock()
    market = Search(clock, takes=10.2, fails=_http_error(503))
    small = Search(clock, takes=4.8, rows=ROWS3[:1])
    first, counters = wiring(market=market, market_small=small).search_line(
        clock=clock).split("\n")
    assert first == ("pesquisa OpenSea, sem filtro: ⚠️ 503 em 10,2 s · "
                     "página de 1: respondeu em 4,8 s (1 resultado)")
    assert counters == "  unicidade desde o arranque: nenhuma pergunta"
    assert market.asked == [SEARCH_CONTROL_QUERY] and small.asked == [SEARCH_CONTROL_QUERY]


def test_what_was_measured_on_10_10_reads_as_it_was():
    """Sem filtro, a página de 50 dava 503 aos 10 s e a de 1 respondia; por
    cadeia, o controlo (sem resultados) respondia. A linha diz as três
    coisas — e mais nada."""
    clock = Clock()

    class Split(Search):
        def named_items(self, text):
            return self._answer(text, takes=10.2, fails=_http_error(503))

        def item_ids(self, text, *, chain):
            self.chains.append(chain)
            return set(self._answer(text, takes=0.7 if chain == "ethereum" else 3.6))

    text = wiring(market=Split(clock), market_small=Search(clock, takes=4.8, rows=ROWS3[:1]),
                  deposit_chains=CHAINS).search_line(clock=clock)
    assert text.split("\n")[:2] == [
        "pesquisa OpenSea, sem filtro: ⚠️ 503 em 10,2 s · "
        "página de 1: respondeu em 4,8 s (1 resultado)",
        "  por cadeia: base respondeu em 3,6 s (0 resultados) · "
        "ethereum respondeu em 0,7 s (0 resultados)",
    ]


def test_every_shape_is_measured_at_the_same_time():
    """O /status demora o que demorar a medição mais lenta, não a soma: com
    duas cadeias e tudo a esgotar os 25 s eram 100 s em fila. Aqui cada
    pedido só responde quando os TRÊS estão em curso — em fila nunca se
    encontravam, e a linha dizia três falhas."""
    together = threading.Barrier(3, timeout=10)

    class Meeting(Search):
        def _answer(self, text, **kw):
            together.wait()
            return super()._answer(text, **kw)

    market = Meeting()
    lines = wiring(market=market, market_small=Search(),
                   deposit_chains=CHAINS).search_line().split("\n")
    assert "⚠️" not in "".join(lines) and len(market.asked) == 3
    assert lines[0].startswith("pesquisa OpenSea, sem filtro: respondeu em ")
    assert lines[1].startswith("  por cadeia: base respondeu em ")


def test_the_page_of_one_waits_for_the_page_of_fifty_to_fail():
    """As duas vão em fila, na mesma thread: a de 1 só se pede se a de 50
    falhar — pedi-la ao mesmo tempo era gastá-la sempre."""
    order: list[str] = []

    class Noting(Search):
        def __init__(self, label, **kw):
            super().__init__(**kw)
            self.label = label

        def _answer(self, text, **kw):
            order.append(f"{self.label}@{threading.get_ident()}")
            return super()._answer(text, **kw)

    wiring(market=Noting("50", fails=_http_error(503)),
           market_small=Noting("1")).search_line()
    (big, big_thread), (small, small_thread) = (o.split("@") for o in order)
    assert (big, small) == ("50", "1") and big_thread == small_thread


def test_a_measurement_that_blows_up_is_one_failed_measurement_not_a_lost_line():
    class Broken(Search):
        def item_ids(self, text, *, chain):
            if chain == "base":
                raise RuntimeError(f"boom {SECRET}")
            return super().item_ids(text, chain=chain)

    lines = wiring(market=Broken(), deposit_chains=CHAINS).search_line().split("\n")
    assert lines[1].startswith("  por cadeia: base ⚠️ outro:RuntimeError em ")
    assert " · ethereum respondeu em " in lines[1] and SECRET not in "\n".join(lines)


def test_the_line_never_says_what_it_did_not_measure():
    """"um /launch seria recusado; uma hunt no puzzle ficava em hold" saiu
    daqui a 10/10 sem ter sido medido — e era falso naquele momento."""
    down = Search(fails=_http_error(503))
    for text in (wiring(market=down, market_small=down, deposit_chains=CHAINS).search_line(),
                 wiring(market=Search(), deposit_chains=CHAINS).search_line()):
        for claim in ("/launch", "hold", "recusad", "puzzle"):
            assert claim not in text, claim


def test_both_failing_reads_as_both_failing():
    clock = Clock()
    market = Search(clock, takes=25.0, fails=TimeoutError("timed out"))
    small = Search(clock, takes=9.8, fails=_http_error(503))
    lines = wiring(market=market, market_small=small,
                   deposit_chains=frozenset({"base"})).search_line(clock=clock).split("\n")
    assert lines[0] == ("pesquisa OpenSea, sem filtro: ⚠️ sem resposta em 25,0 s (timeout) · "
                        "página de 1: 503 em 9,8 s")
    assert lines[1] == "  por cadeia: base ⚠️ sem resposta em 25,0 s (timeout)"


def test_without_a_page_of_one_the_line_still_says_what_failed():
    clock = Clock()
    market = Search(clock, takes=1.0, fails=_http_error(503))
    first = wiring(market=market).search_line(clock=clock).split("\n")[0]
    assert first == "pesquisa OpenSea, sem filtro: ⚠️ 503 em 1,0 s"


def test_the_surface_is_named_and_a_wiring_without_a_search_says_so():
    assert wiring(market=Search(), market_surface="rarible").search_line().startswith(
        "pesquisa Rarible, sem filtro: respondeu em ")
    assert wiring().search_line() == "pesquisa: não ligada"


def _guard_after(*outcomes) -> MarketNameUniqueness:
    """A guarda verdadeira, depois de umas perguntas: cada `outcome` é uma
    excepção (a pergunta falha) ou None (responde, e o alvo lá está)."""
    class Scripted:
        def __init__(self):
            self.steps = list(outcomes)
            self.control_fails = False

        def named_items(self, text):
            if text == SEARCH_CONTROL_QUERY:
                if self.control_fails:
                    raise _http_error(503)
                return []
            step = self.steps.pop(0)
            if isinstance(step, BaseException):
                raise step
            return [("ETHEREUM:0XAAA:7", text)]

    search = Scripted()
    g = MarketNameUniqueness(search=search, page_size=50, sleep_s=0.0)
    failures = 0
    for outcome in outcomes:
        # the control answers after the 1st failed question, fails after the
        # 2nd, answers after the 3rd…
        search.control_fails = failures % 2 == 1
        g("Salt Harbor", "ethereum", "0xaaa", 7)
        if outcome is not None:
            failures += 1
            assert g.last_transport
    return g


def test_the_counters_since_the_start_cost_nothing_and_add_up():
    g = _guard_after(None, _http_error(503), None, _http_error(503))
    line = wiring(market=Search(), uniqueness=g).search_line().split("\n")[-1]
    assert line == ("  unicidade desde o arranque: 4 pergunta(s) · 2 pedido(s) falhado(s) "
                    "(503 2) · controlo: respondeu 1, falhou 1")


def test_questions_that_all_answered_read_as_no_failed_request():
    g = _guard_after(None, None, None)
    line = wiring(market=Search(), uniqueness=g).search_line().split("\n")[-1]
    assert line == "  unicidade desde o arranque: 3 pergunta(s) · 0 pedido(s) falhado(s)"


def test_looking_does_not_move_what_the_guard_has_counted():
    """O /status pergunta à pesquisa, não à guarda: os contadores da guarda
    — e o que ela guarda da ÚLTIMA pergunta de uma colheita a correr —
    ficam como estavam."""
    g = _guard_after(_http_error(503))
    before = (dict(g.stats), dict(g.failed_requests), dict(g.control),
              g.last_transport, g.last_control)
    market = Search(fails=_http_error(503))
    w = wiring(market=market, market_small=Search(), uniqueness=g)
    w.search_line()
    w.search_line()
    assert (dict(g.stats), dict(g.failed_requests), dict(g.control),
            g.last_transport, g.last_control) == before
    assert len(market.asked) == 2                            # one per look, no more


def test_the_line_shows_words_and_numbers_and_nothing_it_was_answered():
    rows = [("ETHEREUM:0xbbb:1", "Quiet Lantern Above")]
    ok = wiring(market=Search(rows=rows)).search_line()
    down = wiring(market=Search(fails=_http_error(503)),
                  market_small=Search(rows=rows)).search_line()
    for text in (ok, down):
        for leak in ("Quiet", "Lantern", "0xbbb", SECRET, "api.example",
                     SEARCH_CONTROL_QUERY):
            assert leak not in text, leak


# --------------------------------------------------------------------------- #
# 3. Produção                                                                   #
# --------------------------------------------------------------------------- #


def test_production_wires_the_search_its_page_of_one_and_the_guard():
    w = _built(opensea_api_key="k")
    assert isinstance(w.market, OpenSeaSearch) and isinstance(w.market_small, OpenSeaSearch)
    assert (w.market._size, w.market_small._size) == (50, 1)            # noqa: SLF001
    assert isinstance(w.uniqueness, MarketNameUniqueness)
    assert w.uniqueness._search is w.market                              # noqa: SLF001
    assert w.finder._name_is_unique is w.uniqueness                      # noqa: SLF001
    # `_built` answers every GET with "{}": no results list → an unreadable body
    lines = w.search_line().split("\n")
    assert lines[0].startswith("pesquisa OpenSea, sem filtro: ⚠️ corpo-ilegível em ")
    assert " · página de 1: corpo-ilegível em " in lines[0]
    assert lines[1].startswith("  por cadeia: ")
    assert [part.split(" ")[0] for part in lines[1][len("  por cadeia: "):].split(" · ")] \
        == sorted(w.deposit_chains)
    assert lines[-1] == "  unicidade desde o arranque: nenhuma pergunta"


def test_the_other_surface_gets_the_same_line_under_its_own_name():
    w = _built()                                             # no OpenSea key: Rarible
    assert w.market_surface == "rarible" and w.market_small._size == 1   # noqa: SLF001
    assert w.uniqueness._search is w.market                              # noqa: SLF001
    assert w.search_line().startswith("pesquisa Rarible, sem filtro: ")


def test_status_reads_the_line_and_survives_it_blowing_up():
    src = inspect.getsource(main)
    assert "lines.append(target_wiring.search_line())" in src
    assert 'lines.append(f"pesquisa: ilegível ({type(e).__name__})")' in src


# --------------------------------------------------------------------------- #
# 4. O /launch com a pesquisa em baixo                                          #
# --------------------------------------------------------------------------- #


class _Silent:
    """A pesquisa não respondeu: a guarda não pôde verificar nada."""
    ok, found, blind = False, None, False
    detail = "unverifiable after 3 attempts (HTTP Error 503: Service Unavailable)"


class _Blind:
    """O canário falhou: a pesquisa respondeu e a peça não aparece."""
    ok, found, blind = False, None, True
    detail = "canary failed"


class _Found:
    ok, found, blind = False, True, False
    detail = "target surfaced in a marketplace search"


def _refused(verdict=None, *, raises=None) -> tuple[str, TargetWorld]:
    w = TargetWorld()
    if raises is not None:
        def boom(text, **kw):
            raise raises
        w.ports.recheck_clue_one = boom
    else:
        w.guard_verdict = verdict
    with pytest.raises(LaunchRefused) as e:
        w.launch()
    return str(e.value), w


def test_a_search_that_did_not_answer_keeps_the_preparation_and_says_launch_again():
    text, w = _refused(_Silent())
    assert LAUNCH_SEARCH_SILENT == ("a pesquisa não respondeu — a preparação mantém-se; "
                                    "repete o /launch quando o /status disser que responde")
    assert text == (f"⛔ launch recusado — {LAUNCH_SEARCH_SILENT} "
                    "(unverifiable after 3 attempts (HTTP Error 503: Service Unavailable)). "
                    "Nada foi publicado.")
    assert "/prepare" not in text                            # the advice that cost a target
    assert w.posts() == [] and not w.rig.repo.hunts          # nothing posted, nothing written
    assert w.prepared_slot is not None                       # the preparation is still there


def test_the_same_preparation_launches_once_the_search_is_back():
    text, w = _refused(_Silent())
    sealed = w.prepared_slot.commitment
    w.guard_verdict = dryrun._GuardOk()
    hunt = w.launch()
    assert hunt.target.commitment == sealed                  # the SAME target, not another
    assert len(w.posts()) == 1 and "commitment v2:" in w.posts()[0]


def test_a_recheck_that_blows_up_reads_the_same_way():
    text, w = _refused(raises=TimeoutError("timed out"))
    assert text == (f"⛔ launch recusado — {LAUNCH_SEARCH_SILENT} (TimeoutError). "
                    "Nada foi publicado.")
    assert w.prepared_slot is not None and w.posts() == []


@pytest.mark.parametrize("verdict, why", [
    (_Found(), "a peça tornou-se pesquisável desde ontem"),
    (_Blind(), "a pesquisabilidade não pôde ser verificada"),
])
def test_an_answer_about_the_piece_still_asks_for_a_new_preparation(verdict, why):
    """A pesquisa RESPONDEU — a peça aparece com a pista, ou não aparece
    nem pelo próprio nome. Isso é sobre a peça: o conselho não mudou."""
    text, w = _refused(verdict)
    assert text == (f"⛔ Clue 1 recusada no launch — {why} ({verdict.detail}). "
                    "Corre /prepare outra vez.")
    assert "a pesquisa não respondeu" not in text and w.posts() == []

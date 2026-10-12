"""Em que estado está a pesquisa do mercado AGORA? — a linha do /status, e o
que o /launch diz quando ela não responde.

10/10. Duas colheitas perderam candidatos para rajadas de 503 na pesquisa do
OpenSea — na segunda, 15 pedidos em 15, todos lentos, com o controlo a falhar
também, durante mais de seis minutos — e a página de estado deles dizia
"operational". Sem chave a pesquisa responde 401 à porta, por isso não há
como a testar de fora. O Pedro quer VER, antes de cada hunt, pesquisas de
controlo com o que responderam e em quanto tempo — pelo transporte das
pesquisas a sério, com o tempo limite delas (25 s) — e os contadores da
guarda de unicidade desde o arranque.

Corrigido no mesmo dia, depois de medir com a pesquisa em baixo (12:12–12:17
UTC): (1) a Cloudflare guarda as respostas boas — o mesmo pedido, repetido um
minuto depois, voltou em 0,1 s sem tocar na pesquisa — por isso o controlo
passa a furar a cache; (2) a linha dizia "um /launch seria recusado; uma hunt
no puzzle ficava em hold" sem o ter medido: o /launch e as pistas perguntam
POR CADEIA, e essa forma estava a responder. A linha mede as duas formas,
cada resposta com a contagem de resultados ao lado, e não diz o que não
mediu.

11/10 — DOIS CONTROLOS. O texto do controlo era uma pergunta PESADA: deu "⚠️
503" dois dias seguidos, e nesse segundo dia um /prepare selou um alvo à
primeira. Medido com três nomes já revelados, sem filtro: dois deram 503 e um
respondeu em 1,7 s; por cadeia responderam os três. A linha "sem filtro"
passa a mostrar o controlo LEVE e o PESADO, pedidos ao mesmo tempo (leve
falha = em baixo; só a pesada falha = degradada; as duas respondem = boa), a
"página de 1" sai, e por cadeia pergunta-se com o texto leve.

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
from collections import Counter
from urllib.parse import quote

import pytest
from test_creator_harvest import _built

from finding_memeland import main
from finding_memeland.target import dryrun
from finding_memeland.target.dryrun import TargetWorld
from finding_memeland.target.hunt import LaunchRefused
from finding_memeland.target.integration import LAUNCH_SEARCH_SILENT
from finding_memeland.target.search_guard import (
    SEARCH_HEAVY_QUERY,
    SEARCH_LIGHT_QUERY,
    MarketNameUniqueness,
    OpenSeaSearch,
    SearchProbe,
    probe_search,
)
from finding_memeland.target.wiring import TargetWiring

SECRET = "Quiet+Lantern+Above"
LIGHT, HEAVY = SEARCH_LIGHT_QUERY, SEARCH_HEAVY_QUERY


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
    relógio de mentira) e responde, ou levanta `fails`. O texto PESADO pode
    ter um comportamento só dele — `heavy=dict(takes=…, fails=…, rows=…)` —
    e sem isso responde como qualquer outro. Perguntada de várias threads
    ao mesmo tempo: nada aqui muda a meio de um pedido."""

    def __init__(self, clock: Clock | None = None, *, takes: float = 0.0, fails=None,
                 rows=(), heavy: dict | None = None):
        self.clock, self.takes, self.fails, self.rows = clock, takes, fails, list(rows)
        self.heavy = heavy
        self.asked: list[str] = []
        self.chains: list[str] = []          # one per chain-filtered question

    def _answer(self, text, *, takes=None, fails=None):
        self.asked.append(text)
        own = self.heavy if text == HEAVY and self.heavy is not None else {}
        if self.clock is not None:
            self.clock.now += own.get("takes", self.takes) if takes is None else takes
        fails = own.get("fails", self.fails) if fails is None else fails
        if fails is not None:
            raise fails
        return list(own.get("rows", self.rows))

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
# 1. Uma pesquisa de controlo                                                   #
# --------------------------------------------------------------------------- #


def test_a_control_that_answers_says_so_and_how_long_it_took():
    clock = Clock()
    search = Search(clock, takes=0.64)
    got = probe_search(search, clock=clock)
    assert got == SearchProbe(ok=True, kind="", seconds=pytest.approx(0.64), results=0)
    assert got.render() == "respondeu em 0,6 s (0 resultados)"
    assert search.asked == [LIGHT]                           # one request: the light text


def test_the_heavy_text_is_asked_when_it_is_the_one_wanted():
    search = Search()
    assert probe_search(search, query=HEAVY).ok and search.asked == [HEAVY]


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
    assert search.chains == ["base"] and search.asked == [LIGHT]


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
    assert search.asked == [LIGHT]                           # never retried
    assert SECRET not in got.render() and "api.example" not in got.render()


def _recording():
    seen: list[str] = []

    def get(url, headers):
        seen.append(url)
        return '{"results": []}'

    return get, seen


def test_the_real_adapter_is_asked_each_control_with_the_page_of_fifty():
    get, seen = _recording()
    search = OpenSeaSearch(http_get=get, api_key="k", size=50)
    assert probe_search(search).ok and probe_search(search, query=HEAVY).ok
    light, heavy = seen
    assert f"query={quote(LIGHT)}" in light and "&limit=50&cb=" in light
    assert "query=finding%20memeland" in heavy and "&limit=50&cb=" in heavy
    assert "chains=" not in light + heavy                    # unfiltered, as the harvest asks


def test_no_cache_can_answer_a_control():
    """Medido a 10/10 com a pesquisa em baixo: o mesmo pedido, repetido um
    minuto depois, voltou em 0,1 s com `cf-cache-status: HIT`. Um parâmetro
    que a API não conhece faz de cada controlo um pedido novo."""
    get, seen = _recording()
    search = OpenSeaSearch(http_get=get, api_key="k", size=50)
    for query in (LIGHT, LIGHT, HEAVY):
        probe_search(search, query=query)
    probe_search(search, chain="base")
    nonces = [u.rsplit("&cb=", 1)[1] for u in seen]
    assert len(set(nonces)) == 4 and all(n and n.isalnum() for n in nonces)
    assert "&chains=base&cb=" in seen[3]                     # filtered, and still no cache


def test_a_real_search_stays_cacheable():
    """Uma resposta guardada à MESMA pergunta continua a ser a resposta."""
    get, seen = _recording()
    search = OpenSeaSearch(http_get=get, api_key="k", size=50)
    search.named_items("Salt Harbor")
    search.item_ids("Salt Harbor", chain="base")
    assert all("cb=" not in u for u in seen)
    assert seen[0].endswith("&limit=50") and seen[1].endswith("&chains=base")


def test_the_guard_s_own_controls_cannot_come_from_a_cache_either():
    """Na colheita, os controlos dizem o estado da pesquisa: vindos de uma
    cache, diziam "boa" de uma pesquisa em baixo."""
    seen: list[str] = []

    def get(url, headers):
        seen.append(url)
        if "cb=" in url:
            return '{"results": []}'
        raise _http_error(503)

    g = MarketNameUniqueness(search=OpenSeaSearch(http_get=get, api_key="k", size=50),
                             page_size=50, sleep_s=0.0)
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
    question, light, heavy = seen
    assert "cb=" not in question and "query=Salt%20Harbor" in question
    assert f"query={quote(LIGHT)}" in light and "&cb=" in light
    assert "query=finding%20memeland" in heavy and "&cb=" in heavy
    assert g.last_state == "boa"


def test_a_chain_this_surface_cannot_search_fails_the_probe_and_asks_nothing():
    get, seen = _recording()
    got = probe_search(OpenSeaSearch(http_get=get, api_key="k"), chain="tezos")
    assert got.ok is False and seen == []


# --------------------------------------------------------------------------- #
# 2. A linha do /status                                                         #
# --------------------------------------------------------------------------- #


CHAINS = frozenset({"ethereum", "base"})
ROWS3 = [(f"ZORA:0xbbb:{i}", "x") for i in range(3)]
ROWS10 = [(f"ZORA:0xbbb:{i}", "x") for i in range(10)]
NO_QUESTION = "  unicidade desde o arranque: nenhuma pergunta"


def test_both_controls_and_one_plain_line_when_the_search_is_good():
    clock = Clock()
    market = Search(clock, takes=0.6, rows=ROWS3, heavy={"takes": 4.4, "rows": ROWS10})
    line = wiring(market=market).search_line(clock=clock)
    assert line == ("pesquisa OpenSea, sem filtro: leve respondeu em 0,6 s (3 resultados) · "
                    "pesada respondeu em 4,4 s (10 resultados)\n" + NO_QUESTION)
    assert Counter(market.asked) == {LIGHT: 1, HEAVY: 1}     # two requests, no more


def test_a_degraded_search_reads_as_the_light_answering_and_the_heavy_not():
    """O estado de 11/10: a pesquisa responde ao controlo leve e dá 503 ao
    pesado — está viva, e há nomes para os quais não tem resposta."""
    clock = Clock()
    market = Search(clock, takes=1.7, rows=ROWS10,
                    heavy={"takes": 10.3, "fails": _http_error(503)})
    first, counters = wiring(market=market).search_line(clock=clock).split("\n")
    assert first == ("pesquisa OpenSea, sem filtro: leve respondeu em 1,7 s (10 resultados) · "
                     "pesada ⚠️ 503 em 10,3 s")
    assert counters == NO_QUESTION
    assert Counter(market.asked) == {LIGHT: 1, HEAVY: 1}     # failing costs no extra request


def test_a_search_that_is_down_reads_as_both_failing():
    clock = Clock()
    market = Search(clock, takes=25.0, fails=TimeoutError("timed out"),
                    heavy={"takes": 10.1, "fails": _http_error(503)})
    lines = wiring(market=market, deposit_chains=frozenset({"base"})).search_line(
        clock=clock).split("\n")
    assert lines[0] == ("pesquisa OpenSea, sem filtro: leve ⚠️ sem resposta em 25,0 s "
                        "(timeout) · pesada ⚠️ 503 em 10,1 s")
    assert lines[1] == "  por cadeia: base ⚠️ sem resposta em 25,0 s (timeout)"


def test_the_line_measures_the_search_by_chain_too_one_question_per_chain():
    """A forma em que as pistas e o /launch perguntam: uma cadeia de cada
    vez, pelas cadeias em que o jogo guarda alvos — sempre as mesmas, por
    ordem, seja qual for a cadeia do alvo preparado. Com o texto LEVE."""
    clock = Clock()
    market = Search(clock, takes=0.5)
    lines = wiring(market=market, deposit_chains=CHAINS).search_line(
        clock=clock).split("\n")
    assert lines == [
        "pesquisa OpenSea, sem filtro: leve respondeu em 0,5 s (0 resultados) · "
        "pesada respondeu em 0,5 s (0 resultados)",
        "  por cadeia: base respondeu em 0,5 s (0 resultados) · "
        "ethereum respondeu em 0,5 s (0 resultados)",
        NO_QUESTION,
    ]
    assert sorted(market.chains) == ["base", "ethereum"]
    assert Counter(market.asked) == {LIGHT: 3, HEAVY: 1}     # the heavy one: unfiltered only
    # the line reads by name, whatever order the set holds them in and
    # whichever answered first
    line = wiring(market=Search(), deposit_chains=frozenset(
        {"zora", "ethereum", "optimism", "base", "arbitrum", "polygon"})
        ).search_line().split("\n")[1]
    assert [part.split(" ")[0] for part in line[len("  por cadeia: "):].split(" · ")] \
        == ["arbitrum", "base", "ethereum", "optimism", "polygon", "zora"]


def test_what_was_measured_on_11_10_reads_as_it_was():
    """Sem filtro, o nome leve respondeu em 1,7 s com dez resultados e o
    texto pesado deu 503 aos 10 s; por cadeia, o nome leve respondeu em 1 s
    com dois resultados em ethereum. A linha diz isso — e mais nada."""
    clock = Clock()

    class Split(Search):
        def item_ids(self, text, *, chain):
            self.chains.append(chain)
            if chain == "ethereum":
                self._answer(text, takes=1.0)
                return {"ETHEREUM:0xaaa:1", "ETHEREUM:0xaaa:2"}
            return set(self._answer(text, takes=2.0)) - set(ROWS10)

    market = Split(clock, takes=1.7, rows=ROWS10,
                   heavy={"takes": 10.3, "fails": _http_error(503)})
    text = wiring(market=market, deposit_chains=CHAINS).search_line(clock=clock)
    assert text.split("\n")[:2] == [
        "pesquisa OpenSea, sem filtro: leve respondeu em 1,7 s (10 resultados) · "
        "pesada ⚠️ 503 em 10,3 s",
        "  por cadeia: base respondeu em 2,0 s (0 resultados) · "
        "ethereum respondeu em 1,0 s (2 resultados)",
    ]


def test_every_measurement_is_made_at_the_same_time():
    """O /status demora o que demorar a medição mais lenta, não a soma. Aqui
    cada pedido só responde quando os QUATRO estão em curso (leve, pesada e
    duas cadeias) — em fila nunca se encontravam, e a linha dizia falhas."""
    together = threading.Barrier(4, timeout=10)

    class Meeting(Search):
        def _answer(self, text, **kw):
            together.wait()
            return super()._answer(text, **kw)

    market = Meeting()
    lines = wiring(market=market, deposit_chains=CHAINS).search_line().split("\n")
    assert "⚠️" not in "".join(lines) and len(market.asked) == 4
    assert lines[0].startswith("pesquisa OpenSea, sem filtro: leve respondeu em ")
    assert lines[1].startswith("  por cadeia: base respondeu em ")


def test_there_is_no_page_of_one_any_more():
    """A "página de 1" media se era o tamanho do pedido que pesava; o
    controlo leve diz mais com o mesmo pedido. A linha não a pede nem a
    escreve, falhe o que falhar."""
    market = Search(fails=_http_error(503))
    text = wiring(market=market, deposit_chains=CHAINS).search_line()
    assert "página de 1" not in text and len(market.asked) == 4
    assert not hasattr(wiring(), "market_small")


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
    daqui a 10/10 sem ter sido medido — e era falso naquele momento. Nem um
    controlo sabe se um nome concreto vai ter resposta (11/10): a linha
    mostra medições, não previsões."""
    down = Search(fails=_http_error(503))
    degraded = Search(heavy={"fails": _http_error(503)})
    for market in (down, degraded, Search()):
        text = wiring(market=market, deposit_chains=CHAINS).search_line()
        for claim in ("/launch", "/prepare", "hold", "recusad", "puzzle", "sela"):
            assert claim not in text, claim


def test_the_surface_is_named_and_a_wiring_without_a_search_says_so():
    assert wiring(market=Search(), market_surface="rarible").search_line().startswith(
        "pesquisa Rarible, sem filtro: leve respondeu em ")
    assert wiring().search_line() == "pesquisa: não ligada"


def _guard_after(*outcomes, states=("boa", "em-baixo", "degradada")) -> MarketNameUniqueness:
    """A guarda verdadeira, depois de umas perguntas: cada `outcome` é uma
    excepção (a pergunta falha) ou None (responde, e o alvo lá está). A
    pesquisa é encontrada, depois de cada falha, no estado seguinte de
    `states` (em roda)."""
    class Scripted:
        def __init__(self):
            self.steps = list(outcomes)
            self.state = "boa"

        def named_items(self, text):
            if text == LIGHT:
                if self.state == "em-baixo":
                    raise _http_error(503)
                return []
            if text == HEAVY:
                if self.state == "degradada":
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
        search.state = states[failures % len(states)]
        g("Salt Harbor", "ethereum", "0xaaa", 7)
        if outcome is not None:
            failures += 1
            assert g.last_transport
    return g


def test_the_counters_since_the_start_cost_nothing_and_add_up():
    g = _guard_after(None, _http_error(503), None, _http_error(503), _http_error(503))
    line = wiring(market=Search(), uniqueness=g).search_line().split("\n")[-1]
    assert line == ("  unicidade desde o arranque: 5 pergunta(s) · 3 pedido(s) falhado(s) "
                    "(503 3) · estado nas falhas: boa 1, degradada 1, em baixo 1")


def test_only_the_states_that_were_found_are_named():
    g = _guard_after(_http_error(503), _http_error(503), states=("degradada",))
    line = wiring(market=Search(), uniqueness=g).search_line().split("\n")[-1]
    assert line.endswith("(503 2) · estado nas falhas: degradada 2")


def test_questions_that_all_answered_read_as_no_failed_request():
    g = _guard_after(None, None, None)
    line = wiring(market=Search(), uniqueness=g).search_line().split("\n")[-1]
    assert line == "  unicidade desde o arranque: 3 pergunta(s) · 0 pedido(s) falhado(s)"


def test_looking_does_not_move_what_the_guard_has_counted():
    """O /status pergunta à pesquisa, não à guarda: os contadores da guarda
    — e o que ela guarda da ÚLTIMA pergunta de uma colheita a correr —
    ficam como estavam."""
    g = _guard_after(_http_error(503))
    before = (dict(g.stats), dict(g.failed_requests), dict(g.search_states),
              g.last_transport, g.last_state)
    market = Search(fails=_http_error(503))
    w = wiring(market=market, uniqueness=g)
    w.search_line()
    w.search_line()
    assert (dict(g.stats), dict(g.failed_requests), dict(g.search_states),
            g.last_transport, g.last_state) == before
    assert len(market.asked) == 4                            # two per look, no more


def test_the_line_shows_words_and_numbers_and_nothing_it_was_answered():
    """Nem os resultados nem os textos dos controlos: o leve é o nome de um
    alvo — já revelado, mas um nome — e a linha só diz "leve" e "pesada"."""
    rows = [("ETHEREUM:0xbbb:1", "Quiet Lantern Above")]
    ok = wiring(market=Search(rows=rows), deposit_chains=CHAINS).search_line()
    down = wiring(market=Search(fails=_http_error(503)),
                  deposit_chains=CHAINS).search_line()
    for text in (ok, down):
        for leak in ("Quiet", "Lantern", "0xbbb", SECRET, "api.example", LIGHT, HEAVY,
                     *LIGHT.split(), *HEAVY.split()):
            assert leak.lower() not in text.lower(), leak


# --------------------------------------------------------------------------- #
# 3. Produção                                                                   #
# --------------------------------------------------------------------------- #


def test_production_wires_the_search_and_the_guard_with_both_controls():
    w = _built(opensea_api_key="k")
    assert isinstance(w.market, OpenSeaSearch) and w.market._size == 50   # noqa: SLF001
    assert isinstance(w.uniqueness, MarketNameUniqueness)
    assert w.uniqueness._search is w.market                              # noqa: SLF001
    assert (w.uniqueness._light_query, w.uniqueness._heavy_query) == (LIGHT, HEAVY)  # noqa: SLF001
    assert w.finder._name_is_unique is w.uniqueness                      # noqa: SLF001
    # `_built` answers every GET with "{}": no results list → an unreadable body
    lines = w.search_line().split("\n")
    assert lines[0].startswith("pesquisa OpenSea, sem filtro: leve ⚠️ corpo-ilegível em ")
    assert " · pesada ⚠️ corpo-ilegível em " in lines[0]
    assert lines[1].startswith("  por cadeia: ")
    assert [part.split(" ")[0] for part in lines[1][len("  por cadeia: "):].split(" · ")] \
        == sorted(w.deposit_chains)
    assert lines[-1] == NO_QUESTION


def test_the_other_surface_gets_the_same_line_under_its_own_name():
    w = _built()                                             # no OpenSea key: Rarible
    assert w.market_surface == "rarible"
    assert w.uniqueness._search is w.market                              # noqa: SLF001
    assert w.search_line().startswith("pesquisa Rarible, sem filtro: leve ")


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

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

E o texto do /launch: quando a pesquisa não respondia, a recusa mandava
correr /prepare outra vez — o conselho de "a peça tornou-se pesquisável".
Segui-lo com a pesquisa de volta selava OUTRO alvo e deitava fora uma
preparação válida. Passa a dizer o que é: a pesquisa não respondeu, a
preparação mantém-se, repete-se o /launch.
"""
from __future__ import annotations

import inspect
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
    def __init__(self):
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Search:
    """Uma pesquisa de mentira: cada pedido demora `takes` segundos (no
    relógio de mentira) e responde, ou levanta `fails`."""

    def __init__(self, clock: Clock | None = None, *, takes: float = 0.0, fails=None,
                 rows=()):
        self.clock, self.takes, self.fails, self.rows = clock, takes, fails, list(rows)
        self.asked: list[str] = []

    def named_items(self, text):
        self.asked.append(text)
        if self.clock is not None:
            self.clock.now += self.takes
        if self.fails is not None:
            raise self.fails
        return list(self.rows)


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
    assert got == SearchProbe(ok=True, kind="", seconds=pytest.approx(0.64))
    assert got.render() == "respondeu em 0,6 s"
    assert search.asked == [SEARCH_CONTROL_QUERY]            # one request, the fixed text


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


def test_the_real_adapter_is_asked_the_control_with_the_page_it_was_built_with():
    seen: list[tuple[str, dict]] = []

    def get(url, headers):
        seen.append((url, headers))
        return '{"results": []}'

    assert probe_search(OpenSeaSearch(http_get=get, api_key="k", size=50)).ok
    assert probe_search(OpenSeaSearch(http_get=get, api_key="k", size=1)).ok
    (big, _h1), (small, _h2) = seen
    assert "query=finding%20memeland" in big and big.endswith("&limit=50")
    assert "query=finding%20memeland" in small and small.endswith("&limit=1")
    assert "chains=" not in big                              # unfiltered, as the harvest asks


# --------------------------------------------------------------------------- #
# 2. A linha do /status                                                         #
# --------------------------------------------------------------------------- #


def test_one_request_and_one_plain_line_when_the_search_answers():
    clock = Clock()
    market, small = Search(clock, takes=0.6), Search(clock, takes=0.4)
    line = wiring(market=market, market_small=small).search_line(clock=clock)
    assert line == ("pesquisa OpenSea: respondeu em 0,6 s\n"
                    "  unicidade desde o arranque: nenhuma pergunta")
    assert len(market.asked) == 1 and small.asked == []      # the page of 1 is not spent


def test_a_failed_control_is_followed_by_the_page_of_one_on_the_same_line():
    clock = Clock()
    market = Search(clock, takes=14.2, fails=_http_error(503))
    small = Search(clock, takes=0.4)
    first, counters = wiring(market=market, market_small=small).search_line(
        clock=clock).split("\n")
    assert first == ("pesquisa OpenSea: ⚠️ 503 em 14,2 s · página de 1: respondeu em 0,4 s"
                     " — um /launch seria recusado; uma hunt no puzzle ficava em hold")
    assert counters == "  unicidade desde o arranque: nenhuma pergunta"
    assert market.asked == [SEARCH_CONTROL_QUERY] and small.asked == [SEARCH_CONTROL_QUERY]


def test_both_failing_reads_as_both_failing():
    clock = Clock()
    market = Search(clock, takes=25.0, fails=TimeoutError("timed out"))
    small = Search(clock, takes=9.8, fails=_http_error(503))
    first = wiring(market=market, market_small=small).search_line(clock=clock).split("\n")[0]
    assert first == ("pesquisa OpenSea: ⚠️ sem resposta em 25,0 s (timeout) · "
                     "página de 1: 503 em 9,8 s"
                     " — um /launch seria recusado; uma hunt no puzzle ficava em hold")


def test_without_a_page_of_one_the_line_still_says_what_failed():
    clock = Clock()
    market = Search(clock, takes=1.0, fails=_http_error(503))
    first = wiring(market=market).search_line(clock=clock).split("\n")[0]
    assert first.startswith("pesquisa OpenSea: ⚠️ 503 em 1,0 s — um /launch seria recusado")
    assert "página de 1" not in first


def test_the_surface_is_named_and_a_wiring_without_a_search_says_so():
    assert wiring(market=Search(), market_surface="rarible").search_line().startswith(
        "pesquisa Rarible: respondeu em ")
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
    line = wiring(market=Search(), uniqueness=g).search_line().split("\n")[1]
    assert line == ("  unicidade desde o arranque: 4 pergunta(s) · 2 pedido(s) falhado(s) "
                    "(503 2) · controlo: respondeu 1, falhou 1")


def test_questions_that_all_answered_read_as_no_failed_request():
    g = _guard_after(None, None, None)
    line = wiring(market=Search(), uniqueness=g).search_line().split("\n")[1]
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
    first, counters = w.search_line().split("\n")
    assert first.startswith("pesquisa OpenSea: ⚠️ corpo-ilegível em ")
    assert " · página de 1: corpo-ilegível em " in first
    assert counters == "  unicidade desde o arranque: nenhuma pergunta"


def test_the_other_surface_gets_the_same_line_under_its_own_name():
    w = _built()                                             # no OpenSea key: Rarible
    assert w.market_surface == "rarible" and w.market_small._size == 1   # noqa: SLF001
    assert w.uniqueness._search is w.market                              # noqa: SLF001
    assert w.search_line().startswith("pesquisa Rarible: ")


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

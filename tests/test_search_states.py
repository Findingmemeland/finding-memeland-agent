"""A pesquisa tem três estados, e um controlo só não os distingue.

11/10. O controlo de 10/10 ("finding memeland") era uma pergunta PESADA: deu
503 dois dias seguidos — e nesse segundo dia um /prepare selou um alvo à
primeira e a despensa tinha ganho três na véspera. O /status dizia "⚠️ 503"
de uma pesquisa que respondia ao que importava, e na colheita tudo contava
como "o momento": 2.ª volta, 3.ª volta, memória — seis minutos por corrida
para salvar 1 candidato em 10.

Medido a 11/10, 11:55 UTC, com os nomes de três alvos já revelados, sem
filtro, página de 50: dois deram 503 aos 10,3 s e um respondeu em 1,7 s com
dez resultados; por cadeia responderam os três em 1–2 s. Sem filtro, ter ou
não resposta depende do NOME — nenhum controlo o adivinha. O que dois
controlos conseguem é dizer o estado:

    leve falha                    → em baixo  (não responde a ninguém)
    leve responde, pesada falha   → degradada (viva; há nomes sem resposta)
    as duas respondem             → boa       (um nome que falhe, falha sozinho)

As três decisões do Pedro (11/10): o texto leve é o nome revelado da #16;
com a pesquisa degradada, os nomes sem resposta vão para a memória SEM
voltas; e no início da corrida seguinte os dois controlos decidem por quem
perguntar — em baixo, ninguém; degradada, só os que ficaram à espera com a
pesquisa em baixo; boa, todos.

O que não mudou: "boa" três vezes seguidas com o nome a falhar continua a
ser índice-cego; em baixo continua a ter a 2.ª e a 3.ª volta; o /prepare
mantém o alvo em qualquer estado.
"""
from __future__ import annotations

import time

import pytest
from test_carried_candidates import HEAD, _manifold
from test_probe import addr
from test_unique_transport import (
    ADDRESS,
    A,
    Clock,
    _http_error,
    _larder_of,
    _preparer,
    _three,
    _timeout,
    finder,
    guard,
    probe,
    ref,
)

from finding_memeland.target.prepare import (
    CarryReport,
    Larder,
    PrepareRefused,
    SearchPasses,
    Source,
    Tally,
)
from finding_memeland.target.search_guard import (
    HEAVY_FAILURE_TTL_S,
    SEARCH_DEGRADED,
    SEARCH_DOWN,
    SEARCH_GOOD,
    SEARCH_HEAVY_QUERY,
    SEARCH_LIGHT_QUERY,
    SEARCH_STATES,
)

BOOM = _http_error(503)


def set_state(g, state: str, *, names=BOOM) -> None:
    """Pôr a pesquisa de mentira num estado. Em qualquer deles, um nome sem
    guião próprio faz `names` (por omissão, falha: 503)."""
    s = g._search                                        # noqa: SLF001
    s.then = names
    s.control_then = BOOM if state == SEARCH_DOWN else None
    s.heavy_then = BOOM if state == SEARCH_DEGRADED else None


def a_guard(state: str, script=None, *, clock=None, **kw):
    g = guard(script, **({} if clock is None else {"clock": clock}), **kw)
    set_state(g, state)
    return g


def counts(g) -> tuple[int, int, int]:
    """(perguntas por um nome, controlos leves, controlos pesados)."""
    s = g._search                                        # noqa: SLF001
    return len(s.tids), s.controls, s.heavies


def run(f, larder, tids, said=None, **kw):
    return f.deposit(larder, [ref(t) for t in tids], chain_ok=lambda c: True,
                     notify=(said.append if said is not None else None), **kw)


# --------------------------------------------------------------------------- #
# 1. Os dois controlos e os três estados                                        #
# --------------------------------------------------------------------------- #


def test_the_states_are_three_and_the_texts_are_two():
    assert SEARCH_STATES == (SEARCH_GOOD, SEARCH_DEGRADED, SEARCH_DOWN)
    assert (SEARCH_GOOD, SEARCH_DEGRADED, SEARCH_DOWN) == ("boa", "degradada", "em-baixo")
    assert SEARCH_HEAVY_QUERY == "finding memeland"
    # the Pedro's choice (11/10): the revealed name of hunt #16 — public
    # since the reveal, and the one of three that was answered that day
    assert SEARCH_LIGHT_QUERY == "Akari Haruto"


@pytest.mark.parametrize("state, light, heavy", [
    (SEARCH_GOOD, 1, 1),
    (SEARCH_DEGRADED, 1, 1),
    (SEARCH_DOWN, 1, 0),             # the light one failed: the heavy one adds nothing
])
def test_a_failed_question_finds_the_search_in_one_of_three_states(state, light, heavy):
    g = a_guard(state)
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert g.last_state == state and g.search_states == {state: 1}
    assert counts(g) == (1, light, heavy)
    assert g.failed_requests == {"503": 1}               # a control is never a failed request


@pytest.mark.parametrize("state", SEARCH_STATES)
def test_the_state_can_be_asked_without_a_question_and_counts_nothing(state):
    """O início de uma corrida pergunta o estado antes de perguntar por
    alguém: um ou dois pedidos, e nenhum contador se mexe — "nas falhas" é
    só depois de uma pergunta falhada."""
    g = a_guard(state)
    assert g.search_state() == state
    assert counts(g) == (0, 1, 0 if state == SEARCH_DOWN else 1)
    assert g.search_states == {} and g.last_state is None
    assert g.stats["transport"] == 0 and g.failed_requests == {}


def test_any_failure_of_a_control_is_a_failure_whatever_its_kind():
    for exc in (_timeout(), _http_error(429), ConnectionResetError(54, "x"), KeyError("k")):
        assert guard(control=[exc]).search_state() == SEARCH_DOWN
        assert guard(heavy=[exc]).search_state() == SEARCH_DEGRADED


# -- a memória do controlo pesado ------------------------------------------- #


def test_a_heavy_control_that_failed_is_not_asked_again_for_five_minutes():
    """Custa os seus dez segundos de cada vez, e uma pesquisa degradada
    ficou degradada dois dias. Numa colheita com 15 nomes sem resposta eram
    150 s só de controlos pesados."""
    assert HEAVY_FAILURE_TTL_S == 300.0
    clock = Clock()
    g = a_guard(SEARCH_DEGRADED, clock=clock)
    for tid in range(1, 6):
        clock.now += 20.0
        assert g("Salt Harbor", "ethereum", "0xaaa", tid) is None
        assert g.last_state == SEARCH_DEGRADED
    assert counts(g) == (5, 5, 1)                        # the light one every time
    assert g.search_states == {SEARCH_DEGRADED: 5}
    clock.now += HEAVY_FAILURE_TTL_S - 81.0              # 299 s after it failed
    assert g.search_state() == SEARCH_DEGRADED and counts(g) == (5, 6, 1)
    clock.now += 1.0                                     # five minutes: asked again
    assert g.search_state() == SEARCH_DEGRADED and counts(g) == (5, 7, 2)


def test_a_heavy_control_that_answered_is_asked_every_time():
    """"Boa" é o estado que pode custar um candidato (três seguidas = o
    nome): nunca se tira da memória."""
    clock = Clock()
    g = a_guard(SEARCH_GOOD, clock=clock)
    for tid in range(1, 4):
        g("Salt Harbor", "ethereum", "0xaaa", tid)
        assert g.last_state == SEARCH_GOOD
    assert counts(g) == (3, 3, 3)


def test_a_remembered_failure_never_hides_a_search_that_went_down():
    clock = Clock()
    g = a_guard(SEARCH_DEGRADED, clock=clock)
    g("Salt Harbor", "ethereum", "0xaaa", 1)
    set_state(g, SEARCH_DOWN)
    clock.now += 5.0
    assert g.search_state() == SEARCH_DOWN               # the light one is always asked


def test_the_memory_ends_when_the_heavy_control_answers_again():
    clock = Clock()
    g = a_guard(SEARCH_DEGRADED, clock=clock)
    g("Salt Harbor", "ethereum", "0xaaa", 1)
    set_state(g, SEARCH_GOOD)
    clock.now += 5.0
    assert g.search_state() == SEARCH_DEGRADED           # still remembered
    clock.now += HEAVY_FAILURE_TTL_S
    assert g.search_state() == SEARCH_GOOD
    set_state(g, SEARCH_DEGRADED)
    assert g.search_state() == SEARCH_DEGRADED           # asked at once: nothing remembered
    assert counts(g)[2] == 3


# --------------------------------------------------------------------------- #
# 2. Na colheita: degradada → memória, sem voltas                               #
# --------------------------------------------------------------------------- #


def test_with_the_search_degraded_a_name_without_an_answer_goes_to_memory_at_once():
    g = a_guard(SEARCH_DEGRADED)
    f, _calls, clock = finder(g, gap=60.0, third=300.0)
    larder, said = Larder(), []
    rep = run(f, larder, [1, 2], said)
    assert larder.size() == 0 and f.carried() == 2
    # no second pass, no third, and not one second of waiting
    assert (rep.search.second_asked, rep.search.third_asked) == (0, 0)
    assert clock.slept == []
    assert counts(g) == (2, 2, 1)                        # one question each
    # ours, never the piece's — and under its own name
    assert rep.rejected.unique_kinds == {"pesquisa-degradada:503": 2}
    assert rep.render().endswith(
        "único 2 (pesquisa-degradada:503 2) · pesquisa do único: 2 pedido(s) "
        "falhado(s) (503 2) — rápidos 2, lentos 0 · estado da pesquisa nas falhas: "
        "degradada 2 · ficam em memória para a próxima corrida: 2")
    (note,) = [m for m in said if "a pesquisa está degradada" in m]
    assert note == ("deposit: 2 candidato(s) sem resposta da pesquisa do nome — a "
                    "pesquisa está degradada (responde a umas perguntas e não a "
                    "estas): não volto a perguntar nesta corrida")
    assert not [m for m in said if "volto a perguntar agora" in m or "3.ª" in m]
    assert not ADDRESS.search(" ".join(said)) and "Name" not in " ".join(said)


def test_the_names_that_do_get_an_answer_are_stored_as_always():
    g = a_guard(SEARCH_DEGRADED, {2: [BOOM]})
    set_state(g, SEARCH_DEGRADED, names="ok")
    f, _calls, clock = finder(g, gap=60.0, third=300.0)
    larder = Larder()
    rep = run(f, larder, [1, 2, 3])
    assert sorted(c.token_id for c in larder.candidates) == [1, 3]
    assert rep.rejected.unique_kinds == {"pesquisa-degradada:503": 1}
    assert f.carried() == 1 and clock.slept == []


def test_a_search_that_is_down_still_gets_both_passes():
    g = a_guard(SEARCH_DOWN)
    f, _calls, clock = finder(g, gap=60.0, third=300.0)
    said: list[str] = []
    rep = run(f, Larder(), [1], said)
    assert (rep.search.second_asked, rep.search.third_asked) == (1, 1)
    assert clock.slept == [60.0, 300.0]
    assert rep.rejected.unique_kinds == {"rede-NOSSO:503": 1} and f.carried() == 1
    assert rep.search.states == {SEARCH_DOWN: 3}
    assert "estado da pesquisa nas falhas: em baixo 3" in rep.render()
    assert [m for m in said if "volto a perguntar agora" in m]
    assert not [m for m in said if "a pesquisa está degradada" in m]


def test_a_search_that_degrades_between_the_passes_ends_them_there():
    """Em baixo na 1.ª falha, degradada na 2.ª volta: a 3.ª já não se faz."""
    g = guard(then=BOOM, control=[BOOM], heavy=[BOOM])
    f, _calls, clock = finder(g, gap=60.0, third=300.0)
    rep = run(f, Larder(), [1])
    assert (rep.search.second_asked, rep.search.third_asked) == (1, 0)
    assert clock.slept == [60.0]
    assert rep.rejected.unique_kinds == {"pesquisa-degradada:503": 1}
    assert rep.search.states == {SEARCH_DOWN: 1, SEARCH_DEGRADED: 1}
    assert f.carried() == 1 and rep.search.carried == 1


def test_each_note_counts_its_own_candidates():
    """Numa corrida com um que ficou sem resposta com a pesquisa em baixo e
    outro com ela degradada, cada aviso diz o seu número — e só o primeiro
    é perguntado outra vez."""
    g = guard(then=BOOM, control=[BOOM], heavy=[BOOM])   # down for 1, degraded for 2
    f, _calls, _clock = finder(g)
    said: list[str] = []
    rep = run(f, Larder(), [1, 2], said)
    (degraded,) = [m for m in said if "a pesquisa está degradada" in m]
    (again,) = [m for m in said if "volto a perguntar agora" in m]
    assert degraded.startswith("deposit: 1 candidato(s) sem resposta")
    assert again.startswith("deposit: 1 candidato(s) sem resposta")
    assert (rep.search.second_asked, rep.search.third_asked) == (1, 0)
    assert g._search.tids == [1, 2, 1] and f.carried() == 2   # noqa: SLF001


def test_a_state_nobody_knows_is_no_state():
    """Uma guarda que diga outra coisa qualquer não tem estado: o candidato
    segue o caminho de sempre (voltas, e fica nosso)."""
    class Odd:
        last_state = "talvez"

        def __init__(self):
            self.stats = {"transport": 0}

        def __call__(self, *a):
            self.stats["transport"] += 1
            return None

    f, _calls, _clock = finder(Odd())
    src = Source("deposit", "ethereum", A)
    tally = Tally()
    read, base = f.named_token(src, 5, tally)
    first = f.verify(src, 5, read, base, tally, defer=True)
    assert first.states == (None,) and not first.degraded()
    rep = run(f, Larder(), [1])
    assert rep.rejected.unique_kinds == {"rede-NOSSO": 1}
    assert (rep.search.second_asked, rep.search.third_asked) == (1, 1)


def test_good_three_times_running_is_still_the_name_and_is_not_kept():
    g = a_guard(SEARCH_GOOD)
    f, _calls, _clock = finder(g)
    rep = run(f, Larder(), [1])
    assert rep.rejected.unique_kinds == {"índice-cego:pesquisa-503": 1}
    assert f.carried() == 0 and rep.search.states == {SEARCH_GOOD: 3}


def test_good_twice_and_degraded_on_the_third_is_ours_not_the_name():
    g = guard(then=BOOM, heavy=[None, None, BOOM])
    f, _calls, _clock = finder(g)
    rep = run(f, Larder(), [1])
    assert rep.rejected.unique_kinds == {"pesquisa-degradada:503": 1}
    assert f.carried() == 1 and "índice-cego" not in rep.render()


def test_the_report_names_only_the_states_it_found_in_a_fixed_order():
    passes = SearchPasses(states={SEARCH_DOWN: 2, SEARCH_GOOD: 1, SEARCH_DEGRADED: 4})
    assert passes.render() == "estado da pesquisa nas falhas: boa 1, degradada 4, em baixo 2"
    assert SearchPasses(states={SEARCH_DEGRADED: 1}).render() == (
        "estado da pesquisa nas falhas: degradada 1")
    assert SearchPasses().render() == ""


# -- a sonda, o /prepare e o /fill ------------------------------------------ #


def test_the_probe_does_not_ask_again_either_and_keeps_nobody():
    chain, order = _three()
    g = a_guard(SEARCH_DEGRADED)
    p, clock = probe(chain, order, g, gap=60.0)
    said: list[str] = []
    rep = p.run(3, notify=said.append)
    assert rep.with_arweave.passed == 0
    assert rep.with_arweave.causes == {("único", "pesquisa-degradada:503"): 3}
    assert (rep.search.second_asked, rep.search.third_asked) == (0, 0)
    assert clock.slept == [] and p._finder.carried() == 0     # noqa: SLF001
    assert sum(rep.today.causes.values()) + rep.today.passed == rep.tested == 3
    (note,) = [m for m in said if "a pesquisa está degradada" in m]
    assert note.startswith("probe manifold: 3 peça(s) sem resposta da pesquisa do nome")
    assert "ficam em memória" not in rep.render()


@pytest.mark.parametrize("state", SEARCH_STATES)
def test_prepare_keeps_every_target_whatever_the_state(state):
    """No /prepare não há voltas nem memória: um nome sem resposta mantém o
    alvo na despensa — em baixo, degradada ou boa."""
    world, g, f, larder = _larder_of(8, then=BOOM)
    set_state(g, state)
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, f).prepare(larder)
    assert larder.size() == 8 and "despensa INTACTA" in str(e.value)
    assert f.carried() == 0


def test_the_fill_counts_a_lost_draw_as_before_and_keeps_nobody():
    g = a_guard(SEARCH_DEGRADED)
    f, _calls, clock = finder(g, gap=60.0, third=300.0)
    tally = f.fill(Larder(), want=1, max_draws=3)
    assert tally.unique_kinds == {"rede-NOSSO:503": 3}
    assert clock.slept == [] and f.carried() == 0


# --------------------------------------------------------------------------- #
# 3. O início da corrida seguinte: os controlos decidem por quem perguntar      #
# --------------------------------------------------------------------------- #


def memory():
    """Três à espera: o 1 ficou com a pesquisa EM BAIXO; o 2 e o 3 com ela
    DEGRADADA. Devolve tudo com a pesquisa degradada e os contadores a zero."""
    clock = Clock()
    g = a_guard(SEARCH_DOWN, clock=clock)
    f, _calls, _clock = finder(g, clock=clock)
    larder = Larder()
    run(f, larder, [1])
    set_state(g, SEARCH_DEGRADED)
    run(f, larder, [2, 3])
    assert f.carried() == 3 and larder.size() == 0
    s = g._search                                        # noqa: SLF001
    s.tids.clear()
    s.controls = s.heavies = 0
    return g, f, larder, clock


def test_down_nobody_is_asked():
    g, f, larder, _clock = memory()
    set_state(g, SEARCH_DOWN)
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=3, unasked_down=3)
    assert counts(g) == (0, 1, 0) and f.carried() == 3
    assert rep.render() == (HEAD + "3 candidato(s) em memória — 3 nem perguntado(s): "
                            "a pesquisa não responde · ficam em memória: 3")


def test_degraded_only_those_set_aside_with_the_search_down_are_asked():
    """Os que ficaram com a pesquisa degradada têm nomes a que ela, assim,
    não responde: perguntar outra vez era ouvir o mesmo silêncio. O que
    ficou com ela em baixo pode ter um nome a que ela já responde."""
    g, f, larder, _clock = memory()
    g._search.script = {1: ["ok"]}                       # noqa: SLF001
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=3, asked=1, rescued=1, unasked_degraded=2)
    assert g._search.tids == [1]                         # noqa: SLF001 — one question
    assert [c.token_id for c in larder.candidates] == [1] and f.carried() == 2
    assert rep.render() == (
        HEAD + "3 candidato(s) em memória — 1 perguntado(s) outra vez: 1 guardado(s) · "
        "2 nem perguntado(s): a pesquisa continua degradada · ficam em memória: 2")


def test_degraded_and_the_one_asked_fails_too_it_waits_with_the_others():
    g, f, larder, _clock = memory()
    rep = f.retry_carried(larder)                        # the name of 1 is heavy too
    assert rep == CarryReport(total=3, asked=1, waiting=1, unasked_degraded=2)
    assert f.carried() == 3
    # …and now it is one of them: the next degraded run asks nobody
    again = f.retry_carried(larder)
    assert again == CarryReport(total=3, unasked_degraded=3)
    assert again.render() == (
        HEAD + "3 candidato(s) em memória — 3 nem perguntado(s): a pesquisa continua "
        "degradada · ficam em memória: 3")


def test_good_again_everybody_is_asked_in_the_order_they_were_set_aside():
    g, f, larder, clock = memory()
    set_state(g, SEARCH_GOOD, names="ok")
    clock.now += HEAVY_FAILURE_TTL_S                     # the failed heavy control is forgotten
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=3, asked=3, rescued=3)
    assert g._search.tids == [1, 2, 3] and f.carried() == 0   # noqa: SLF001
    assert rep.render() == (HEAD + "3 candidato(s) em memória — "
                            "3 perguntado(s) outra vez: 3 guardado(s)")


def test_the_state_is_read_once_before_anybody_is_asked():
    g, f, larder, clock = memory()
    set_state(g, SEARCH_GOOD, names="ok")
    clock.now += HEAVY_FAILURE_TTL_S
    f.retry_carried(larder)
    assert counts(g) == (3, 1, 1)                        # two controls for three candidates


def test_a_search_that_degrades_while_asking_stops_the_degraded_ones_behind():
    """Boa no início; o primeiro nome falha e a pesquisa é encontrada
    degradada: os que tinham ficado à espera por isso já não se perguntam."""
    g, f, larder, clock = memory()
    clock.now += HEAVY_FAILURE_TTL_S
    g._search.heavy = [None]                             # noqa: SLF001 — good, once
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=3, asked=1, waiting=1, unasked_degraded=2)
    assert g._search.tids == [1]                         # noqa: SLF001


def test_a_search_that_goes_down_while_asking_stops_everybody_behind():
    g, f, larder, clock = memory()
    set_state(g, SEARCH_GOOD)
    clock.now += HEAVY_FAILURE_TTL_S
    g._search.control = [None, BOOM]                     # noqa: SLF001 — good, then down
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=3, asked=1, waiting=1, unasked_down=2)
    assert f.carried() == 3


def test_a_name_set_aside_as_degraded_can_still_turn_out_to_be_the_name():
    """Com a pesquisa BOA e o nome a falhar três corridas seguidas, é o nome
    — tenha ele ficado à espera pelo motivo que for."""
    clock = Clock()
    g = a_guard(SEARCH_DEGRADED, clock=clock)
    f, _calls, _clock = finder(g, clock=clock)
    larder = Larder()
    run(f, larder, [1])
    set_state(g, SEARCH_GOOD)
    clock.now += HEAVY_FAILURE_TTL_S
    reports = [f.retry_carried(larder) for _ in range(3)]
    assert reports[0] == reports[1] == CarryReport(total=1, asked=1, waiting=1)
    assert reports[2] == CarryReport(total=1, asked=1, blind=1) and f.carried() == 0


def test_what_is_in_the_larder_or_over_its_share_is_settled_in_any_state():
    """Isso não precisa da pesquisa: resolve-se mesmo com ela em baixo."""
    g, f, larder, _clock = memory()
    larder.used.append(ref(1))
    set_state(g, SEARCH_DOWN)
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=3, duplicate=1, unasked_down=2)
    assert f.carried() == 2
    assert rep.render() == (HEAD + "3 candidato(s) em memória — 1 já na despensa · "
                            "2 nem perguntado(s): a pesquisa não responde · "
                            "ficam em memória: 2")


def test_a_guard_whose_state_cannot_be_read_asks_everybody():
    g, f, larder, _clock = memory()
    set_state(g, SEARCH_GOOD, names="ok")
    g.search_state = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    assert f.retry_carried(larder) == CarryReport(total=3, asked=3, rescued=3)


# --------------------------------------------------------------------------- #
# 4. O comando: /harvest manifold com a pesquisa degradada                      #
# --------------------------------------------------------------------------- #


def test_harvest_manifold_with_a_degraded_search_does_not_wait_six_minutes(monkeypatch):
    tw, store, g, _said = _manifold(monkeypatch)
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    tw.finder._sleep = slept.append                      # noqa: SLF001
    set_state(g, SEARCH_DEGRADED, names="ok")
    g._search.script = {addr(2): [BOOM], addr(3): [BOOM]}    # noqa: SLF001
    out = tw.harvest_source("manifold", 60)
    assert store.larder.size() == 2 and tw.finder.carried() == 2
    assert slept == []                                   # neither the minute nor the five
    assert "único 2 (pesquisa-degradada:503 2)" in out
    assert "estado da pesquisa nas falhas: degradada 2" in out
    assert "2.ª volta" not in out and "3.ª volta" not in out
    assert "ficam em memória para a próxima corrida: 2" in out
    assert out.rstrip().endswith("despensa 0 → 2 (+2) · à espera em memória: 2")
    assert not ADDRESS.search(out) and "Some Two Words" not in out


def test_the_next_harvest_leaves_them_alone_while_it_is_still_degraded(monkeypatch):
    tw, store, g, _said = _manifold(monkeypatch)
    set_state(g, SEARCH_DEGRADED, names="ok")
    g._search.script = {addr(2): [BOOM] * 2}             # noqa: SLF001
    tw.harvest_source("manifold", 60)
    assert tw.finder.carried() == 1
    before = len(g._search.contracts)                    # noqa: SLF001
    second = tw.harvest_source("manifold", 60)
    assert second.split("\n")[1] == (
        HEAD + "1 candidato(s) em memória — 1 nem perguntado(s): a pesquisa continua "
        "degradada · ficam em memória: 1")
    # the harvest itself found the piece again and asked once, as for any piece
    assert g._search.contracts[before:].count(addr(2)) == 1   # noqa: SLF001
    assert tw.finder.carried() == 1 and store.larder.size() == 3

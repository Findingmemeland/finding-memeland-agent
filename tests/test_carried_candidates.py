"""Os candidatos a quem a pesquisa NÃO RESPONDEU ficam para a corrida seguinte.

10/10. Numa colheita de 150 contratos a pesquisa do OpenSea falhou 15 pedidos
em 15, durante mais de seis minutos, com o controlo a falhar também: cinco
candidatos que tinham passado tudo o resto perderam-se para o MOMENTO, e a
despensa ganhou um alvo. Medido depois, do Railway e da máquina do Pedro: é o
serviço deles (503 aos 10 s, "timed out waiting for an internal service"), e
não há página mais pequena que o resolva sem mudar o que a unicidade decide.

Decisão do Pedro: "os candidatos sem resposta ficam em memória e voltam a ser
perguntados no início da corrida seguinte do mesmo processo; nunca escritos
nem impressos."

O que isto fixa:

  · quem a 3.ª volta deixou sem resposta — o momento, não o nome — fica na
    memória do finder; conta nesta corrida como "rede-NOSSO" e como "ficam em
    memória";
  · a corrida seguinte começa por eles: SÓ a pesquisa, uma pergunta a cada
    um; único → despensa; veredicto → fora;
  · antes de perguntar por alguém, vê-se o ESTADO da pesquisa (11/10, dois
    controlos — ver test_search_states.py): em baixo, ninguém se pergunta
    e ninguém se perde; degradada, só os que ficaram à espera com ela em
    baixo; boa, todos;
  · sem resposta três vezes seguidas com a pesquisa BOA: é o nome
    (índice-cego), como nas voltas de uma corrida;
  · a mesma peça nunca duas vezes; cinquenta no máximo, os mais antigos
    saem primeiro — e o relatório diz quantos saíram;
  · a regra dos dois por contrato NA DESPENSA (colheita de uma fonte) segue
    com o candidato;
  · a sonda e o /fill não guardam ninguém;
  · nada disto se escreve nem se imprime: contagens.
"""
from __future__ import annotations

import time

import pytest
from test_creator_harvest import _BaseWorld, _command
from test_harvest_command import _Harv, _Store
from test_probe import addr
from test_search_status import Search as StatusSearch
from test_search_status import wiring
from test_unique_transport import (
    ADDRESS,
    _http_error,
    _three,
    finder,
    guard,
    probe,
    ref,
)

from finding_memeland.target.harvest import MAX_TARGETS_PER_CONTRACT
from finding_memeland.target.prepare import (
    CARRY_MAX,
    NAME_BOUND_AFTER,
    CarryReport,
    Larder,
    SearchPasses,
)
from finding_memeland.target.wiring import TargetWiring

HEAD = "da corrida anterior: "


def down(g) -> None:
    """A pesquisa não responde a ninguém — nem ao controlo leve (em baixo)."""
    g._search.then = _http_error(503)                  # noqa: SLF001
    g._search.control_then = _http_error(503)          # noqa: SLF001


def up(g) -> None:
    g._search.then = "ok"                              # noqa: SLF001
    g._search.control_then = None                      # noqa: SLF001


def run(f, larder, tids, **kw):
    """Uma corrida: o depósito do que a colheita trouxe."""
    return f.deposit(larder, [ref(t) for t in tids], chain_ok=lambda c: True, **kw)


def world(*tids, **kw):
    """Uma corrida com a pesquisa em baixo: quem lá chegou fica à espera."""
    g = guard()
    down(g)
    f, calls, clock = finder(g)
    larder = Larder()
    rep = run(f, larder, tids, **kw)
    return g, f, larder, rep, calls


def asked(g) -> list[int]:
    return list(g._search.tids)                        # noqa: SLF001


# --------------------------------------------------------------------------- #
# 1. Quem fica à espera                                                         #
# --------------------------------------------------------------------------- #


def test_what_no_pass_got_an_answer_for_waits_in_memory():
    _g, f, larder, rep, _calls = world(1, 2)
    assert larder.size() == 0 and f.carried() == 2
    # in THIS run it did not get in, and the report says so as it always did
    assert rep.rejected.unique_kinds == {"rede-NOSSO:503": 2}
    assert (rep.search.third_asked, rep.search.third_rescued) == (2, 0)
    assert rep.search.carried == 2 and rep.search.evicted == 0
    assert rep.render().endswith("3.ª volta: 2 repetido(s), 0 salvo(s) · "
                                 "ficam em memória para a próxima corrida: 2")


def test_a_name_the_marketplace_cannot_search_is_not_kept():
    """O controlo respondeu depois das três falhas: é o nome. Perguntar outra
    vez na corrida seguinte era gastar pedidos para ouvir o mesmo."""
    g = guard(then=_http_error(503))                   # the control answers
    f, _calls, _clock = finder(g)
    rep = run(f, Larder(), [1])
    assert rep.rejected.unique_kinds == {"índice-cego:pesquisa-503": 1}
    assert f.carried() == 0 and rep.search.carried == 0
    assert "ficam em memória" not in rep.render()


def test_a_verdict_and_a_rescue_leave_nobody_waiting():
    g = guard({1: ["namesake"], 2: [_http_error(503), "ok"]},
              control=[_http_error(503)])
    f, _calls, _clock = finder(g)
    larder = Larder()
    rep = run(f, larder, [1, 2, 3])
    assert larder.size() == 2 and f.carried() == 0
    assert rep.search.second_rescued == 1 and rep.search.carried == 0


def test_a_run_with_nobody_waiting_says_nothing_about_memory():
    assert "memória" not in SearchPasses(second_asked=1, second_rescued=1).render()
    assert SearchPasses(third_asked=2, carried=2).render() == (
        "3.ª volta: 2 repetido(s), 0 salvo(s) · ficam em memória para a próxima corrida: 2")


# --------------------------------------------------------------------------- #
# 2. A corrida seguinte começa por eles                                         #
# --------------------------------------------------------------------------- #


def test_the_next_run_asks_them_again_and_what_is_unique_now_enters():
    g, f, larder, _rep, calls = world(1, 2)
    before = (len(asked(g)), len(calls["image"]), len(calls["eoa"]))
    up(g)
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=2, asked=2, rescued=2)
    assert larder.size() == 2 and f.carried() == 0
    assert asked(g)[before[0]:] == [1, 2]                    # one question each, in order
    # the search ONLY: the image and the owner were proven when it was set aside
    assert (len(calls["image"]), len(calls["eoa"])) == before[1:]
    assert rep.render() == (HEAD + "2 candidato(s) em memória — "
                            "2 perguntado(s) outra vez: 2 guardado(s)")


def test_a_rescued_candidate_is_the_one_a_healthy_run_would_have_stored():
    g, f, larder, _rep, _calls = world(7)
    up(g)
    f.retry_carried(larder)
    healthy_f, _c, _k = finder(guard())
    healthy = Larder()
    run(healthy_f, healthy, [7])
    (late,), (direct,) = larder.candidates, healthy.candidates
    assert late.id() == direct.id() == ref(7)
    assert (late.name, late.name_onchain, late.image, late.token_uri, late.metadata) == (
        direct.name, direct.name_onchain, direct.image, direct.token_uri, direct.metadata)


def test_a_verdict_on_the_way_back_is_final():
    g, f, larder, _rep, _calls = world(1, 2, 3)
    up(g)
    g._search.script = {1: ["namesake"], 2: ["blind"]}       # noqa: SLF001
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=3, asked=3, rescued=1, verdict=2)
    assert larder.size() == 1 and f.carried() == 0
    assert rep.render() == (HEAD + "3 candidato(s) em memória — 3 perguntado(s) "
                            "outra vez: 1 guardado(s), 2 com veredicto")
    assert f.retry_carried(larder) == CarryReport()          # and they are not asked twice


def test_with_nobody_waiting_nothing_is_asked_and_nothing_is_said():
    g = guard()
    f, _calls, _clock = finder(g)
    rep = f.retry_carried(Larder())
    assert rep == CarryReport() and rep.render() == "" and asked(g) == []


# --------------------------------------------------------------------------- #
# 3. A pesquisa continua em baixo                                               #
# --------------------------------------------------------------------------- #


def test_a_search_that_is_still_down_costs_one_control_and_nobody_is_lost():
    """Cada um custava uma pergunta falhada e um controlo falhado (~20 s ao
    que se mediu a 10/10). Um controlo leve chega para saber: ninguém é
    perguntado, e todos esperam."""
    g, f, larder, _rep, _calls = world(1, 2, 3)
    s = g._search                                             # noqa: SLF001
    questions, controls, heavies = len(asked(g)), s.controls, s.heavies
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=3, unasked_down=3)
    assert len(asked(g)) == questions                         # no question at all…
    assert (s.controls - controls, s.heavies - heavies) == (1, 0)   # …one light control
    assert f.carried() == 3 and larder.size() == 0
    assert rep.render() == (
        HEAD + "3 candidato(s) em memória — 3 nem perguntado(s): a pesquisa não "
        "responde · ficam em memória: 3")
    # …and when it answers they all come back, in the order they were set aside
    up(g)
    assert f.retry_carried(larder) == CarryReport(total=3, asked=3, rescued=3)
    assert asked(g)[-3:] == [1, 2, 3] and larder.size() == 3


def test_it_stops_where_the_search_stops_not_before():
    """A pesquisa estava boa no início e caiu a meio: quem vinha atrás já
    não é perguntado."""
    g, f, larder, _rep, _calls = world(1, 2, 3)
    up(g)
    g._search.script = {2: [_http_error(503)]}               # noqa: SLF001
    g._search.control = [None, _http_error(503)]             # noqa: SLF001 — good, then down
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=3, asked=2, rescued=1, waiting=1, unasked_down=1)
    assert larder.size() == 1 and f.carried() == 2
    assert rep.render() == (
        HEAD + "3 candidato(s) em memória — 2 perguntado(s) outra vez: 1 guardado(s), "
        "1 sem resposta outra vez · 1 nem perguntado(s): a pesquisa não responde · "
        "ficam em memória: 2")


def test_no_answer_with_the_search_good_goes_on_to_the_next_one():
    """Os dois controlos responderam: a pesquisa está boa — o problema pode
    ser deste nome, e os outros não têm de esperar por ele."""
    g, f, larder, _rep, _calls = world(1, 2, 3)
    up(g)
    g._search.script = {1: [_http_error(503)]}               # noqa: SLF001
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=3, asked=3, rescued=2, waiting=1)
    assert larder.size() == 2 and f.carried() == 1
    assert rep.render() == (
        HEAD + "3 candidato(s) em memória — 3 perguntado(s) outra vez: 2 guardado(s), "
        "1 sem resposta outra vez · ficam em memória: 1")


def test_a_guard_without_a_control_never_stops_the_others():
    """Outra superfície, ou uma guarda de mentira: sem controlos não há
    estado nenhum — pergunta-se a todos."""
    class Mute:
        def __init__(self):
            self.stats = {"transport": 0}
            self.answers = False

        def __call__(self, *a):
            if self.answers:
                return True
            self.stats["transport"] += 1
            return None

    g = Mute()
    f, _calls, _clock = finder(g)
    larder = Larder()
    run(f, larder, [1, 2])
    assert f.carried() == 2
    assert f.retry_carried(larder) == CarryReport(total=2, asked=2, waiting=2)
    g.answers = True
    assert f.retry_carried(larder) == CarryReport(total=2, asked=2, rescued=2)


# --------------------------------------------------------------------------- #
# 4. O nome, não o momento                                                      #
# --------------------------------------------------------------------------- #


def test_three_failures_in_a_row_with_the_search_good_are_the_name():
    assert NAME_BOUND_AFTER == 3                             # the passes' own three
    g, f, larder, _rep, _calls = world(1)                    # the search was down: kept
    g._search.control_then = None                            # noqa: SLF001 — it is good now
    reports = [f.retry_carried(larder) for _ in range(3)]
    assert reports[0] == reports[1] == CarryReport(total=1, asked=1, waiting=1)
    assert reports[2] == CarryReport(total=1, asked=1, blind=1)
    assert f.carried() == 0 and larder.size() == 0
    assert reports[2].render() == (
        HEAD + "1 candidato(s) em memória — 1 perguntado(s) outra vez: 0 guardado(s), "
        "1 que a pesquisa não procura (índice-cego)")


def test_one_failure_with_the_search_down_and_the_count_starts_again():
    g, f, larder, _rep, _calls = world(1)
    s = g._search                                            # noqa: SLF001
    s.control_then = None
    f.retry_carried(larder)
    f.retry_carried(larder)                                  # two in a row…
    s.control = [None, _http_error(503)]                     # good, asked, and then down
    assert f.retry_carried(larder).waiting == 1              # …and the moment again
    assert f.retry_carried(larder).waiting == 1
    assert f.retry_carried(larder).waiting == 1
    assert f.retry_carried(larder) == CarryReport(total=1, asked=1, blind=1)


def test_a_run_with_the_search_down_does_not_count_against_the_name():
    """Uma corrida em que ninguém foi perguntado não é uma falha do nome:
    a conta das três seguidas fica onde estava."""
    g, f, larder, _rep, _calls = world(1)
    s = g._search                                            # noqa: SLF001
    s.control_then = None
    f.retry_carried(larder)
    f.retry_carried(larder)                                  # two in a row
    s.control = [_http_error(503)]
    assert f.retry_carried(larder) == CarryReport(total=1, unasked_down=1)
    assert f.retry_carried(larder) == CarryReport(total=1, asked=1, blind=1)   # the third


# --------------------------------------------------------------------------- #
# 5. A memória: sem repetidos, com tecto                                        #
# --------------------------------------------------------------------------- #


def test_the_same_piece_is_never_in_memory_twice():
    """Uma colheita pode voltar a encontrar a peça que a anterior deixou à
    espera (não está na despensa: nada a impede)."""
    g, f, larder, _rep, _calls = world(1, 2)
    rep = run(f, larder, [2, 3])
    assert f.carried() == 3 and rep.search.carried == 2 and rep.search.evicted == 0
    up(g)
    before = len(asked(g))
    assert f.retry_carried(larder) == CarryReport(total=3, asked=3, rescued=3)
    assert sorted(asked(g)[before:]) == [1, 2, 3] and larder.size() == 3


def test_memory_holds_fifty_and_the_oldest_leave_first():
    extra = 10
    g, f, larder, rep, _calls = world(*range(1, CARRY_MAX + extra + 1))
    assert CARRY_MAX == 50 and f.carried() == CARRY_MAX
    # the report does not say sixty are waiting when fifty are
    assert (rep.search.carried, rep.search.evicted) == (CARRY_MAX, extra)
    assert rep.search.render().endswith(
        "ficam em memória para a próxima corrida: 50 · "
        "saíram da memória, os mais antigos: 10 (cabem 50)")
    up(g)
    f.retry_carried(larder)
    assert sorted(c.token_id for c in larder.candidates) == list(
        range(extra + 1, CARRY_MAX + extra + 1))


def test_an_older_run_s_candidates_leave_before_this_run_s():
    g, f, larder, _rep, _calls = world(*range(1, 46))        # 45 waiting
    rep = run(f, larder, range(100, 110))                    # 10 more
    assert f.carried() == CARRY_MAX
    assert (rep.search.carried, rep.search.evicted) == (10, 5)
    up(g)
    f.retry_carried(larder)
    assert sorted(c.token_id for c in larder.candidates) == [
        *range(6, 46), *range(100, 110)]


def test_what_is_in_the_larder_by_now_is_not_asked():
    g, f, larder, _rep, _calls = world(1, 2)
    up(g)
    run(f, larder, [1])                                      # it got in another way
    before = len(asked(g))
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=2, asked=1, rescued=1, duplicate=1)
    assert asked(g)[before:] == [2] and larder.size() == 2
    assert rep.render() == (HEAD + "2 candidato(s) em memória — 1 perguntado(s) "
                            "outra vez: 1 guardado(s) · 1 já na despensa")


def test_a_target_already_played_is_not_asked_either():
    g, f, larder, _rep, _calls = world(1)
    larder.used.append(ref(1))
    up(g)
    before = len(asked(g))
    assert f.retry_carried(larder) == CarryReport(total=1, duplicate=1)
    assert len(asked(g)) == before and larder.size() == 0


# --------------------------------------------------------------------------- #
# 6. Os dois por contrato, na despensa                                          #
# --------------------------------------------------------------------------- #


def test_the_two_per_contract_rule_of_a_source_harvest_follows_the_candidate():
    """A colheita de uma fonte não lê uma terceira peça de um contrato que
    já tem dois alvos na despensa (10/10). Um candidato que ficou à espera
    não pode ser essa terceira peça só por ter chegado mais tarde."""
    g, f, larder, _rep, _calls = world(1, larder_cap=2)
    up(g)
    run(f, larder, [5, 6])                                   # the contract fills meanwhile
    before = len(asked(g))
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=1, full=1)
    assert len(asked(g)) == before                           # not even asked
    assert larder.size() == 2 and f.carried() == 0
    assert rep.render() == HEAD + "1 candidato(s) em memória — 1 de contrato já cheio"


def test_below_its_share_the_contract_takes_it():
    g, f, larder, _rep, _calls = world(1, larder_cap=2)
    up(g)
    run(f, larder, [5])
    assert f.retry_carried(larder) == CarryReport(total=1, asked=1, rescued=1)
    assert larder.size() == 2


def test_a_block_harvest_has_no_such_rule_and_none_is_invented():
    """Na colheita por blocos o limite é por CORRIDA, não na despensa."""
    g, f, larder, _rep, _calls = world(1)
    up(g)
    run(f, larder, [5, 6])
    assert f.retry_carried(larder) == CarryReport(total=1, asked=1, rescued=1)
    assert larder.size() == 3


# --------------------------------------------------------------------------- #
# 7. Ninguém se perde, nada se imprime                                          #
# --------------------------------------------------------------------------- #


def test_nobody_leaves_memory_unanswered_if_asking_blows_up():
    g, f, larder, _rep, _calls = world(1, 2, 3)
    up(g)
    real = larder.add

    def add(cand):
        if cand.token_id == 2:
            raise RuntimeError("boom")
        return real(cand)

    larder.add = add
    with pytest.raises(RuntimeError):
        f.retry_carried(larder)
    assert larder.size() == 1 and f.carried() == 2           # the one it blew on, and the next
    larder.add = real
    assert f.retry_carried(larder) == CarryReport(total=2, asked=2, rescued=2)
    assert sorted(c.token_id for c in larder.candidates) == [1, 2, 3]


def test_what_the_larder_does_not_take_is_not_called_saved():
    g, f, larder, _rep, _calls = world(1)
    up(g)
    larder.add = lambda _cand: False
    rep = f.retry_carried(larder)
    assert rep == CarryReport(total=1, asked=1, duplicate=1)
    assert larder.size() == 0 and f.carried() == 0


def test_a_waiting_candidate_cannot_be_printed():
    _g, f, larder, rep, _calls = world(1, 2)
    carried = list(f._carried)                               # noqa: SLF001
    assert [repr(p) for p in carried] == ["Pending(<candidate>)"] * 2
    assert f"{carried[0]}" == "Pending(<candidate>)" and "Name" not in f"{carried}"
    again = f.retry_carried(larder)
    text = rep.render() + again.render() + repr(again) + repr(rep.search)
    assert "Name" not in text and not ADDRESS.search(text)
    assert set(vars(again).values()) <= {0, 1, 2}            # counts, and nothing else


def test_the_probe_keeps_nobody():
    """A sonda mede: não guarda alvos, e também não guarda quem espera."""
    chain, order = _three()
    g = guard()
    down(g)
    p, _clock = probe(chain, order, g)
    rep = p.run(3)
    assert rep.search.third_asked == 3 and rep.search.carried == 0
    assert p._finder.carried() == 0                          # noqa: SLF001
    assert "ficam em memória" not in rep.render()


def test_a_fill_keeps_nobody():
    g = guard()
    down(g)
    f, _calls, _clock = finder(g)
    tally = f.fill(Larder(), want=1, max_draws=3)
    assert tally.unique_kinds == {"rede-NOSSO:503": 3} and f.carried() == 0


# --------------------------------------------------------------------------- #
# 8. O comando: /harvest manifold, duas corridas                                #
# --------------------------------------------------------------------------- #


def _manifold(monkeypatch, n=4):
    """O comando inteiro, com o finder como a produção o constrói — só o
    `sleep` é de mentira."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    contracts = {addr(i): {"last": 1} for i in range(1, n + 1)}
    w = _BaseWorld()
    g = guard()
    w.name_is_unique = g
    tw, store, _draws, _world = _command(list(contracts), contracts, world=w)
    said: list[str] = []
    tw.notify = said.append
    return tw, store, g, said


def _only(g, contract, *steps):
    g._search.script = {contract: list(steps)}               # noqa: SLF001


def test_harvest_manifold_starts_by_those_the_last_run_left_waiting(monkeypatch):
    tw, store, g, said = _manifold(monkeypatch)
    _only(g, addr(2), *[_http_error(503)] * 3)
    g._search.control = [_http_error(503)]                   # noqa: SLF001
    first = tw.harvest_source("manifold", 60)
    assert store.larder.size() == 3 and tw.finder.carried() == 1
    assert "ficam em memória para a próxima corrida: 1" in first
    assert first.rstrip().endswith("despensa 0 → 3 (+3) · à espera em memória: 1")
    assert HEAD not in first and not [m for m in said if "corrida anterior" in m]

    saves, before = store.saves, len(g._search.contracts)    # noqa: SLF001
    second = tw.harvest_source("manifold", 60)
    assert store.larder.size() == 4 and tw.finder.carried() == 0
    # asked before anything was drawn, and it is the first line of the report
    assert g._search.contracts[before] == addr(2)            # noqa: SLF001
    assert second.split("\n")[:2] == [
        "harvest manifold:",
        HEAD + "1 candidato(s) em memória — 1 perguntado(s) outra vez: 1 guardado(s)"]
    assert second.rstrip().endswith("despensa 3 → 4 (+1)")
    assert store.saves >= saves + 2                          # saved at once, and at the end
    (note,) = [m for m in said if "corrida anterior" in m]
    assert note == ("harvest: 1 candidato(s) da corrida anterior sem resposta da "
                    "pesquisa — vejo como ela está e pergunto por quem fizer sentido, "
                    "antes de começar")
    assert not ADDRESS.search(first + second + note)
    assert "Some Two Words" not in first + second + note


def test_what_it_rescues_is_saved_even_if_the_harvest_then_refuses(monkeypatch):
    """Um sorteio cego recusa a corrida — e não pode levar consigo o que a
    pesquisa acabou de responder."""
    tw, store, g, _said = _manifold(monkeypatch)
    _only(g, addr(2), *[_http_error(503)] * 3)
    g._search.control = [_http_error(503)]                   # noqa: SLF001
    tw.harvest_source("manifold", 60)
    tw.creator_harvesters["manifold"]._sampler.head_error = TimeoutError("x")  # noqa: SLF001
    saves = store.saves
    out = tw.harvest_source("manifold", 60)
    assert store.larder.size() == 4 and store.saves == saves + 1
    carried, refusal = out.split("\n")
    assert carried.startswith(HEAD + "1 candidato(s) em memória")
    assert refusal.startswith("harvest manifold: NÃO MEDIDA — o RPC falhou (TimeoutError)")


def test_a_search_still_down_costs_the_next_run_one_control_and_says_so(monkeypatch):
    tw, store, g, _said = _manifold(monkeypatch)
    down(g)
    first = tw.harvest_source("manifold", 60)
    assert store.larder.size() == 0 and tw.finder.carried() == 4
    assert "ficam em memória para a próxima corrida: 4" in first
    saves = store.saves
    second = tw.harvest_source("manifold", 60)
    assert second.split("\n")[1] == (
        HEAD + "4 candidato(s) em memória — 4 nem perguntado(s): a pesquisa não "
        "responde · ficam em memória: 4")
    # the harvest found the same four again: still four waiting, not eight —
    # and the last line says the total, so two "4" are not read as eight
    assert tw.finder.carried() == 4 and store.larder.size() == 0
    assert "ficam em memória para a próxima corrida: 4" in second
    assert second.rstrip().endswith("despensa 0 → 0 (+0) · à espera em memória: 4")
    assert store.saves == saves + 1                          # the run's own save only


def test_the_command_passes_its_two_per_contract_rule_on(monkeypatch):
    tw, _store, g, _said = _manifold(monkeypatch)
    down(g)
    tw.harvest_source("manifold", 60)
    assert {p.cap for p in tw.finder._carried} == {MAX_TARGETS_PER_CONTRACT}  # noqa: SLF001


def test_nobody_waiting_and_the_command_reads_as_it_always_did(monkeypatch):
    tw, store, _g, said = _manifold(monkeypatch)
    out = tw.harvest_source("manifold", 60)
    assert out.startswith("harvest manifold:\nmanifold/base: 4 contrato(s) sorteado(s)")
    assert store.larder.size() == 4 and store.saves == 1
    assert "memória" not in out and not [m for m in said if "corrida anterior" in m]


# --------------------------------------------------------------------------- #
# 9. O /harvest por blocos: a mesma memória                                     #
# --------------------------------------------------------------------------- #


def _blocks(tids, *, chains=("ethereum",)):
    g = guard()
    f, _calls, _clock = finder(g)
    store = _Store()
    harv = _Harv([ref(t) for t in tids])
    said: list[str] = []
    tw = TargetWiring(
        ports=None, pipeline=None, epoch=None, snapshot_store=None, scan_blocks=0,
        writability_rates={}, uniqueness_rates={}, finder=f, larder_store=store,
        harvesters={"ethereum": harv}, deposit_chains=frozenset(chains),
        notify=said.append)
    return tw, store, g, harv, said


def test_a_block_harvest_starts_by_them_too():
    tw, store, g, harv, said = _blocks([1, 2])
    down(g)
    first = tw.harvest(200)
    assert store.larder.size() == 0 and tw.finder.carried() == 2
    assert first.rstrip().endswith("despensa 0 → 0 (+0) · à espera em memória: 2")
    up(g)
    harv.refs = [ref(9)]                                     # this run finds another piece
    before = len(asked(g))
    second = tw.harvest(200)
    assert asked(g)[before:] == [1, 2, 9]                    # the waiting ones first
    assert second.split("\n")[:2] == [
        "harvest:",
        HEAD + "2 candidato(s) em memória — 2 perguntado(s) outra vez: 2 guardado(s)"]
    assert second.rstrip().endswith("despensa 0 → 3 (+3)")
    assert store.larder.size() == 3 and tw.finder.carried() == 0
    assert len([m for m in said if "corrida anterior" in m]) == 1
    assert not ADDRESS.search(first + second) and "Name" not in first + second


def test_a_block_harvest_puts_no_larder_rule_on_who_waits():
    tw, _store, g, _harv, _said = _blocks([1, 2])
    down(g)
    tw.harvest(200)
    assert {p.cap for p in tw.finder._carried} == {0}        # noqa: SLF001


def test_they_are_asked_even_if_every_chain_of_the_run_is_skipped():
    """Uma cadeia sem provedor público é saltada — e isso não é razão para
    deixar à espera quem a pesquisa já pode responder."""
    tw, store, g, harv, _said = _blocks([1])
    down(g)
    tw.harvest(200)
    tw.deposit_chains = frozenset()
    up(g)
    out = tw.harvest(200)
    assert "ethereum: saltada" in out and harv.calls == 1
    assert store.larder.size() == 1 and tw.finder.carried() == 0
    assert out.rstrip().endswith("despensa 0 → 1 (+1)")


def test_a_chain_this_process_cannot_harvest_asks_nobody():
    """`/harvest tezos`: nada foi varrido — e ninguém foi perguntado."""
    tw, store, g, _harv, _said = _blocks([1])
    down(g)
    tw.harvest(200)
    up(g)
    before = len(asked(g))
    out = tw.harvest(200, only="tezos")
    assert "não configurada" in out and len(asked(g)) == before
    assert tw.finder.carried() == 1 and store.larder.size() == 0


# --------------------------------------------------------------------------- #
# 10. O /status diz quantos esperam                                             #
# --------------------------------------------------------------------------- #


def test_the_status_counts_who_is_waiting_and_nothing_more():
    _g, f, _larder, _rep, _calls = world(1, 2, 3)
    line = wiring(market=StatusSearch(), finder=f).search_line().split("\n")[-1]
    assert line == "  unicidade desde o arranque: nenhuma pergunta · à espera em memória: 3"
    assert "Name" not in line and not ADDRESS.search(line)


def test_the_status_says_nothing_about_memory_when_nobody_waits():
    f, _calls, _clock = finder(guard())
    text = wiring(market=StatusSearch(), finder=f).search_line()
    assert "memória" not in text

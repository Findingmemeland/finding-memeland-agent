"""Depois das hunts #19 e #20 (13/10) — as decisões do Pedro de 14/10.

  1. PISTA FALHADA: na #20 a pista #4 falhou as seis tentativas e os
     jogadores esperaram uma janela inteira (6–26 min) por nada. Uma ronda
     que as guardas recusaram volta a ser pedida logo a seguir — duas vezes
     por pista — e só depois espera a janela. Um post falhado no X e uma
     guarda nossa indisponível ficam como estavam.
  2. A PISTA E O TAUNT SEPARADOS: a partir da pista 3 a pista abria com o
     gozo ("3 clues deep and still lost — word two of the name is…") e o
     taunt repetia-o ou saía cortado. Causa lida no código: a instrução do
     escritor de alvos não tinha a regra que o motor de personas tem, e a
     limpeza da pista cortava no primeiro ponto final — com um travessão a
     juntar, deixava tudo. Ficam (a) a regra na instrução e (b) a limpeza
     até ao travessão. O taunt NÃO é tocado (decisão fechada: sai como o
     escritor o escreve).
  3. (Os claims noutras cadeias estão em test_foreign_chain_claims.py; aqui
     fica o gozo desses palpites, que nunca pode nomear a cadeia.)
  4. "ÚNICO ONCHAIN": o homónimo do Rarible não existe on-chain (medido); e
     conta-se, sem mudar nada, quantos "único" saem de uma página cheia.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from test_foreign_chain_claims import matcher as foreign_matcher
from test_unique_transport import Guard
from test_unique_transport import Search as ScriptedSearch

from finding_memeland.claims.taunts import TAUNT_POOL, TauntEngine
from finding_memeland.content.clue_engine import (
    ClueRoundRefused,
    _strip_leading_meta,
)
from finding_memeland.orchestrator.state_machine import Orchestrator
from finding_memeland.target.clues import parse_target_clue
from finding_memeland.target.dryrun import TargetWorld
from finding_memeland.target.prepare import SearchPasses

WINDOW = timedelta(minutes=20)


class Refusing:
    """O motor de pistas da simulação, com a pista `clue` recusada nas
    primeiras `rounds` rondas. Regista quando cada pista foi pedida."""

    def __init__(self, inner, clock, *, clue: int, rounds: int, attempts: int = 6):
        self.inner, self.clock = inner, clock
        self.clue, self.left, self.attempts = clue, rounds, attempts
        self.asked: list[tuple[int, object]] = []

    def next_clue(self, ctx, clue_index, prior_clues):
        self.asked.append((clue_index, self.clock.now()))
        if clue_index == self.clue and self.left > 0:
            self.left -= 1
            raise ClueRoundRefused(
                f"clue #{clue_index} failed guardrails after {self.attempts} attempts "
                "(1 razões — omitidas aqui porque nomeiam a resposta)",
                clue_index=clue_index, attempts=self.attempts, reasons=1)
        return self.inner.next_clue(ctx, clue_index, prior_clues)


def hunt_with(engine_factory, rounds: int = 40):
    w = TargetWorld()
    engine = engine_factory(w)
    w.ports.clue_engine = engine
    hunt = w.launch()
    w.orch._hunt_timeout_h = None
    w.orch._clue_due_fn = lambda now: now + WINDOW
    w.orch._max_rounds = rounds
    with pytest.raises(RuntimeError):
        w.orch._claim_loop(hunt)
    return w, engine


def times_of(engine, clue: int) -> list:
    return [t for i, t in engine.asked if i == clue]


# --------------------------------------------------------------------------- #
# 1. Uma ronda recusada: outra logo a seguir, duas vezes por pista              #
# --------------------------------------------------------------------------- #


def test_two_refused_rounds_are_asked_again_at_once_and_the_clue_goes_out():
    w, engine = hunt_with(lambda w: Refusing(w.ports.clue_engine, w.rig.clock,
                                             clue=2, rounds=2))
    t = times_of(engine, 2)
    assert len(t) >= 3
    poll = timedelta(seconds=w.orch._poll_interval_s)             # noqa: SLF001
    assert t[1] - t[0] == poll and t[2] - t[1] == poll            # one turn of the loop
    assert sum(1 for p in w.posts() if "2nd Clue" in p) == 1
    msgs = w.notices()
    assert ("pista #2: 6 tentativas recusadas — tento outra vez dentro de ~1 min "
            "(ronda 2 de 3)") in msgs
    assert ("pista #2: 6 tentativas recusadas — tento outra vez dentro de ~1 min "
            "(ronda 3 de 3)") in msgs
    assert not any("skipping this round" in m for m in msgs)


def test_three_refused_rounds_wait_for_the_window_and_say_so():
    w, engine = hunt_with(lambda w: Refusing(w.ports.clue_engine, w.rig.clock,
                                             clue=2, rounds=3), rounds=120)
    t = times_of(engine, 2)
    assert len(t) >= 4
    assert t[3] - t[2] >= WINDOW                                  # the normal window
    assert ("pista #2: 3 rondas recusadas (18 tentativas) — fica para a janela "
            "seguinte") in w.notices()
    assert sum(1 for p in w.posts() if "2nd Clue" in p) == 1     # and then it went out


def test_after_its_two_quick_rounds_a_clue_waits_for_every_window():
    """"Duas rondas rápidas por pista": a quarta ronda recusada da mesma
    pista já não é rápida."""
    w, engine = hunt_with(lambda w: Refusing(w.ports.clue_engine, w.rig.clock,
                                             clue=2, rounds=4), rounds=200)
    t = times_of(engine, 2)
    assert len(t) >= 5
    assert t[3] - t[2] >= WINDOW and t[4] - t[3] >= WINDOW
    assert any("clue generation failed (skipping this round)" in m for m in w.notices())


def test_each_clue_has_its_own_two_quick_rounds():
    class Both(Refusing):
        def next_clue(self, ctx, clue_index, prior_clues):
            if clue_index == 3 and self.clue == 2 and self.left == 0:
                self.clue, self.left = 3, 2                       # now clue 3 refuses twice
            return super().next_clue(ctx, clue_index, prior_clues)

    w, engine = hunt_with(lambda w: Both(w.ports.clue_engine, w.rig.clock,
                                         clue=2, rounds=2), rounds=80)
    poll = timedelta(seconds=w.orch._poll_interval_s)             # noqa: SLF001
    for clue in (2, 3):
        t = times_of(engine, clue)
        assert t[1] - t[0] == poll and t[2] - t[1] == poll, clue
    assert sum(1 for m in w.notices() if "(ronda 3 de 3)" in m) == 2
    assert not w.orch._refused_rounds                             # noqa: SLF001 — emptied


def test_a_post_that_failed_on_x_waits_for_the_window_as_before():
    """Pode ter saído e só a resposta falhado: repetir logo podia publicar
    a mesma pista duas vezes."""
    w = TargetWorld()
    hunt = w.launch()
    publisher = w.orch._publisher                                 # noqa: SLF001
    real, failed = publisher.post, []

    def post(text):
        if "2nd Clue" in text and not failed:
            failed.append(w.rig.clock.now())
            raise ConnectionError("X timed out")
        return real(text)

    publisher.post = post
    w.orch._hunt_timeout_h = None
    w.orch._clue_due_fn = lambda now: now + WINDOW
    w.orch._max_rounds = 40
    with pytest.raises(RuntimeError):
        w.orch._claim_loop(hunt)
    assert failed and any("clue generation failed (skipping this round)" in m
                          for m in w.notices())
    assert not any("tento outra vez dentro de ~1 min" in m for m in w.notices())


def test_any_other_failure_of_the_writer_waits_for_the_window_as_before():
    class Down:
        def __init__(self, inner):
            self.inner, self.down = inner, True

        def next_clue(self, ctx, clue_index, prior_clues):
            if clue_index == 2 and self.down:
                self.down = False
                raise RuntimeError("LLM unavailable")
            return self.inner.next_clue(ctx, clue_index, prior_clues)

    w, _engine = hunt_with(lambda w: Down(w.ports.clue_engine))
    assert any("clue generation failed (skipping this round): LLM unavailable" in m
               for m in w.notices())
    assert not any("tento outra vez" in m for m in w.notices())


def test_the_refused_round_is_a_runtime_error_with_counts_only():
    e = ClueRoundRefused("clue #4 failed guardrails after 6 attempts", clue_index=4,
                         attempts=6, reasons=1)
    assert isinstance(e, RuntimeError)
    assert (e.clue_index, e.attempts, e.reasons) == (4, 6, 1)
    assert Orchestrator._QUICK_CLUE_ROUNDS == 2


def test_the_engines_raise_it_when_every_attempt_was_refused():
    from finding_memeland.content.clue_engine import ClueDraft, ClueEngine, PersonaContext

    class Stubborn(ClueEngine):
        def generate(self, persona, clue_index, prior_clues, *, feedback=None):
            return ClueDraft(text="the answer is lantern, obviously", taunt="")

    ctx = PersonaContext(display_name="Ada", handle="@ada", bio="b",
                         avatar_description="a", voice="v", backstory="s",
                         solution_terms=["lantern"])
    with pytest.raises(ClueRoundRefused) as got:
        Stubborn(None, "m").next_clue(ctx, 4, [], max_attempts=3)
    assert (got.value.clue_index, got.value.attempts) == (4, 3)
    assert got.value.reasons >= 1 and "lantern" not in str(got.value)


# --------------------------------------------------------------------------- #
# 2. A pista só com a pista                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("clue, cleaned", [
    # hunt #20, clues 3 and 4 (the parts before the dash are Pedro's report)
    ("3 clues deep and still lost — word two of the name is the hour before dawn.",
     "word two of the name is the hour before dawn."),
    ("four clues in and still no winner — the first word hides in a lighthouse.",
     "the first word hides in a lighthouse."),
    ("Five clues in: the first word hides in a lighthouse.",
     "the first word hides in a lighthouse."),
    ("Six clues in - the word you want is what the moon pulls.",
     "the word you want is what the moon pulls."),
    # what the old rule already did, and still does
    ("five clues deep and you still need help? the second word is a tide.",
     "the second word is a tide."),
    # a dash AFTER the first full stop belongs to the clue: only the count goes
    ("3 clues in. the hour before dawn — and after.", "the hour before dawn — and after."),
    # two sentences: the old rule alone would have eaten the first, clue and all
    ("3 clues deep and still lost — word two of the name is the hour before dawn. "
     "look east.", "word two of the name is the hour before dawn. look east."),
])
def test_a_clue_count_joined_by_a_dash_leaves_the_clue(clue, cleaned):
    assert _strip_leading_meta(clue) == cleaned


@pytest.mark.parametrize("clue", [
    "one clue away from glory — the second word is a tide.",        # singular: a real opening
    "the hour before dawn — 3 clues in",                            # not at the start
    "two clues-worth of salt and still the tide.",                  # no dash after a count
    "five clues in and still no winner —",                          # nothing after: kept whole
])
def test_what_is_not_a_count_opening_is_left_alone(clue):
    assert _strip_leading_meta(clue) == clue


def test_the_writer_s_answer_comes_out_clean_and_the_taunt_as_written():
    """A limpeza é da PISTA. O taunt sai como o escritor o escreveu —
    decisão fechada do Pedro."""
    draft = parse_target_clue(
        '{"clue": "four clues in and still no winner — the first word hides in a '
        'lighthouse.", "taunt": "four clues in and still no winner", "angle": null, '
        '"image_aspect": null, "claims": []}')
    assert draft.text == "the first word hides in a lighthouse."
    assert draft.taunt == "four clues in and still no winner"


def test_the_target_writer_is_told_to_keep_them_apart():
    import inspect

    from finding_memeland.target import clues
    src = inspect.getsource(clues)
    rule = ("Clue counts and jeering belong in the taunt ONLY: the clue text never "
            "opens with \"N clues in…\" or any jab — it is the puzzle and nothing "
            "else. The taunt is a sentence of its own: it never repeats the clue, "
            "never starts it, and never ends hanging on a dash.")
    joined = " ".join(src.replace("\\\n", " ").split())
    assert rule in joined


# --------------------------------------------------------------------------- #
# 3. O gozo de um palpite noutra cadeia nunca nomeia a cadeia                   #
# --------------------------------------------------------------------------- #


class Talkative:
    """Um LLM que, se o deixarem, diz onde o tesouro NÃO está."""

    def __init__(self):
        self.asked = 0
        self.messages = self

    def create(self, **kw):
        self.asked += 1

        class _B:
            type = "text"
            text = "solana? wrong pond entirely, fren 🐸"

        class _R:
            content = [_B()]

        return _R()


def test_the_jeer_of_a_guess_on_a_chain_the_larder_never_produces_comes_from_the_pool():
    llm = Talkative()
    engine = TauntEngine(anthropic_client=llm, model="m")
    plain = engine.taunt("solana:7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", (),
                         llm=False)
    assert plain in TAUNT_POOL and llm.asked == 0
    assert "solana" not in plain.lower()
    # an ordinary wrong guess keeps its varied jeer
    assert engine.taunt("ethereum:0x" + "1a" * 20 + ":3", ()) == (
        "solana? wrong pond entirely, fren 🐸")


def test_in_the_hunt_that_guess_is_jeered_from_the_pool():
    from test_format_replies import _row, _run, _thread

    from finding_memeland.target.dryrun import replies_to
    w = TargetWorld()
    llm = Talkative()
    w.orch._taunt_engine = TauntEngine(anthropic_client=llm, model="m")   # noqa: SLF001
    hunt = w.launch()
    _thread(w, hunt, (6301, "42", "solana:7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"))
    _run(w, hunt)
    (reply,) = replies_to(w.rig, 6301)
    assert "solana" not in reply.lower() and reply.endswith("four left.")
    assert _row(w, 6301)["outcome"] == "bad_code"
    assert foreign_matcher().never_ours("tezos:KT1RJ6PbjHpwc3M5rw5s2Nbmefwbuwbdxton:1")


# --------------------------------------------------------------------------- #
# 4. Único onchain, e a página cheia contada                                    #
# --------------------------------------------------------------------------- #


def _page(own: bool, others: int, namesake: bool = False):
    rows = [(f"ETHEREUM:0XBBB:{i}", f"look alike {i}") for i in range(others)]
    if namesake:
        rows.append(("BASE:0XCCC:9", "Salt Harbor"))
    if own:
        rows.insert(0, ("ETHEREUM:0XAAA:7", "Salt Harbor"))
    return rows


class PageSearch(ScriptedSearch):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def named_items(self, base):
        return list(self.rows)


@pytest.mark.parametrize("rows, verdict, full", [
    (_page(own=True, others=49), True, 1),             # a full page: unique, counted
    (_page(own=True, others=24), True, 0),             # not full: unique, not counted
    (_page(own=True, others=48, namesake=True), False, 0),   # a namesake in a full page
    (_page(own=False, others=50), None, 0),            # crowded: not unique, not counted
])
def test_a_unique_from_a_full_page_is_counted_and_decided_as_before(rows, verdict, full):
    g = Guard(search=PageSearch(rows), page_size=50, sleep_s=0.0)
    assert g("Salt Harbor", "ethereum", "0xaaa", 7) is verdict
    assert g.stats["unique_full"] == full


def test_the_report_and_the_status_say_how_many_came_from_a_full_page():
    assert SearchPasses(unique_full=2).render() == "único com a página de 50 cheia: 2"
    assert SearchPasses().render() == ""
    from test_search_status import Search as StatusSearch
    from test_search_status import wiring
    g = Guard(search=PageSearch(_page(own=True, others=49)), page_size=50, sleep_s=0.0)
    g("Salt Harbor", "ethereum", "0xaaa", 7)
    line = wiring(market=StatusSearch(), uniqueness=g).search_line().split("\n")[-1]
    assert line == ("  unicidade desde o arranque: 1 pergunta(s) · 0 pedido(s) falhado(s)"
                    " · único com a página cheia: 1")


def test_a_deposit_reports_its_own_full_pages():
    from test_unique_transport import finder, ref

    from finding_memeland.target.prepare import Larder
    g = Guard(search=PageSearch(_page(own=False, others=0)), page_size=50, sleep_s=0.0)
    f, _calls, _clock = finder(g)

    class Pages(PageSearch):
        def named_items(self, base):
            tid = int(self.asking.rsplit(":", 1)[1])
            return [(self.asking.upper(), base)] + [
                (f"ETHEREUM:0XBBB:{i}", f"look alike {i}") for i in range(49)
            ] if tid == 1 else [(self.asking.upper(), base)]

    g._search = Pages([])                                          # noqa: SLF001
    rep = f.deposit(Larder(), [ref(1), ref(2)], chain_ok=lambda c: True)
    assert rep.added == 2 and rep.search.unique_full == 1
    assert "único com a página de 50 cheia: 1" in rep.render()


def test_the_rule_is_written_where_the_guard_is():
    from finding_memeland.target.search_guard import MarketNameUniqueness
    doc = MarketNameUniqueness.__doc__
    assert "UNIQUE ONCHAIN" in doc and "nonexistent token" in doc
    assert "lazy listing" in doc

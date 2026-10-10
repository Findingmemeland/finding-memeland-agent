"""O que as hunts #16 e #17 mostraram (09/10) — nove defeitos, nove blocos.

  1. O leitor de JSON das pistas lia da primeira "{" à última "}" e rebentava
     com dois objectos ("Extra data: line 3 column 1", #17, clue 4). Lê o
     PRIMEIRO objecto completo; uma resposta que não se lê gasta UMA das
     tentativas da ronda, não a ronda.
  2. A contagem por guarda somava uma tentativa a menos quando a ronda
     falhava toda (seis tentativas, contagens a somar cinco).
  3. A regra 1b (um termo da resposta escrito na pista) tem contador próprio,
     fora de "text rules" — só o número.
  4. A mensagem do Telegram mandava o operador aos logs buscar razões que a
     hunt-alvo nunca lá escreve; diz onde está a contagem.
  5. O aviso da palavra de endereço escrevia a palavra no log — e a cadeia e
     a plataforma fazem parte da resposta.
  6. As respostas de formato não deixavam linha: depois de um reinício o
     agente tentou repeti-las e o X recusou (403, duplicate content).
  7. O detector anti-spray voltava a zero num reinício.
  8. O /status mostrava o id da base de dados em vez do número público.
  9. Um nome com mais palavras do que o plano de pistas aguenta rebentava o
     plano (IndexError) depois de o alvo ter passado por todas as chamadas
     pagas. Recusa-se à entrada, com motivo próprio.

Três acrescentos do Pedro ao mesmo commit, cada um no bloco a que pertence:
  · (1) `"clue": null` e `"taunt": null` nunca viram a palavra "None";
  · (6) o post público de fecho não conta as linhas de formato;
  · (8) o aviso da retoma usa o número público, como o /status.

Nenhum destes testes escreve o nome de um alvo num relatório: os nomes aqui
são inventados e as asserções procuram precisamente que não apareçam.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import timedelta

import pytest
from test_target_clues import RAW, FakeTruthJudge, ctx, engine
from test_target_prepare import S, _finder, _preparer
from test_target_prepare import World as Chain

from finding_memeland.claims.matcher import CodeClaimMatcher, TargetClaimMatcher
from finding_memeland.content.clue_engine import _parse_clue
from finding_memeland.content.guardrails import check_clue
from finding_memeland.content.relic_clues import (
    MAX_NAME_WORDS,
    MIN_PIECES_PER_WORD,
    PUZZLE_CLUES,
    name_fits_plan,
    relic_ramp_plan,
)
from finding_memeland.orchestrator.state_machine import HuntState
from finding_memeland.runtime import hunt_status_line
from finding_memeland.target.claim import ClaimJudge
from finding_memeland.target.clues import (
    ONE_OBJECT_REMINDER,
    TargetClueContext,
    TargetClueEngine,
    forbidden_address_words,
    parse_target_clue,
)
from finding_memeland.target.dryrun import (
    WALLET_A,
    FakeClaimSource,
    post,
    replies_to,
)
from finding_memeland.target.dryrun import (
    TargetWorld as World,
)
from finding_memeland.target.hunt import SprayParams
from finding_memeland.target.integration import rebuild_spray
from finding_memeland.target.prepare import (
    Candidate,
    Larder,
    PrepareRefused,
    Tally,
)
from finding_memeland.target.probe import cause_of
from finding_memeland.target.selector import Target
from finding_memeland.target.templates import (
    POST_REPLY_COLLECTION_LINK,
    POST_REPLY_FORMAT,
    POST_REPLY_ONE_TOKEN,
    POST_REPLY_UNRESOLVED_LINK,
)

CLEAN = "patience is a coin nobody spends"
ON_BASE = "patience is a coin on base"            # an address word
WITH_TERM = "a pinch of salt on the tongue"       # a word of the answer (1b)
RHYME = "it rhymes with coin"                     # another text rule, puzzle phase
OTHER = "0x" + "9" * 40                           # a contract that is never the target


def _messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


# --------------------------------------------------------------------------- #
# 1. O leitor de JSON: o primeiro objecto completo; mal formada = 1 tentativa   #
# --------------------------------------------------------------------------- #


def test_two_objects_the_first_one_is_read():
    """A resposta da #17: dois objectos, um a seguir ao outro."""
    text = ('{"clue": "first", "taunt": "t", "angle": "semantic field", "claims": []}'
            '\n\n{"clue": "second", "taunt": "u"}')
    with pytest.raises(json.JSONDecodeError):       # o que a leitura antiga fazia
        json.loads(text[text.find("{"):text.rfind("}") + 1])
    d = parse_target_clue(text)
    assert d.text == "first" and d.taunt == "t" and d.angle == "semantic field"


@pytest.mark.parametrize("text", [
    'Here it is:\n{"clue": "first"}\nHope that helps.',
    '{"clue": "first"} {"clue": "second"} {"clue": "third"}',
    'a set like {a, b} is not an object — {"clue": "first"}',
    '```json\n{"clue": "first"}\n```\n\n```json\n{"clue": "second"}\n```',
])
def test_what_surrounds_the_first_object_is_ignored(text):
    assert parse_target_clue(text).text == "first"


@pytest.mark.parametrize("text", [
    "", "no json here", "{ never closed", '{"clue": ""}', '{"taunt": "only a taunt"}',
    '{"clue": "cut short", "claims": [{"type": "starts", "word": 1, "value": "a"}], "tau',
])
def test_nothing_readable_is_a_value_error_with_counts_only(text):
    with pytest.raises(ValueError) as e:
        parse_target_clue(text)
    assert "cut short" not in str(e.value) and "taunt" not in str(e.value)


def _doubled(e: TargetClueEngine) -> TargetClueEngine:
    """The writer answers every time with TWO objects — the good one first."""
    inner = e._client.messages.create

    def create(**kw):
        resp = inner(**kw)
        resp.content[0].text += "\n\n" + json.dumps({"clue": "a second object"})
        return resp
    e._client.messages.create = create
    return e


def test_a_two_object_answer_publishes_at_the_first_attempt():
    e = _doubled(engine([CLEAN, "never reached"]))
    assert e.next_clue(ctx(), 4, ["x"] * 3).text == CLEAN
    assert len(e._client.calls) == 1 and e._rejections == {}


def test_an_unreadable_answer_spends_one_attempt_not_the_round(caplog):
    """A #17 morreu à segunda resposta ilegível, com quatro tentativas por
    gastar. Agora cada uma gasta uma — e a ronda só acaba quando acabam."""
    e = engine([(RAW, "no json"), (RAW, '{"clue": "'), (RAW, "still nothing"), CLEAN])
    with caplog.at_level(logging.WARNING):
        d = e.next_clue(ctx(), 4, ["x"] * 3)
    assert d.text == CLEAN and len(e._client.calls) == 4
    assert e.last_attempt_report == (4, {"malformed answer": 3})
    assert any("published after 4 attempts — malformed answer ×3" in m
               for m in _messages(caplog))


def test_a_round_of_unreadable_answers_ends_when_the_attempts_do(caplog):
    e = engine([(RAW, "no json")] * 6 + ["never reached"])
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError) as err:
            e.next_clue(ctx(), 4, ["x"] * 3)
    assert not isinstance(err.value, ValueError)          # never the old ValueError
    assert len(e._client.calls) == 6                      # one call per attempt
    assert any("NOT produced after 6 attempts — malformed answer ×6" in m
               for m in _messages(caplog))


def test_the_reminder_asks_for_one_object_nothing_before_nothing_after():
    assert "exactly ONE JSON object" in ONE_OBJECT_REMINDER
    assert "nothing before it and nothing after it" in ONE_OBJECT_REMINDER
    e = engine([(RAW, "no json"), CLEAN])
    e.next_clue(ctx(), 4, ["x"] * 3)
    first, second = (c["messages"][0]["content"] for c in e._client.calls)
    assert ONE_OBJECT_REMINDER not in first and ONE_OBJECT_REMINDER in second
    assert "no JSON object (you reasoned" not in second       # the old wording


def test_the_reminder_is_added_to_what_the_guards_said_and_only_once():
    e = engine([ON_BASE, (RAW, "oops"), (RAW, "oops again"), CLEAN])
    e.next_clue(ctx(), 4, ["x"] * 3)
    last = e._client.calls[3]["messages"][0]["content"]
    assert "blockchain or a platform" in last               # the guard's feedback stays
    assert last.count(ONE_OBJECT_REMINDER) == 1


def test_an_unreadable_answer_never_reaches_a_log_or_a_message(caplog):
    said = 'Looking at "Harbor" — let me check its letters first: H-A-R'
    e = engine([(RAW, said)] * 6)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError) as err:
            e.next_clue(ctx(), 4, ["x"] * 3)
    for text in [str(err.value), *_messages(caplog)]:
        assert "Harbor" not in text and "letters" not in text


def test_clue_one_keeps_its_ten_attempts_through_unreadable_answers():
    e = engine([(RAW, "no json")] * 9 + [CLEAN])
    assert e.next_clue(ctx(), 1, []).text == CLEAN
    assert len(e._client.calls) == 10


# -- um campo `null` é um campo vazio, nunca a palavra "None" ---------------- #


@pytest.mark.parametrize("clue", ["null", "123", "true", '["a", "b"]', '{"x": 1}', '"   "'])
def test_a_clue_that_is_not_text_is_an_empty_clue(clue):
    """`str(None)` é "None": uma pista `null` saía como a palavra "None"."""
    for read in (parse_target_clue, _parse_clue):
        with pytest.raises(ValueError) as e:
            read('{"clue": ' + clue + ', "taunt": "a jeer"}')
        assert "empty clue" in str(e.value)


@pytest.mark.parametrize("taunt", ["null", "123", "false", '["a"]', '""'])
def test_a_taunt_that_is_not_text_is_no_taunt(taunt):
    for read in (parse_target_clue, _parse_clue):
        d = read('{"clue": "a real clue", "taunt": ' + taunt + "}")
        assert d.text == "a real clue" and d.taunt is None


def test_text_that_happens_to_say_none_is_still_the_writers_text():
    d = parse_target_clue('{"clue": "None of them saw it", "taunt": "None"}')
    assert d.text == "None of them saw it" and d.taunt == "None"


def _forcing(e: TargetClueEngine, *, first: int = 99, **fields) -> TargetClueEngine:
    """The writer's first `first` answers, with these JSON fields forced."""
    inner = e._client.messages.create
    left = [first]

    def create(**kw):
        resp = inner(**kw)
        if left[0] > 0:
            left[0] -= 1
            doc = json.loads(resp.content[0].text)
            doc.update(fields)
            resp.content[0].text = json.dumps(doc)
        return resp
    e._client.messages.create = create
    return e


def test_a_null_clue_spends_one_attempt_and_is_never_published(caplog):
    e = _forcing(engine([CLEAN, CLEAN]), first=1, clue=None)
    with caplog.at_level(logging.WARNING):
        d = e.next_clue(ctx(), 4, ["x"] * 3)
    assert d.text == CLEAN and "None" not in d.text
    assert e.last_attempt_report == (2, {"malformed answer": 1})


def test_a_null_taunt_publishes_the_clue_without_one():
    e = _forcing(engine([CLEAN]), taunt=None)
    d = e.next_clue(ctx(), 4, ["x"] * 3)
    assert d.text == CLEAN and d.taunt is None
    assert len(e._client.calls) == 1 and e._rejections == {}


# --------------------------------------------------------------------------- #
# 2. A contagem por guarda soma sempre as tentativas recusadas                  #
# --------------------------------------------------------------------------- #


def _tally_line(caplog) -> str:
    return next(m for m in _messages(caplog)
                if "NOT produced after" in m or "published after" in m)


def test_a_round_that_fails_whole_counts_every_attempt(caplog):
    """Seis tentativas, contagens a somar seis — somavam cinco."""
    e = engine([RHYME] * 6)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError):
            e.next_clue(ctx(), 4, ["x"] * 3)
    assert "NOT produced after 6 attempts — text rules ×6" in _tally_line(caplog)
    assert e._attempts == 6 and sum(e._rejections.values()) == 6


def test_a_mixed_round_adds_up_guard_by_guard(caplog):
    judge = FakeTruthJudge({"present": (False, "that is the present")})
    e = engine([RHYME, ON_BASE, WITH_TERM, (RAW, "no json"),
                "the seam of the present", RHYME], judge=judge)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError):
            e.next_clue(ctx(), 4, ["x"] * 3)
    assert e._rejections == {"text rules": 2, "address words": 1,
                             "answer term (1b)": 1, "malformed answer": 1, "judge": 1}
    assert sum(e._rejections.values()) == e._attempts == 6
    line = _tally_line(caplog)
    assert all(f"{k} ×{v}" in line for k, v in e._rejections.items())
    assert "cut short" not in line


def test_a_published_clue_counts_one_less_than_its_attempts(caplog):
    e = engine([RHYME, RHYME, CLEAN])
    with caplog.at_level(logging.WARNING):
        e.next_clue(ctx(), 4, ["x"] * 3)
    assert "published after 3 attempts — text rules ×2" in _tally_line(caplog)


def test_an_attempt_a_guard_of_ours_could_not_judge_is_named_apart(caplog):
    """Uma guarda nossa em baixo não RECUSOU a tentativa: fica dita à parte,
    pelo tipo, e as contas continuam a bater certo."""
    e = engine([ON_BASE, CLEAN], judge=FakeTruthJudge(down=True))
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError):
            e.next_clue(ctx(), 4, ["x"] * 3)
    line = _tally_line(caplog)
    assert "NOT produced after 2 attempts — address words ×1" in line
    assert "attempt 2 cut short (TruthJudgeUnavailable)" in line


# --------------------------------------------------------------------------- #
# 3. A regra 1b tem contador próprio — só o número                              #
# --------------------------------------------------------------------------- #


def test_the_text_guard_says_how_many_answer_terms_it_found():
    def run(clue):
        return check_clue(clue, clue_index=4, persona_display_name="Salt Harbor",
                          persona_handle="", persona_bio="",
                          solution_terms=["salt", "harbor"])
    assert run(CLEAN).answer_terms == 0
    assert run(WITH_TERM).answer_terms == 1
    assert run("salt in the harbor").answer_terms == 2
    assert run("the salty wind").answer_terms == 1          # substring, as before


def test_rule_1b_is_counted_apart_from_the_other_text_rules(caplog):
    e = engine([WITH_TERM, WITH_TERM, RHYME, CLEAN])
    with caplog.at_level(logging.DEBUG):
        e.next_clue(ctx(), 4, ["x"] * 3)
    assert e.last_attempt_report == (4, {"answer term (1b)": 2, "text rules": 1})
    assert any("answer term (1b) ×2, text rules ×1" in m for m in _messages(caplog))


def test_a_draft_that_breaks_1b_and_another_rule_is_one_count_under_1b():
    e = engine([WITH_TERM + ", it rhymes with malt", CLEAN])
    e.next_clue(ctx(), 4, ["x"] * 3)
    assert e._rejections == {"answer term (1b)": 1}


def test_the_1b_counter_never_writes_the_term(caplog):
    e = engine([WITH_TERM] * 6)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError) as err:
            e.next_clue(ctx(), 4, ["x"] * 3)
    for text in [str(err.value), *_messages(caplog)]:
        assert "salt" not in text.lower() and "harbor" not in text.lower()
    assert any("answer term (1b) ×6" in m for m in _messages(caplog))


# --------------------------------------------------------------------------- #
# 4. A mensagem do Telegram diz onde está o que lá está                         #
# --------------------------------------------------------------------------- #


def test_a_target_hunt_points_the_operator_at_the_tally():
    e = engine([ON_BASE] * 6)
    with pytest.raises(RuntimeError) as err:
        e.next_clue(ctx(), 4, ["x"] * 3)
    msg = str(err.value)
    assert "a contagem por guarda está nos logs" in msg
    assert "estão nos logs" not in msg and "base" not in msg


def test_an_engine_that_does_log_its_reasons_still_says_so():
    """Personas e relics registam as razões: para eles a frase antiga é
    verdadeira e fica."""
    e = engine([ON_BASE] * 6)
    e.LOG_REJECTION_REASONS = True
    with pytest.raises(RuntimeError) as err:
        e.next_clue(ctx(), 4, ["x"] * 3)
    assert "estão nos logs" in str(err.value)
    assert "contagem por guarda" not in str(err.value)


# --------------------------------------------------------------------------- #
# 5. A palavra de endereço nunca chega ao log — só quantas vezes                #
# --------------------------------------------------------------------------- #


def test_the_repeated_address_word_is_logged_as_a_count(caplog):
    e = engine([ON_BASE, ON_BASE, "patience is a coin on BASE, again", CLEAN])
    with caplog.at_level(logging.DEBUG):
        e.next_clue(ctx(), 4, ["x"] * 3)
    msgs = _messages(caplog)
    hits = [m for m in msgs if "forbidden address word" in m]
    assert hits and "3 times" in hits[-1] and "2 times" in hits[0]
    for m in msgs:
        assert forbidden_address_words(m) == [], m


def test_two_different_address_words_are_not_the_same_word_repeated(caplog):
    e = engine([ON_BASE, "seen on opensea once", CLEAN])
    with caplog.at_level(logging.DEBUG):
        e.next_clue(ctx(), 4, ["x"] * 3)
    msgs = _messages(caplog)
    assert not any("forbidden address word" in m for m in msgs)
    assert all(forbidden_address_words(m) == [] for m in msgs)
    assert e._rejections == {"address words": 2}


# --------------------------------------------------------------------------- #
# 6. As respostas de formato ficam no registo e sobrevivem a um reinício        #
# --------------------------------------------------------------------------- #


def _crash(w: World, hunt, rounds: int = 3) -> None:
    w.orch._max_rounds = rounds
    with pytest.raises(RuntimeError):
        w.orch._claim_loop(hunt)


def _restart(w: World, hunt):
    """Um processo novo: a hunt reconstruída da linha, e o fio lido outra
    vez desde o princípio — é isso que o X devolve depois de um redeploy."""
    seen = list(w.src._delivered)
    w.src = FakeClaimSource()
    w.src._delivered = seen
    w.src.reshared = set()
    w.orch._claim_source = w.src
    return w.orch._rebuild_hunt(w.rig.repo.hunts[hunt.id], HuntState.LIVE)


def _rows(w: World, tid) -> list[dict]:
    return [s for s in w.rig.repo.submissions if s.get("dm_id") == str(tid)]


def test_a_format_reply_leaves_a_row_with_its_kind_and_never_the_post():
    w = World()
    hunt = w.launch()
    t0 = hunt.live_at
    w.src.schedule[1] = lambda: [
        post(5001, "42", f"{OTHER}:7", t0 + timedelta(minutes=1), hunt.reshare_post_id)]
    _crash(w, hunt)
    assert replies_to(w.rig, 5001) == [POST_REPLY_FORMAT]
    (row,) = _rows(w, 5001)
    assert row["outcome"] == "format" and row["submitted_claim_code"] == "no_chain"
    assert row["sender_x_id"] == "42" and OTHER not in repr(row)


def test_after_a_restart_the_same_format_reply_is_not_sent_again():
    """O que aconteceu na #17: o fio é relido, os posts de formato não
    estavam no registo, e o agente respondeu-lhes outra vez."""
    w = World()
    hunt = w.launch()
    t0 = hunt.live_at
    w.src.schedule[1] = lambda: [
        post(5001, "42", f"{OTHER}:7", t0 + timedelta(minutes=1), hunt.reshare_post_id),
        post(5002, "43", OTHER, t0 + timedelta(minutes=2), hunt.reshare_post_id),
        post(5003, "44", "https://foundation.app/@someone/a-piece",
             t0 + timedelta(minutes=3), hunt.reshare_post_id)]
    _crash(w, hunt)
    before = list(w.rig.publisher.post_replies)
    assert [len(replies_to(w.rig, t)) for t in (5001, 5002, 5003)] == [1, 1, 1]

    rebuilt = _restart(w, hunt)
    processed, guesses, _c, _t, sys_sent, _p, _q = w.orch._rebuild_claim_state(rebuilt)
    assert {"5001", "5002", "5003"} <= processed
    # by TYPE since the per-type replies (tests/test_format_replies.py): "no
    # chain" and "no tokenId" are answered with the same words — one type
    assert sys_sent["format:missing"] == {"42", "43"}
    assert sys_sent["format:unreadable_link"] == {"44"}
    assert guesses == {}                              # a format slip is not a guess
    _crash(w, rebuilt)
    assert list(w.rig.publisher.post_replies) == before   # not one reply more
    assert len(_rows(w, 5001)) == 1                       # and not one row more


def test_one_reply_of_a_type_per_profile_holds_across_a_restart():
    w = World()
    hunt = w.launch()
    t0 = hunt.live_at
    w.src.schedule[1] = lambda: [
        post(5001, "42", f"{OTHER}:7", t0 + timedelta(minutes=1), hunt.reshare_post_id)]
    _crash(w, hunt)
    rebuilt = _restart(w, hunt)
    w.src.schedule[1] = lambda: [
        post(5010, "42", OTHER, t0 + timedelta(minutes=9), hunt.reshare_post_id)]
    _crash(w, rebuilt)
    assert replies_to(w.rig, 5010) == []                  # same words: answered once
    assert _rows(w, 5010)[0]["outcome"] == "format"       # but logged all the same
    assert _rows(w, 5010)[0]["submitted_claim_code"] == "no_token_id"


def test_a_multi_token_post_is_remembered_as_its_own_type_after_a_restart():
    w = World()
    hunt = w.launch()
    t0 = hunt.live_at
    two = (f"https://opensea.io/assets/ethereum/{OTHER}/1 "
           f"https://opensea.io/assets/ethereum/{OTHER}/2")
    w.src.schedule[1] = lambda: [
        post(5001, "42", two, t0 + timedelta(minutes=1), hunt.reshare_post_id)]
    _crash(w, hunt)
    assert replies_to(w.rig, 5001) == [POST_REPLY_ONE_TOKEN]
    assert _rows(w, 5001)[0]["outcome"] == "malformed"    # its own row, as before
    rebuilt = _restart(w, hunt)
    assert w.orch._rebuild_claim_state(rebuilt)[4]["format:one_token"] == {"42"}
    w.src.schedule[1] = lambda: [
        post(5010, "42", two.replace("/2", "/3"), t0 + timedelta(minutes=9),
             hunt.reshare_post_id),
        post(5011, "42", f"{OTHER}:7", t0 + timedelta(minutes=10), hunt.reshare_post_id)]
    _crash(w, rebuilt)
    assert replies_to(w.rig, 5010) == []                   # the same rule: not again
    assert replies_to(w.rig, 5011) == [POST_REPLY_FORMAT]  # another one: answered


def test_the_player_who_slipped_on_the_format_can_still_win_after_a_restart():
    w = World()
    hunt = w.launch()
    t0 = hunt.live_at
    t = hunt.target.target
    w.src.schedule[1] = lambda: [
        post(5001, "42", f"{t.contract}:{t.token_id}", t0 + timedelta(minutes=1),
             hunt.reshare_post_id)]
    _crash(w, hunt)
    rebuilt = _restart(w, hunt)
    w.src.reshared.add("42")
    w.src.schedule[1] = lambda: [
        post(5010, "42", f"ethereum:{t.contract}:{t.token_id}",
             t0 + timedelta(minutes=9), hunt.reshare_post_id)]
    w.src.schedule[4] = lambda: [
        post(5020, "42", "0x" + "a" * 40, t0 + timedelta(minutes=12),
             w.rig.repo.hunts[hunt.id].get("pending_ask_tweet_id"))]
    w.orch._max_rounds = 30
    winner = w.orch._claim_loop(rebuilt)
    assert winner is not None and winner.submission.sender_x_id == "42"


def test_a_row_that_cannot_be_written_never_costs_the_reply():
    w = World()
    hunt = w.launch()
    t0 = hunt.live_at
    real = w.rig.repo.log_submission

    def log(**fields):
        if fields.get("outcome") == "format":
            raise RuntimeError('invalid input value for enum submission_outcome: "format"')
        return real(**fields)
    w.rig.repo.log_submission = log
    w.src.schedule[1] = lambda: [
        post(5001, "42", f"{OTHER}:7", t0 + timedelta(minutes=1), hunt.reshare_post_id)]
    _crash(w, hunt)
    assert replies_to(w.rig, 5001) == [POST_REPLY_FORMAT]
    assert any("format post 5001 not logged" in m for m in w.notices())


def _won_hunt(extra_posts) -> World:
    """A whole hunt: `extra_posts(hunt, t0)` first, then the answer, the
    wallet, the payment, the reveal and the closing post."""
    w = World()
    hunt = w.launch()
    t0 = hunt.live_at
    t = hunt.target.target
    w.src.reshared.add("42")
    w.src.schedule[1] = lambda: [
        *extra_posts(hunt, t0),
        post(5900, "42", f"ethereum:{t.contract}:{t.token_id}",
             t0 + timedelta(minutes=5), hunt.reshare_post_id)]
    w.src.schedule[5] = lambda: [
        post(5950, "42", WALLET_A, t0 + timedelta(minutes=8),
             w.rig.repo.hunts[hunt.id].get("pending_ask_tweet_id"))]
    winner = w.orch._claim_loop(hunt)
    receipt = w.orch._pay(hunt, winner)
    w.orch._reveal(hunt, winner, receipt)
    w.orch._retire(hunt)
    return w


def test_the_public_closing_count_leaves_the_format_rows_out():
    """Uma resposta de formato não é um palpite (Pedro, 09/10): a linha
    existe para o reinício, e o número público continua a dizer o que dizia."""
    w = _won_hunt(lambda hunt, t0: [
        post(5001, "43", f"{OTHER}:7", t0 + timedelta(minutes=1), hunt.reshare_post_id),
        post(5002, "44", OTHER, t0 + timedelta(minutes=2), hunt.reshare_post_id),
        post(5003, "45", f"ethereum:{OTHER}:1", t0 + timedelta(minutes=3),
             hunt.reshare_post_id)])
    outcomes = sorted(r["outcome"] for r in w.rig.repo.submissions)
    assert outcomes == ["bad_code", "format", "format", "won"]
    assert w.posts()[-1] == "Hunt #1 closed. 2 submissions logged for public audit."
    done = [m for m in w.notices() if m.startswith("hunt #1 done;")]
    assert len(done) == 1 and "2 format reply row(s) logged apart" in done[0]


def test_a_hunt_without_format_rows_closes_exactly_as_before():
    w = _won_hunt(lambda hunt, t0: [
        post(5003, "45", f"ethereum:{OTHER}:1", t0 + timedelta(minutes=3),
             hunt.reshare_post_id)])
    assert w.posts()[-1] == "Hunt #1 closed. 2 submissions logged for public audit."
    done = [m for m in w.notices() if m.startswith("hunt #1 done;")]
    assert len(done) == 1 and "logged apart" not in done[0]


def _matcher(resolve=None) -> TargetClaimMatcher:
    return TargetClaimMatcher(
        judge=ClaimJudge(target_id="ethereum:0x" + "ab" * 20 + ":1"),
        resolve_link=resolve, format_reply=POST_REPLY_FORMAT,
        unresolved_reply=POST_REPLY_UNRESOLVED_LINK,
        one_token_reply=POST_REPLY_ONE_TOKEN,
        collection_reply=POST_REPLY_COLLECTION_LINK)


@pytest.mark.parametrize("text, kind, reply", [
    (f"{OTHER}:7", "no_chain", POST_REPLY_FORMAT),
    (f"here: {OTHER}", "no_token_id", POST_REPLY_FORMAT),
    (f"https://rarible.com/base/collections/{OTHER}", "collection_link",
     POST_REPLY_COLLECTION_LINK),
    ("https://foundation.app/@someone/a-piece", "unreadable_link",
     POST_REPLY_UNRESOLVED_LINK),
    (f"ethereum:{OTHER}:1 base:{OTHER}:2", "one_token", POST_REPLY_ONE_TOKEN),
    (f"ethereum:{OTHER}:1", None, None),               # a claim: judged, not taught
    ("gm, is it a lighthouse?", None, None),           # chatter
])
def test_every_format_reply_has_a_kind_and_the_reply_did_not_change(text, kind, reply):
    m = _matcher()
    assert m.format_hint(text) == reply
    assert m.format_kind(text) == kind
    assert CodeClaimMatcher("ABCD1234").format_kind(text) is None


def test_asking_for_the_kind_costs_no_second_resolver_call():
    calls = []

    def resolve(url):
        calls.append(url)
        return None
    m = _matcher(resolve)
    link = "https://foundation.app/@someone/a-piece"
    assert m.format_hint(link) == POST_REPLY_UNRESOLVED_LINK and len(calls) == 1
    assert m.format_kind(link) == "unreadable_link" and len(calls) == 1
    m.format_hint(link)                                # a later post: read again
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# 7. O anti-spray reconstrói-se do registo                                      #
# --------------------------------------------------------------------------- #

TIGHT = SprayParams(min_total_guesses=3, min_distinct_ratio=0.9)


def _wrong(w: World, hunt, tid, author, label, outcome="bad_code") -> None:
    w.rig.repo.log_submission(
        hunt_id=hunt.id, dm_id=str(tid), sender_x_id=str(author), wallet=None,
        sender_handle=f"u{author}", submitted_claim_code=label, outcome=outcome,
        x_created_at=hunt.live_at + timedelta(seconds=int(tid) % 1000))


def test_the_log_is_rebuilt_from_the_wrong_guesses_and_nothing_else():
    w = World()
    hunt = w.launch()
    _wrong(w, hunt, 7001, 70, f"ethereum:{OTHER}:1")
    _wrong(w, hunt, 7002, 70, f"base:{OTHER}:2")
    _wrong(w, hunt, 7003, 71, f"ethereum:{OTHER}:3")
    _wrong(w, hunt, 7004, 72, f"https://foundation.app/@x/{OTHER}")   # a link, read by the resolver
    _wrong(w, hunt, 7005, 73, None)                                    # no label
    _wrong(w, hunt, 7006, 74, "no_chain", outcome="format")
    _wrong(w, hunt, 7007, 75, "3 tokens", outcome="malformed")
    _wrong(w, hunt, 7008, 76, f"ethereum:{OTHER}:9", outcome="spam_capped")
    before = len(w.notices())
    log, state = rebuild_spray(w.orch, hunt)
    assert log == [("70", f"ethereum:{OTHER}:1"), ("70", f"base:{OTHER}:2"),
                   ("71", f"ethereum:{OTHER}:3")]
    assert state == {}
    (notice,) = w.notices()[before:]
    assert "anti-spray detector rebuilt from the log" in notice
    assert "3 wrong guess(es), 2 account(s), 3 distinct target(s)" in notice
    assert "0x" not in notice and "NOT pause" not in notice


def test_nothing_to_rebuild_is_silence():
    w = World()
    hunt = w.launch()
    before = len(w.notices())
    assert rebuild_spray(w.orch, hunt) == ([], {})
    assert w.notices()[before:] == []
    w.ports.spray = None                                   # no detector at all
    _wrong(w, hunt, 7001, 70, f"ethereum:{OTHER}:1")
    assert rebuild_spray(w.orch, hunt) == ([], {})


def test_a_detector_that_had_fired_comes_back_fired_and_says_so():
    w = World(live_params=TIGHT)
    hunt = w.launch()
    for n in range(1, 6):                                  # five accounts, five pieces
        _wrong(w, hunt, 7000 + n, 70 + n, f"ethereum:{OTHER}:{n}")
    before = len(w.notices())
    log, state = rebuild_spray(w.orch, hunt)
    assert len(log) == 5 and state == {"fired": True}
    (notice,) = w.notices()[before:]
    assert "will NOT pause this hunt again" in notice and "0x" not in notice
    assert "0.9" not in notice and "90%" not in notice     # the thresholds are reserved


def test_fired_is_replayed_guess_by_guess_not_read_off_the_final_log():
    """Disparou ao 4.º palpite; os seis seguintes repetiram a mesma peça e o
    registo INTEIRO já não dispara. Disparou na mesma — e só dispara uma vez."""
    w = World(live_params=TIGHT)
    hunt = w.launch()
    for n in range(1, 5):
        _wrong(w, hunt, 7000 + n, 70 + n, f"ethereum:{OTHER}:{n}")
    for n in range(5, 11):
        _wrong(w, hunt, 7000 + n, 70 + n, f"ethereum:{OTHER}:1")
    log, state = rebuild_spray(w.orch, hunt)
    assert not w.ports.spray.evaluate(log).triggered
    assert state == {"fired": True}


def test_a_log_that_cannot_be_read_is_said_and_never_kills_the_hunt():
    w = World()
    hunt = w.launch()

    def down(hunt_id):
        raise TimeoutError("db")
    w.rig.repo.submissions_for_hunt = down
    before = len(w.notices())
    assert rebuild_spray(w.orch, hunt) == ([], {})
    (notice,) = w.notices()[before:]
    assert "could not be rebuilt" in notice and "TimeoutError" in notice


def test_a_restart_no_longer_sends_the_detector_back_to_zero():
    """Três palpites errados antes do reinício, um depois: o quarto é que
    dispara. Com o detector a zero, depois do reinício só via um."""
    w = World(live_params=TIGHT)
    hunt = w.launch()
    t0 = hunt.live_at
    w.src.schedule[1] = lambda: [
        post(8000 + n, str(80 + n), f"ethereum:{OTHER}:{n}",
             t0 + timedelta(minutes=n), hunt.reshare_post_id) for n in (1, 2, 3)]
    _crash(w, hunt)
    assert w.control.pause_calls == 0
    rebuilt = _restart(w, hunt)
    w.src.schedule[1] = lambda: [
        post(8004, "84", f"ethereum:{OTHER}:4", t0 + timedelta(minutes=9),
             hunt.reshare_post_id)]
    _crash(w, rebuilt)
    assert w.control.pause_calls == 1
    assert any("PAUSE for review" in m for m in w.notices())
    assert rebuilt.state is HuntState.LIVE                 # a pause, never a void


def test_a_hunt_already_reviewed_is_not_paused_again_by_the_same_guesses():
    w = World(live_params=TIGHT)
    hunt = w.launch()
    t0 = hunt.live_at
    w.src.schedule[1] = lambda: [
        post(8000 + n, str(80 + n), f"ethereum:{OTHER}:{n}",
             t0 + timedelta(minutes=n), hunt.reshare_post_id) for n in range(1, 6)]
    _crash(w, hunt)
    assert w.control.pause_calls == 1
    w.control._paused = False                              # the operator's /resume
    rebuilt = _restart(w, hunt)
    w.src.schedule[1] = lambda: [
        post(8009, "89", f"ethereum:{OTHER}:9", t0 + timedelta(minutes=9),
             hunt.reshare_post_id)]
    _crash(w, rebuilt)
    assert w.control.pause_calls == 1
    assert sum("PAUSE for review" in m for m in w.notices()) == 1


# --------------------------------------------------------------------------- #
# 8. O /status mostra o número público                                          #
# --------------------------------------------------------------------------- #


class _Repo:
    def __init__(self, rows):
        self._rows = rows

    def active_hunts(self):
        return self._rows


def test_status_shows_the_public_number_with_the_db_id_beside_it():
    line = hunt_status_line(
        _Repo([{"id": 19, "hunt_number": 17, "state": "live"}]), local_active=True)
    assert line == "hunt: #17 (db 19) LIVE"


def test_status_keeps_the_pause_and_the_alarms_after_the_number():
    line = hunt_status_line(
        _Repo([{"id": 19, "hunt_number": 17, "state": "live", "paused": True},
               {"id": 20, "hunt_number": 18, "state": "preparing"}]), local_active=True)
    assert line.startswith("hunt: #17 (db 19) LIVE | ⏸ PAUSED")
    assert "+1 MORE active hunt(s)" in line


@pytest.mark.parametrize("row", [{"id": 4, "state": "live"},
                                 {"id": 4, "hunt_number": None, "state": "live"}])
def test_a_row_from_before_the_numbering_shows_its_id(row):
    assert hunt_status_line(_Repo([row]), local_active=True) == "hunt: #4 LIVE"


def test_an_idle_agent_still_says_exactly_what_the_push_rule_reads():
    assert hunt_status_line(_Repo([]), local_active=False) == "hunt: none (idle)"


def test_the_resume_notice_speaks_the_public_number_too():
    """O mesmo engano, noutro sítio: o aviso da retoma dizia o id da base."""
    w = World()
    hunt = w.launch()
    row = w.rig.repo.hunts[hunt.id]
    row["hunt_number"] = 17                        # the public number, not the id
    row["target_ctx_sealed"] = "not a sealed blob"
    before = len(w.notices())
    w.orch._rebuild_hunt(row, HuntState.LIVE)
    (notice,) = [m for m in w.notices()[before:] if "artwork description" in m]
    assert notice.startswith("hunt #17: ") and f"#{hunt.id}:" not in notice


# --------------------------------------------------------------------------- #
# 9. Um nome com mais palavras do que o plano aguenta é recusado à entrada      #
#    — palavras DE CONTEÚDO desde 10/10 (tests/test_name_words.py): até lá     #
#    contava-se por espaços, e um número ou um "&" soltos eram palavras.       #
# --------------------------------------------------------------------------- #

FOUR = "Quiet Lantern Above Water"        # four content words: more than the plan holds
THREE = "Quiet Lantern Above"


def test_the_plan_holds_three_words_and_the_limit_comes_from_the_plan():
    assert MAX_NAME_WORDS == PUZZLE_CLUES // MIN_PIECES_PER_WORD == 3


@pytest.mark.parametrize("name, fits", [
    ("Lantern", True), ("Quiet Lantern", True), (THREE, True),
    (FOUR, False), ("Quiet Lantern Above The Water", False),   # "The" is not one of the four
    ("Name 42 Of Two", True),              # 10/10: a number is not a word, "Of" is not content
    ("Sun & Moon Rising", True),           # 10/10: nor is a sign on its own
    ("Lion-Hearted King Rises", True),     # a hyphen does not split a word
])
def test_a_name_fits_exactly_when_the_plan_can_be_built(name, fits):
    assert name_fits_plan(name) is fits
    if fits:
        assert len(relic_ramp_plan(name)) == PUZZLE_CLUES
    else:
        with pytest.raises(ValueError) as e:
            relic_ramp_plan(name)
        assert not isinstance(e.value, IndexError)
        assert all(w not in str(e.value) for w in name.split() if len(w) > 2)


def test_the_clue_context_refuses_a_long_name_by_its_count_never_by_its_words():
    target = Target(chain="ethereum", contract="0x" + "ab" * 20, token_id=7,
                    name=FOUR, name_onchain=FOUR, description="",
                    image="ipfs://QmImage", metadata_sha256="ab" * 32, epoch="e1")
    with pytest.raises(ValueError) as e:
        TargetClueContext.from_target(target, image_description="a lighthouse")
    assert "4 content words" in str(e.value) and "Lantern" not in str(e.value)


def test_a_long_name_is_refused_at_the_deposit_before_anything_is_spent():
    chain = Chain(default_name=FOUR)
    larder = Larder()
    rep = _finder(chain).deposit(larder, [f"ethereum:{S.contract}:5"])
    assert rep.added == 0 and larder.size() == 0
    assert rep.rejected.long_name == 1 and rep.rejected.name == 0
    assert chain.probes == [] and chain.paid_calls == 0     # no image, no paid search
    report = rep.render()
    assert "nome-longo 1" in report
    assert all(w not in report for w in FOUR.split())


def test_three_words_still_go_in():
    chain = Chain(default_name=THREE)
    larder = Larder()
    rep = _finder(chain).deposit(larder, [f"ethereum:{S.contract}:5"])
    assert rep.added == 1 and rep.rejected.long_name == 0
    assert "nome-longo" not in rep.render()


def test_the_draw_refuses_it_too_and_names_the_cause_apart():
    chain = Chain(default_name=FOUR)
    larder = Larder()
    tally = _finder(chain).fill(larder, want=2, max_draws=10)
    assert larder.size() == 0 and tally.long_name == 10 and tally.name == 0
    assert chain.paid_calls == 0
    assert "nome-longo 10" in tally.render() and "nome 10" not in tally.render()


def _stored(token_id: int, name: str) -> Candidate:
    return Candidate(chain="ethereum", contract=S.contract, token_id=token_id,
                     name=name, name_onchain=name, description="d",
                     image=f"ipfs://img{token_id}", token_uri=f"ipfs://Qm{token_id}",
                     artist="", metadata={"name": name})


def test_prepare_discards_a_long_name_it_finds_in_the_larder_and_says_why():
    """Um alvo depositado antes de 09/10. Saía na mesma (por 'sem-pista'),
    mas depois da pesquisa paga, da arte inteira e da visão."""
    chain = Chain(names={5: FOUR})
    larder = Larder()
    larder.add(_stored(5, FOUR))
    said: list[str] = []
    described: list[bytes] = []
    chain.describe = lambda data: described.append(data) or "an artwork"
    with pytest.raises(PrepareRefused) as e:
        _preparer(chain, _finder(chain), notify=said.append).prepare(larder)
    assert larder.size() == 0                               # it is gone, as before
    assert chain.paid_calls == 0 and chain.full_reads == [] and described == []
    assert any("mais palavras do que o plano de pistas aguenta" in m for m in said)
    assert "nome-longo 1" in str(e.value)
    for text in [str(e.value), *said]:
        assert all(w not in text for w in FOUR.split())


def test_prepare_goes_on_to_the_next_target():
    chain = Chain(names={5: FOUR, 6: THREE})
    larder = Larder()
    larder.add(_stored(5, FOUR))
    larder.add(_stored(6, THREE))
    for seed in range(6):                                   # whichever comes out first
        lar = Larder()
        lar.add(_stored(5, FOUR))
        lar.add(_stored(6, THREE))
        p = _preparer(chain, _finder(chain))
        p._rng = random.Random(seed)
        prepared, _left = p.prepare(lar)
        assert prepared.target.token_id == 6


def test_the_probe_names_the_same_cause():
    assert cause_of(Tally(long_name=1)) == ("nome-longo", None)
    assert cause_of(Tally(name=1)) == ("nome", None)

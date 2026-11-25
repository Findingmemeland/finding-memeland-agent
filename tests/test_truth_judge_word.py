"""O juiz verifica cada afirmação da pista contra a palavra (Pedro, 25/11).

Na #42 o nome era "The Hair That Speaks", e duas pistas sobre a palavra 2
("Hair") afirmaram coisas falsas e foram publicadas:
  · pista 1: "word 2 of the name is on the same shelf as grief and
    inheritance — not the same shelf as scissors or salons". Aponta para
    "heir" e nega "hair", que era a resposta;
  · pista 3: "word 2 sits with the other action word the way a provocation
    sits with its proof — it sets up the demand; word 4 delivers it". Faz de
    "Hair" uma "action word", e ali é um substantivo.
Nenhuma guarda mecânica as vê (numeração, letras, listas): só o juiz, que
lê a pista ao lado da resposta. E o juiz deu as duas como verdadeiras.

Agora o juiz:
  · vê as palavras do nome numeradas e qual é a da pista ("word 2 of the
    name, 'Hair'");
  · fixa primeiro a palavra: classe gramatical tal como o nome a usa, os
    seus significados, os homófonos;
  · lista CADA afirmação da pista (negações, comparações e rótulos
    gramaticais incluídos) e diz se se verifica;
  · e o código recusa a pista com UMA afirmação falsa, diga o resumo do juiz
    o que disser. Na #42 o resumo foi "verdadeira".

O que o modelo responde não se testa aqui. Aqui testa-se o que lhe é
perguntado, como a resposta é lida e o que o motor faz com ela. As respostas
do "modelo" abaixo são as que um juiz que segue TRUTH_JUDGE_SYSTEM dá.

As pistas 1 e 3 são as da #42, palavra por palavra (textos do Pedro,
25/11). O nome é o da #42, já revelado.
"""
from __future__ import annotations

import json
import logging

from test_target_clues import FakeAnthropic, _Resp

from finding_memeland.target.clues import (
    TARGET_SYSTEM_PROMPT,
    TRUTH_JUDGE_SYSTEM,
    AnthropicTruthJudge,
    TargetClueContext,
    TargetClueEngine,
)
from finding_memeland.target.selector import Target

NAME = "The Hair That Speaks"                     # a #42, já revelada

HEIR_FOR_HAIR = ("word 2 of the name is on the same shelf as grief and inheritance "
                 "— not the same shelf as scissors or salons")          # a pista 1 da #42
ACTION_WORD = ("word 2 sits with the other action word the way a provocation sits "
               "with its proof — it sets up the demand; word 4 delivers it")  # a pista 3
GOOD_1 = "what the barber harvests and the comb tends"
GOOD_3 = "it keeps growing long after the barber leaves"

HAIR = {"part_of_speech": "noun",
        "meanings": ["the strands that grow from the skin and the scalp"],
        "homophones": ["heir", "hare"]}

# O resumo diz "consistent": como na #42, que publicou a pista. As afirmações,
# sozinhas, têm de a afundar.
CLUE_1_VERDICT = {
    "word": HAIR,
    "assertions": [
        {"says": "word 2 is on the same shelf as grief and inheritance", "holds": False,
         "why": "that shelf is HEIR's, a word that only sounds like it"},
        {"says": "word 2 is not on the same shelf as scissors or salons", "holds": False,
         "why": "scissors and salons are exactly HAIR's — the clue denies the word"},
    ],
    "consistent": True, "reason": "a sideways sound clue"}
CLUE_3_VERDICT = {
    "word": HAIR,
    "assertions": [
        {"says": "word 2 is an action word, like word 4", "holds": False,
         "why": "HAIR is a noun in this name; SPEAKS is its only action word"},
        {"says": "word 4 is an action word", "holds": True,
         "why": "SPEAKS is a verb"},
    ],
    "consistent": False, "reason": "wrong part of speech"}


def _ctx() -> TargetClueContext:
    t = Target(chain="ethereum", contract="0x3b3ee1931dc30c1957379fac9aba94d1c48a5405",
               token_id=41234, name=NAME, name_onchain=NAME, description="",
               image="ipfs://QmImage", metadata_sha256="ab" * 32, epoch="e1")
    return TargetClueContext.from_target(t, image_description="a portrait that talks")


class _JudgeModel:
    """Faz de modelo do juiz: uma linha de raciocínio e depois o JSON.
    `answers`: um pedaço da pista → o JSON. Os canários são falsos; qualquer
    outra pista é verdadeira."""

    def __init__(self, answers=None, raw=None):
        self.answers = answers or {}
        self.raw = raw
        self.calls = []
        outer = self

        class _Messages:
            def create(_s, **kw):
                outer.calls.append(kw)
                if outer.raw is not None:
                    return _Resp(outer.raw)
                clue = kw["messages"][0]["content"].split("CLUE: ", 1)[1]
                doc = next((d for k, d in outer.answers.items() if k in clue), None)
                if doc is None and ("single letter" in clue or "number zero" in clue):
                    doc = {"word": HAIR, "consistent": False, "reason": "canary",
                           "assertions": [{"says": clue, "holds": False, "why": "canary"}]}
                if doc is None:
                    doc = {"word": HAIR, "consistent": True, "reason": "fits",
                           "assertions": [{"says": clue, "holds": True, "why": "fits"}]}
                return _Resp("Checking the word first.\n" + json.dumps(doc))
        self.messages = _Messages()

    def asked(self, clue: str) -> str:
        return next(c["messages"][0]["content"] for c in self.calls
                    if f"CLUE: {clue}" in c["messages"][0]["content"])


def _engine(drafts, model):
    judge = AnthropicTruthJudge(model, "m", sleep=lambda s: None)
    return TargetClueEngine(FakeAnthropic(drafts), "m", search_guard=False,
                            truth_judge=judge, solver=False)


def _judge(doc=None, *, raw=None, clue=HEIR_FOR_HAIR, name=NAME, word="Hair"):
    model = _JudgeModel({clue: doc} if doc is not None else None, raw=raw)
    v = AnthropicTruthJudge(model, "m", sleep=lambda s: None).check(
        clue, name=name, word=word, artwork="a portrait that talks")
    return v, model


# --------------------------------------------------------------------------- #
# Os dois casos da #42, pelo motor inteiro                                     #
# --------------------------------------------------------------------------- #


def test_hunt_42_clue_1_heir_for_hair_is_refused_and_rewritten(caplog):
    model = _JudgeModel({"grief and inheritance": CLUE_1_VERDICT})
    e = _engine([HEIR_FOR_HAIR, GOOD_1], model)
    with caplog.at_level(logging.INFO):
        d = e.next_clue(_ctx(), 1, [])
    assert d.text == GOOD_1 and e._rejections == {"judge": 1}
    # o escritor fica a saber o que era falso, e porquê
    fb = e._client.calls[1]["messages"][0]["content"]
    assert "CONSISTENCY JUDGE" in fb and "HEIR's" in fb and "denies the word" in fb
    assert "never deny one" in fb and "only sounds like it" in fb
    # o juiz viu a palavra, o seu número e o nome numerado
    asked = model.asked(HEIR_FOR_HAIR)
    assert "This clue is about word 2 of the name, 'Hair'." in asked
    assert "its words, numbered: 1 'The', 2 'Hair', 3 'That', 4 'Speaks'" in asked
    # o nome nunca chega aos registos
    assert not [r for r in caplog.records if "hair" in r.getMessage().lower()]


def test_hunt_42_clue_3_an_action_word_for_a_noun_is_refused_and_rewritten():
    model = _JudgeModel({"action word": CLUE_3_VERDICT})
    e = _engine([ACTION_WORD, GOOD_3], model)
    d = e.next_clue(_ctx(), 3, ["clue one", "clue two"])
    assert d.text == GOOD_3 and e._rejections == {"judge": 1}
    fb = e._client.calls[1]["messages"][0]["content"]
    assert "a noun in this name" in fb and "part of speech" in fb
    assert "This clue is about word 2 of the name, 'Hair'." in model.asked(ACTION_WORD)


def test_both_hunt_42_clues_get_past_every_mechanical_guard():
    """Porque é o juiz que tem de as ver: sem ele, as duas saíam publicadas."""
    for i, text in ((1, HEIR_FOR_HAIR), (3, ACTION_WORD)):
        e = TargetClueEngine(FakeAnthropic([text]), "m", search_guard=False,
                             truth_judge=False, solver=False)
        assert e.next_clue(_ctx(), i, ["x"] * (i - 1), max_attempts=1).text == text


# --------------------------------------------------------------------------- #
# Como a resposta do juiz é lida                                               #
# --------------------------------------------------------------------------- #


def test_one_false_assertion_rejects_whatever_the_summary_says():
    v, _ = _judge(CLUE_1_VERDICT)                      # resumo: "consistent": true
    assert v.consistent is False
    assert v.reason.startswith("false about the word: ")
    assert "HEIR's" in v.reason and "denies the word" in v.reason


def test_a_clue_whose_assertions_all_hold_passes():
    v, _ = _judge({"word": HAIR, "consistent": True, "reason": "fits",
                   "assertions": [{"says": "the barber harvests it", "holds": True,
                                   "why": "barbers cut hair"}]}, clue=GOOD_1)
    assert v.consistent is True and v.reason == "fits"


def test_the_old_answer_shape_still_reads():
    assert _judge({"consistent": True, "reason": "fits"})[0].consistent is True
    assert _judge({"consistent": False, "reason": "no"})[0].consistent is False


def test_false_written_as_a_string_is_false():
    """bool("false") é True: a leitura antiga deixava passar esta pista."""
    v, _ = _judge({"consistent": "false", "reason": "points at heir"})
    assert v.consistent is False
    v, _ = _judge({"consistent": True, "reason": "x",
                   "assertions": [{"says": "an action word", "holds": "false", "why": "a noun"}]})
    assert v.consistent is False


def test_the_nested_answer_is_read_whole_after_the_reasoning():
    doc = json.dumps(CLUE_3_VERDICT)
    v, _ = _judge(raw="I settle {the word} first: a noun.\n" + doc, clue=ACTION_WORD)
    assert v.consistent is False and "a noun in this name" in v.reason


def test_an_unreadable_answer_still_fails_closed_without_the_text():
    v, model = _judge(raw='HAIR is a noun {"says": "x", "holds": false}')
    assert v.consistent is None and v.reason == "ValueError" and len(model.calls) == 3
    v, _ = _judge(raw='{"consistent": "maybe", "reason": "Hair"}')
    assert v.consistent is None and "Hair" not in v.reason


# --------------------------------------------------------------------------- #
# O que é perguntado                                                          #
# --------------------------------------------------------------------------- #


def test_the_judge_sees_the_word_its_number_and_the_numbered_name():
    _, model = _judge(CLUE_1_VERDICT)
    kw = model.calls[0]
    assert kw["system"] == TRUTH_JUDGE_SYSTEM and kw["max_tokens"] >= 1500
    user = kw["messages"][0]["content"]
    assert "ANSWER — name: The Hair That Speaks" in user
    assert "its words, numbered: 1 'The', 2 'Hair', 3 'That', 4 'Speaks'" in user
    assert "This clue is about word 2 of the name, 'Hair'." in user
    assert "Check every assertion it makes against the word" in user


def test_a_word_the_name_has_twice_is_named_without_a_number():
    _, model = _judge(CLUE_1_VERDICT, name="Hair to Hair")
    user = model.calls[0]["messages"][0]["content"]
    assert "This clue is about the word 'Hair' of the name." in user


def test_an_art_piece_is_checked_against_the_artwork():
    _, model = _judge(CLUE_1_VERDICT, word=None)
    assert "This clue is about the ARTWORK." in model.calls[0]["messages"][0]["content"]


def test_the_judge_is_told_to_check_grammar_meaning_and_sound():
    flat = " ".join(TRUTH_JUDGE_SYSTEM.split())
    assert "its part of speech AS THE NAME USES IT" in flat
    assert "its homophones (other words that sound the same)" in flat
    assert "every negation" in flat and "every grammatical label" in flat
    assert "when it DENIES the word one of its own meanings or worlds" in flat
    assert "describes a word that only sounds like it as if it were the word" in flat
    assert "ONE false assertion makes the clue inconsistent" in flat
    assert '"holds": true|false' in flat and '"assertions": [' in flat


def test_the_writer_is_told_the_same_before_the_judge_sees_it():
    flat = " ".join(TARGET_SYSTEM_PROMPT.split())
    assert "TRUE OF THE WORD: oblique is not false." in flat
    assert 'never call a naming word an "action word", nor the reverse' in flat
    assert "never DENY one" in flat and "only SOUNDS like it" in flat
    TARGET_SYSTEM_PROMPT.format(index=1, obliqueness=0.9, hard_floor=0.6, phase_rules="")

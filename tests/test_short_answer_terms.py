"""Regra 1b para termos CURTOS (09/10, decisão do Pedro).

Um termo da resposta procura-se na pista como SUBSTRING. Para uma palavra de
duas ou três letras isso recusa meia língua: "or" está dentro de for, more,
story, word, work e world; "it" dentro de with; "no" dentro de know, not e
now. A hunt #17 perdeu cinco rondas de pistas para oito dessas palavras; a
correcção mínima de 07/10 tirou essas oito da lista e deixou todas as outras
palavras curtas de um nome a fazer o mesmo.

Agora, NO MOTOR-ALVO: um termo com menos de 4 letras só é recusado como
PALAVRA — ela própria, o plural, o possessivo. Os termos de 4 letras ou mais
ficam exactamente como estavam: substring e raízes.

O que isto deixa passar, dito às claras: um derivado ("sunny", "sunset") já
não tropeça na 1b. Nas pistas 1–7 o solucionador cego continua por trás.

É um INTERRUPTOR. Personas e relics partilham a mesma função e ficam como
estavam — os termos deles (partes de um @) são para apanhar dentro de palavras.
"""
from __future__ import annotations

import pytest
from test_target_clues import ITEM_ID, engine

from finding_memeland.content.guardrails import SHORT_TERM_LEN, _writes_term, check_clue
from finding_memeland.content.relic_clues import PUZZLE_CLUES, RelicClueEngine
from finding_memeland.target.clues import _FUNCTION_WORDS, TargetClueContext, TargetClueEngine
from finding_memeland.target.selector import Target


def _check(clue: str, terms, **kw):
    return check_clue(clue, clue_index=PUZZLE_CLUES + 2, persona_display_name="Xyzzy Qwerty",
                      persona_handle="", persona_bio="", solution_terms=list(terms), **kw)


def _leaks(clue: str, terms, **kw) -> bool:
    return any("solution term(s)" in r for r in _check(clue, terms, **kw).reasons)


# --------------------------------------------------------------------------- #
# 1. Um termo curto é recusado como palavra — plural e possessivo incluídos     #
# --------------------------------------------------------------------------- #


def test_short_means_under_four_letters():
    assert SHORT_TERM_LEN == 4


@pytest.mark.parametrize("term, clue", [
    ("sun", "the sun rises late here"), ("sun", "The SUN, again"),
    ("sun", "two suns over the water"),                 # plural
    ("sun", "the sun's edge"), ("sun", "the sun’s edge"),   # possessive, both apostrophes
    ("sun", "a sun-kissed wall"),                       # a hyphen ends the word
    ("box", "three boxes on a shelf"),                  # -es
    ("sky", "under open skies"),                        # -y -> -ies
    ("or", "this or that"), ("it", "it waits"), ("it", "its weight is known"),
    ("red", "the reds of autumn"), ("art", "arts and crafts"), ("day", "days go by"),
])
def test_the_word_itself_its_plural_and_its_possessive_are_refused(term, clue):
    assert _writes_term(clue.lower(), term, True) is True
    assert _leaks(clue, [term], short_terms_whole_word=True)


@pytest.mark.parametrize("term, clue", [
    ("or", "more work for the world, word by word, before the story ends"),
    ("it", "with a little wit and without a sound"),
    ("no", "you know it now, do you not"),
    ("we", "they were well between two walls"),
    ("is", "this was his"),
    ("de", "under the stairs, inside the wall"),
    ("la", "a large place"),
    ("art", "part of the start"), ("red", "a tired hundred"), ("day", "today, as always"),
    ("man", "many a human hand"), ("one", "a lonely stone"),
])
def test_a_short_term_inside_another_word_no_longer_refuses_the_clue(term, clue):
    assert _leaks(clue, [term]) is True                      # how it was, and still is by default
    assert _leaks(clue, [term], short_terms_whole_word=True) is False
    assert _check(clue, [term], short_terms_whole_word=True).ok


@pytest.mark.parametrize("term, clue", [
    ("sun", "a sunny afternoon"), ("sun", "sunset over the pier"), ("sky", "the skyline"),
    ("red", "redder than rust"), ("cat", "a catalogue of losses"),
])
def test_what_it_lets_through_said_plainly(term, clue):
    """O custo do outro lado: um derivado da palavra passa a 1b. Fica aqui
    escrito para ninguém o descobrir por acaso."""
    assert _leaks(clue, [term], short_terms_whole_word=True) is False


# --------------------------------------------------------------------------- #
# 2. Os termos de 4 letras ou mais ficam exactamente como estavam               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("term, clue, reason", [
    ("salt", "a salty wind", "solution term(s)"),            # substring, 4 letters
    ("lantern", "two lanterns wait", "solution term(s)"),
    ("harbor", "a harbored grudge", "solution term(s)"),
    ("severus", "a severe keeper", "ROOT VARIANT"),          # the root rule, 6+ letters
])
def test_long_terms_keep_the_substring_and_the_roots(term, clue, reason):
    for switch in (False, True):
        reasons = _check(clue, [term], short_terms_whole_word=switch).reasons
        assert any(reason in r for r in reasons), (switch, reasons)


def test_the_count_of_answer_terms_follows_the_same_rule():
    clue = "more salt for the sun"                # "or" inside more/for; "salt"; "sun"
    assert _check(clue, ["or", "salt", "sun"]).answer_terms == 3
    assert _check(clue, ["or", "salt", "sun"], short_terms_whole_word=True).answer_terms == 2


# --------------------------------------------------------------------------- #
# 3. Só o motor-alvo liga o interruptor                                         #
# --------------------------------------------------------------------------- #


def test_off_by_default_so_personas_and_relics_are_untouched():
    """Uma parte de um @ ('tit') é para apanhar dentro de 'title'."""
    assert _leaks("the title of the piece", ["tit"]) is True
    relic = RelicClueEngine(object(), "m", solver=False)
    assert "short_terms_whole_word" not in relic._guardrail_kwargs(None, 3)


def test_the_target_engine_turns_it_on_and_keeps_the_puzzle_rules():
    e = TargetClueEngine(object(), "m", search_guard=False, truth_judge=False, solver=False)
    assert e._guardrail_kwargs(None, 3) == {"puzzle_phase": True,
                                            "short_terms_whole_word": True}
    assert e._guardrail_kwargs(None, PUZZLE_CLUES + 1) == {
        "puzzle_phase": False, "short_terms_whole_word": True}


def _ctx(name: str) -> TargetClueContext:
    target = Target(chain="ethereum", contract="0x3b3ee1931dc30c1957379fac9aba94d1c48a5405",
                    token_id=41234, name=name, name_onchain=name, description="",
                    image="ipfs://QmImage", metadata_sha256="ab" * 32, epoch="e1")
    return TargetClueContext.from_target(target, image_description="a lighthouse")


def test_a_name_with_a_short_word_can_be_written_about():
    """O nome tem "Red". Antes, "tired", "hundred" e "shared" chumbavam todas
    as pistas que os usassem."""
    ctx = _ctx("Red Lantern")
    assert "red" in ctx.solution_terms
    e = engine(["a tired keeper shared a hundred nights with it"],
               guard_hits={"red lantern": {ITEM_ID}})
    d = e.next_clue(ctx, PUZZLE_CLUES + 2, ["x"] * (PUZZLE_CLUES + 1))
    assert d.text.startswith("a tired keeper") and e._rejections == {}


def test_and_the_short_word_itself_is_still_refused_by_the_engine():
    e = engine(["the red door at the end", "its reds never fade",
                "patience is a coin nobody spends"],
               guard_hits={"red lantern": {ITEM_ID}})
    d = e.next_clue(_ctx("Red Lantern"), PUZZLE_CLUES + 2, ["x"] * (PUZZLE_CLUES + 1))
    assert d.text == "patience is a coin nobody spends"
    assert e._rejections == {"answer term (1b)": 2}


def test_the_eight_function_words_stay_off_the_list():
    """A correcção mínima de 07/10 fica (Pedro, 09/10)."""
    assert _FUNCTION_WORDS == {"the", "an", "of", "and", "in", "on", "to", "at"}
    assert _ctx("The Red Lantern").solution_terms == ["lantern", "red"]

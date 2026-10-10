"""As palavras de um nome — UMA leitura, para o plano, as pistas e a guarda.

10/10. "nome-longo" era o maior filtro das duas colheitas (13 de 22 em
Ethereum a 09/10, 3 de 6 na Manifold a 10/10), e por uma razão que não era
do jogo: o plano contava palavras POR ESPAÇOS. "Portrait of a Lady" eram
quatro e não cabia; "Chasing the Doge" (a #17) cabia, e o "the" levava duas
das sete peças do puzzle. Ao seguir a contagem apareceu pior: seis sítios
contavam palavras de seis maneiras, e em "Self-Portrait at Dawn" a 3.ª
palavra do plano era "Dawn" e a da guarda estrutural era "at" — uma
afirmação VERDADEIRA sobre "Dawn" era recusada como falsa.

O que isto fixa (decisões do Pedro, 10/10):

  · UMA maneira de contar palavras, para o plano, as pistas e a guarda
    (content/name_words.py);
  · o plano dá peças só às palavras DE CONTEÚDO — no máximo 3;
  · as posições e a contagem total são as VERDADEIRAS: em "Portrait of a
    Lady", "Lady" é a 4.ª palavra e o nome tem 4;
  · o que não é conteúdo aparece a toda a gente na primeira pista da fase
    de revelação, numa linha fixa: "___ of a ___";
  · as palavras de função são as OITO de 07/10 e mais nenhuma: "for",
    "with", "by", "from" podem pesar no sentido de um nome;
  · o relatório parte o "nome-longo" por palavras de conteúdo e diz quantos
    nomes de 4+ palavras a leitura nova deixa entrar.

Todos os nomes aqui são SINTÉTICOS (ou de hunts já reveladas).
"""
from __future__ import annotations

import inspect
import random
import re
import string

import pytest
from test_probe import AR_META, Chain, _accepts, _Sampler, addr
from test_target_clues import FakeAnthropic, FakeTruthJudge
from test_target_prepare import S, World, _finder, _preparer

from finding_memeland import main
from finding_memeland.content.name_words import (
    BLANK,
    FUNCTION_WORDS,
    answer_terms,
    content_words,
    name_skeleton,
    name_words,
    word_at,
)
from finding_memeland.content.relic_clues import (
    MAX_NAME_WORDS,
    PUZZLE_ANGLES,
    PUZZLE_CLUES,
    PUZZLE_OBLIQUENESS,
    RelicClueContext,
    _solver_target_words,
    angle_for_unverifiable,
    content_facets,
    name_fits_plan,
    puzzle_clues_of,
    relic_guidance_for,
    relic_ramp_plan,
    relic_slot_for,
    stretched_obliqueness,
    target_ramp_plan,
)
from finding_memeland.target import clues as target_clues
from finding_memeland.target import dryrun
from finding_memeland.target.clues import (
    TargetClueContext,
    TargetClueEngine,
    build_target_user_message,
    small_words_for,
    structural_claim_errors,
    verify_claims,
)
from finding_memeland.target.dryrun import TargetWorld
from finding_memeland.target.prepare import Candidate, Larder, Tally
from finding_memeland.target.probe import ContractProbe, cause_of
from finding_memeland.target.selector import Target
from finding_memeland.target.templates import (
    TARGET_SMALL_WORDS_LINE,
    target_clue_followup,
)

LADY = "Portrait of a Lady"                       # 4 words, 2 of content
GARDEN = "The Garden of Earthly Delights"         # 5 words, 3 of content
PLAIN = "Quiet Lantern"                           # every word is content


def _ctx(name: str) -> RelicClueContext:
    return RelicClueContext(
        display_name=name, image_description="", lore="", backstory="",
        solution_terms=sorted(set(answer_terms(name))),
        clue_facet_plan=target_ramp_plan(name),
        angle_offset=sum(ord(c) for c in name) % len(PUZZLE_ANGLES))


def _target(name: str) -> Target:
    return Target(chain="ethereum", contract="0x" + "ab" * 20, token_id=7, name=name,
                  name_onchain=name, description="", image="ipfs://QmImage",
                  metadata_sha256="ab" * 32, epoch="e1")


def _pieces(name: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for facet, _obl in target_ramp_plan(name):
        out[facet] = out.get(facet, 0) + 1
    return out


# --------------------------------------------------------------------------- #
# 1. Uma leitura                                                                #
# --------------------------------------------------------------------------- #


def test_the_function_words_are_the_eight_and_no_others():
    assert FUNCTION_WORDS == {"the", "an", "of", "and", "in", "on", "to", "at"}
    assert target_clues._FUNCTION_WORDS is FUNCTION_WORDS            # one list, not two
    # the ones that can carry the meaning of a name stay content (Pedro, 10/10)
    for word in ("for", "with", "by", "from", "my", "no", "is"):
        (w,) = name_words(word)
        assert w.content, word


@pytest.mark.parametrize("name, words, content", [
    (LADY, ["Portrait", "of", "a", "Lady"], [1, 4]),
    (GARDEN, ["The", "Garden", "of", "Earthly", "Delights"], [2, 4, 5]),
    ("Chasing the Doge", ["Chasing", "the", "Doge"], [1, 3]),          # Hunt #17, public
    ("Waiting for the Sun", ["Waiting", "for", "the", "Sun"], [1, 2, 4]),   # "for" is content
    ("Salt & Harbor", ["Salt", "Harbor"], [1, 2]),                     # a sign is not a word
    ("Lantern No 7 Blue", ["Lantern", "No", "Blue"], [1, 2, 3]),       # nor is a number
    ("Self-Portrait at Dawn", ["Self-Portrait", "at", "Dawn"], [1, 3]),  # a hyphen splits nothing
    ("Rock'n'Roll Forever", ["Rock'n'Roll", "Forever"], [1, 2]),
    ("I Am a Cat", ["I", "Am", "a", "Cat"], [2, 4]),                   # one letter was never a term
    ("Vision in 3D", ["Vision", "in", "3D"], [1]),                     # a word, but no term in it
    (PLAIN, ["Quiet", "Lantern"], [1, 2]),
])
def test_a_name_reads_one_way(name, words, content):
    got = name_words(name)
    assert [w.text for w in got] == words
    assert [w.position for w in got] == list(range(1, len(words) + 1))     # never renumbered
    assert [w.position for w in got if w.content] == content
    assert [w.position for w in content_words(name)] == content
    assert all(word_at(name, w.position) == w for w in got)
    assert word_at(name, 0) is None and word_at(name, len(words) + 1) is None


def test_a_word_is_content_exactly_when_it_gives_a_term_to_the_never_write_list():
    for name in (LADY, GARDEN, "Self-Portrait at Dawn", "Vision in 3D", "I Am a Cat",
                 "Rock'n'Roll Forever", "Waiting for the Sun"):
        terms = set(answer_terms(name))
        for w in name_words(name):
            gives = any(len(r) > 1 and r in terms for r in w.runs)
            assert w.content is gives, (name, w.text)


def test_a_name_made_only_of_function_words_keeps_them_all():
    assert answer_terms("The And Of") == ["the", "and", "of"]
    assert [w.content for w in name_words("The And Of")] == [True, True, True]
    assert name_skeleton("The And Of") is None


def _old_terms(name: str) -> list[str]:
    """What TargetClueContext.from_target computed until 10/10, verbatim."""
    named = [w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ]{2,}", name)]
    return [t for t in named if t not in FUNCTION_WORDS] or named


def test_the_never_write_terms_are_exactly_what_they_were():
    rng = random.Random(11)
    alphabet = string.ascii_letters + "éàçÖ" + "  --''&7.#"
    small = sorted(FUNCTION_WORDS) + ["a", "I", "for", "No", "3D"]
    for _ in range(4000):
        tokens = [rng.choice(small) if rng.random() < 0.3
                  else "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 9)))
                  for _ in range(rng.randint(1, 7))]
        name = " ".join(tokens)
        assert answer_terms(name) == _old_terms(name), name
    for name in (LADY, GARDEN, "Chasing the Doge", "Self-Portrait at Dawn", "The And Of", ""):
        assert answer_terms(name) == _old_terms(name)
        ctx = TargetClueContext.from_target(_target(name or "Xx Yy"), image_description="x")
        assert ctx.solution_terms == sorted(set(_old_terms(name or "Xx Yy")))


@pytest.mark.parametrize("name, skeleton", [
    (LADY, "___ of a ___"),
    (GARDEN, "The ___ of ___ ___"),
    ("Chasing the Doge", "___ the ___"),
    ("Salt & Harbor", "___ & ___"),                 # what is not a word is shown as written
    ("Lantern No 7 Blue", "___ ___ 7 ___"),
    ("Vision in 3D", "___ in 3D"),
    ("I Am a Cat", "I ___ a ___"),
    (PLAIN, None), ("Lantern", None), ("", None),
])
def test_the_skeleton_blanks_the_content_and_shows_the_rest(name, skeleton):
    assert name_skeleton(name) == skeleton
    if skeleton:
        # every blank is the same width: a word's length is a count, and
        # counts are not given away
        assert skeleton.count(BLANK) == len(content_words(name))
        for term in answer_terms(name):
            assert term not in skeleton.lower()


# --------------------------------------------------------------------------- #
# 2. O plano: peças só para o conteúdo, posições verdadeiras                    #
# --------------------------------------------------------------------------- #


def test_the_facets_carry_the_true_positions():
    assert content_facets(LADY) == ["name_word_1", "name_word_4"]
    assert content_facets(GARDEN) == ["name_word_2", "name_word_4", "name_word_5"]
    assert content_facets(PLAIN) == ["name_word_1", "name_word_2"]
    assert content_facets("7 & 9") == ["name_word_1"]        # never a plan on nothing


@pytest.mark.parametrize("name, fits", [
    (LADY, True),                                   # 4 by spaces, 2 of content
    (GARDEN, True),                                 # 5 by spaces, 3 of content
    ("A Day in the Life of an Artist", True),       # 8 by spaces, 3 of content
    ("Waiting for the Sun", True),                  # "for" counts: 3
    ("Song for a Lost Friend", False),              # "for" counts: 4
    ("Quiet Lantern Above Water", False),
    ("The Quiet Lantern Above the Dark Water", False),
])
def test_the_plan_holds_three_content_words(name, fits):
    assert MAX_NAME_WORDS == 3
    assert name_fits_plan(name) is fits
    if fits:
        plan = target_ramp_plan(name)
        # two pieces a content word and two for the artwork (test_puzzle_length)
        assert len(plan) == 2 * len(content_facets(name)) + 2
        assert [obl for _f, obl in plan] == list(stretched_obliqueness(len(plan)))
        assert len(relic_ramp_plan(name)) == PUZZLE_CLUES        # a relic keeps its seven
    else:
        with pytest.raises(ValueError) as e:
            target_ramp_plan(name)
        assert "content words" in str(e.value)
        assert all(w not in str(e.value) for w in name.split() if len(w) > 3)


def test_a_function_word_takes_no_piece_and_the_art_gets_its_two_back():
    """Hunt #17, pública: "Chasing the Doge". Pelo plano antigo o "the"
    levava 2 das 7 peças e a arte ficava com 1."""
    doge = _pieces("Chasing the Doge")
    assert doge == {"name_word_1": 2, "name_word_3": 2, "image": 2}    # nothing for "the"
    for name in (LADY, "Salt & Harbor", "Self-Portrait at Dawn"):
        pieces = _pieces(name)
        assert pieces["image"] == 2
        assert set(pieces) - {"image"} == set(content_facets(name))
        assert all(n >= 2 for f, n in pieces.items() if f != "image")
    three = _pieces(GARDEN)
    assert three == {"name_word_2": 2, "name_word_4": 2, "name_word_5": 2, "image": 2}


def _old_plan(name: str) -> list:
    """The plan as it was built until 10/10 — one facet per space-separated
    token — for names where the two readings must agree."""
    words = [f"name_word_{i + 1}" for i in range(len(name.split()))] or ["name_word_1"]
    rng = random.Random(name)
    n_art = max(0, min(2, PUZZLE_CLUES - 2 * len(words)))
    slots = [w for w in words for _ in range(2)]
    while len(slots) < PUZZLE_CLUES - n_art:
        slots.append(rng.choice(words))
    slots += ["image"] * n_art
    for _ in range(100):
        rng.shuffle(slots)
        if slots[0] != "image" and not any(
                a == b == "image" for a, b in zip(slots, slots[1:], strict=False)):
            break
    return [(facet, PUZZLE_OBLIQUENESS[i]) for i, facet in enumerate(slots)]


def test_a_relic_with_no_small_words_keeps_the_plan_it_had():
    """As relics ficam nas 7 (Pedro, 10/10): um nome cujas palavras são todas
    de conteúdo sai com o MESMO plano de sempre, peça por peça."""
    rng = random.Random(5)
    for _ in range(600):
        n = rng.randint(1, 3)
        name = " ".join("".join(rng.choice(string.ascii_lowercase)
                                for _ in range(rng.randint(4, 9))).capitalize()
                        for _ in range(n))
        if any(w.lower() in FUNCTION_WORDS for w in name.split()):
            continue
        assert relic_ramp_plan(name) == _old_plan(name), name
    for name in ("Whispering Harbor", "Summer Cyclone", "Akari Haruto", "Ancient Future"):
        assert relic_ramp_plan(name) == _old_plan(name)


def test_the_plan_is_the_same_every_time_it_is_built():
    for name in (LADY, GARDEN, "Salt & Harbor"):
        for plan in (relic_ramp_plan, target_ramp_plan):
            assert plan(name) == plan(name)
            assert plan(name)[0][0] != "image"                   # clue 1 is a name piece


def test_every_name_piece_gets_an_angle_and_no_word_repeats_one():
    for name in (LADY, GARDEN, "Chasing the Doge", "A Day in the Life of an Artist"):
        ctx = _ctx(name)
        used: dict[str, list[str]] = {}
        for i in range(1, puzzle_clues_of(ctx) + 1):
            facet, _ = relic_slot_for(i, ctx)
            if facet == "image":
                assert angle_for_unverifiable(i, ctx) is None
                continue
            used.setdefault(facet, []).append(angle_for_unverifiable(i, ctx))
        assert set(used) == set(content_facets(name))
        assert all(len(set(v)) == len(v) for v in used.values()), name


# --------------------------------------------------------------------------- #
# 3. O que o escritor lê                                                        #
# --------------------------------------------------------------------------- #


def test_the_guidance_names_the_word_at_its_true_position():
    ctx = _ctx(LADY)
    text = relic_guidance_for("name_word_4", ctx, 3)
    assert text.startswith("the 4th word of the relic's NAME (the word 'Lady')")
    assert relic_guidance_for("name_word_1", ctx, 9).startswith(
        "the 1st word of the relic's NAME (the word 'Portrait')")
    # a number in the name does not shift the count
    assert relic_guidance_for("name_word_3", _ctx("Lantern No 7 Blue"), 2).startswith(
        "the 3rd word of the relic's NAME (the word 'Blue')")
    # one word only — small words do not make it "the only word" of a longer name
    assert relic_guidance_for("name_word_1", _ctx("Lantern"), 2).startswith("the only word")
    assert relic_guidance_for("name_word_2", _ctx("The Doge"), 2).startswith("the 2nd word")


def test_the_writer_is_told_the_true_count_and_which_words_are_not_the_puzzle():
    ctx = TargetClueContext.from_target(_target(LADY), image_description="a woman by a window")
    msg = build_target_user_message(ctx, 3, ["one", "two"])
    assert "- name (4 words): Portrait of a Lady\n" in msg
    assert "Terms to NEVER write: ['lady', 'portrait']\n" in msg
    assert ("SMALL WORDS: word 2 ('of'), word 3 ('a') — part of the name, "
            "NOT part of the puzzle") in msg
    assert "Word numbers count EVERY word of the name" in msg
    facet = relic_slot_for(3, ctx)[0]
    nth = "1st" if facet.endswith("1") else "4th"
    assert f"FACET for this clue: {facet} — the {nth} word" in msg


def test_a_name_with_no_small_words_reads_exactly_as_before():
    ctx = TargetClueContext.from_target(_target(PLAIN), image_description="a lamp")
    msg = build_target_user_message(ctx, 2, ["one"])
    assert "SMALL WORDS" not in msg
    assert "- name (2 words): Quiet Lantern\n" in msg
    assert "Terms to NEVER write: ['lantern', 'quiet']\n\nThis is clue #2." in msg


def test_a_sign_or_a_number_is_not_counted_as_a_word_for_the_writer():
    ctx = TargetClueContext.from_target(_target("Salt & Harbor"), image_description="x")
    assert "- name (2 words): Salt & Harbor\n" in build_target_user_message(ctx, 1, [])


# --------------------------------------------------------------------------- #
# 4. A guarda estrutural lê as mesmas palavras                                  #
# --------------------------------------------------------------------------- #


def test_a_true_claim_about_the_word_the_writer_was_given_is_accepted():
    """O defeito: o plano dizia "a 3.ª palavra é Dawn", a guarda via "at"."""
    name = "Self-Portrait at Dawn"
    assert relic_guidance_for("name_word_3", _ctx(name), 2).startswith(
        "the 3rd word of the relic's NAME (the word 'Dawn')")
    assert verify_claims([{"type": "starts", "word": 3, "value": "d"}], name) == []
    assert verify_claims([{"type": "ends", "word": 3, "value": "n"}], name) == []
    assert structural_claim_errors("the third word starts with D", name) == []
    # …and a false one about it is still refused
    (err,) = verify_claims([{"type": "starts", "word": 3, "value": "a"}], name)
    assert "FALSE" in err and "dawn" in err


def test_the_positions_are_the_true_ones_function_words_and_all():
    assert verify_claims([{"type": "starts", "word": 4, "value": "l"}], LADY) == []
    (err,) = verify_claims([{"type": "starts", "word": 2, "value": "l"}], LADY)
    assert "'of' starts with 'o'" in err
    assert structural_claim_errors("the fourth word starts with L", LADY) == []
    assert structural_claim_errors("the last word starts with L", LADY) == []
    assert structural_claim_errors("the second word starts with L", LADY) != []


def test_the_word_count_a_clue_may_state_is_the_total():
    assert verify_claims([{"type": "word_count", "word": 0, "value": "4"}], LADY) == []
    assert verify_claims([{"type": "word_count", "word": 0, "value": "2"}], LADY) != []
    assert structural_claim_errors("four words, and the last one is short", LADY) == []
    assert structural_claim_errors("two words, nothing more", LADY) != []
    # a sign is not a word: the writer is told 2, and 2 is what the guard accepts
    assert verify_claims([{"type": "word_count", "word": 0, "value": "2"}], "Salt & Harbor") == []
    assert verify_claims([{"type": "word_count", "word": 0, "value": "3"}], "Salt & Harbor") != []


def test_a_sign_between_words_no_longer_shifts_the_claim():
    assert verify_claims([{"type": "starts", "word": 2, "value": "h"}], "Salt & Harbor") == []
    (err,) = verify_claims([{"type": "starts", "word": 3, "value": "h"}], "Salt & Harbor")
    assert "'word' must be 1..2" in err


def test_a_hyphenated_word_is_checked_as_the_one_word_it_is():
    name = "Self-Portrait at Dawn"
    assert verify_claims([{"type": "starts", "word": 1, "value": "s"}], name) == []
    assert verify_claims([{"type": "ends", "word": 1, "value": "t"}], name) == []
    assert verify_claims([{"type": "contains", "word": 1, "value": "self"}], name) == []
    assert verify_claims([{"type": "contains", "word": 1, "value": "port"}], name) == []
    # the word itself is not "a shorter word inside it"
    assert verify_claims([{"type": "contains", "word": 3, "value": "dawn"}], name) != []


def test_a_doubled_letter_is_two_letters_side_by_side_not_across_a_hyphen():
    assert verify_claims([{"type": "double_letter", "word": 1}], "Hot-Tub Night") != []
    assert verify_claims([{"type": "double_letter", "word": 1}], "Moon-Light Night") == []
    assert structural_claim_errors("the first word hides a doubled letter", "Hot-Tub Night") != []


def test_an_apostrophe_is_not_a_letter():
    name = "'Round Midnight"
    assert verify_claims([{"type": "starts", "word": 1, "value": "r"}], name) == []
    assert structural_claim_errors("the first word has five letters", name) == []


def test_plain_names_are_judged_exactly_as_before():
    name = "Ancient Future"
    assert structural_claim_errors("six letters, nothing fancy", name) == []      # FUTURE has six
    assert structural_claim_errors("word two is a compound of two", name) != []   # unverifiable
    assert verify_claims([{"type": "starts", "word": 2, "value": "f"},
                          {"type": "ends", "word": 1, "value": "t"},
                          {"type": "contains", "word": 1, "value": "cie"}], name) == []
    assert verify_claims([{"type": "contains", "word": 1, "value": "ant"}], name) != []
    assert verify_claims([{"type": "starts", "word": 1, "value": "f"}], name) != []
    assert verify_claims([{"type": "starts", "word": 0, "value": "a"}], name) == []   # whole name
    # a DECLARED double letter is inside a word; loose prose about the whole
    # name keeps the benefit of the doubt it always had (the two g's meet)
    assert verify_claims([{"type": "double_letter", "word": 0}], "Big Game") != []
    assert structural_claim_errors("there is a doubled letter in it", "Big Game") == []
    assert verify_claims([{"type": "palindrome", "word": 0}], "Level Noon") == []
    assert verify_claims([{"type": "no_vowels", "word": 1}], "Rhythm Sky") == []


def _judged(name: str, clue_index: int):
    """One clue through the real engine, with a judge that records what it
    was told the clue is about."""
    judge = FakeTruthJudge()
    engine = TargetClueEngine(FakeAnthropic(["a clue that names nothing"] * 12), "model",
                              search_guard=False, truth_judge=judge, solver=False)
    ctx = TargetClueContext.from_target(_target(name), image_description="a lighthouse")
    engine.next_clue(ctx, clue_index, ["earlier"] * (clue_index - 1))
    return ctx, judge


@pytest.mark.parametrize("name", [LADY, "Salt & Harbor", "Lantern No 7 Blue",
                                  "Self-Portrait at Dawn", GARDEN])
def test_the_consistency_judge_is_told_the_word_the_piece_is_about(name):
    """Pelos espaços, a 2.ª palavra de "Salt & Harbor" era o "&": o juiz lia
    a pista contra um sinal e dava-a como falsa."""
    puzzle = puzzle_clues_of(TargetClueContext.from_target(_target(name),
                                                           image_description="x"))
    for i in range(2, puzzle + 3):                  # the puzzle, and into the reveal
        ctx, judge = _judged(name, i)
        facet = relic_slot_for(i, ctx)[0]
        told = judge.seen[-1][2]                    # (clue, name, word, artwork)
        if facet == "image":
            assert told is None
        else:
            want = word_at(name, int(facet.rsplit("_", 1)[1]))
            assert want.content and told == want.text, (name, i)


@pytest.mark.parametrize("name, ordinal, word", [
    (LADY, "4th", "Lady"), ("Salt & Harbor", "second", "Harbor"),
    ("Lantern No 7", "second", "No"), (PLAIN, "second", "Lantern"),
])
def test_the_judge_s_canary_is_about_the_last_word_at_its_true_position(name, ordinal, word):
    _ctx_, judge = _judged(name, 1)
    canaries = [c for c in judge.seen if "of the name is" in c[0]]
    assert len(canaries) == 2
    assert all(c[0].startswith(f"the {ordinal} word of the name is") for c in canaries)
    assert all(c[2] == word for c in canaries)


# --------------------------------------------------------------------------- #
# 5. O adivinho cego                                                            #
# --------------------------------------------------------------------------- #


def test_the_solver_s_target_is_the_word_at_the_true_position():
    ctx = _ctx(LADY)
    assert _solver_target_words(ctx, "name_word_4")[0] == "lady"
    assert _solver_target_words(ctx, "name_word_1")[0] == "portrait"
    hyphen = _ctx("Self-Portrait at Dawn")
    assert _solver_target_words(hyphen, "name_word_1")[:2] == ["self", "portrait"]
    assert _solver_target_words(hyphen, "name_word_3")[0] == "dawn"


def test_a_small_word_is_never_what_the_solver_is_measured_against():
    """Antes, numa peça de arte, "a" e "the" eram alvos: qualquer palpite
    que os contivesse era um acerto, e a pista era recusada."""
    for name in (LADY, GARDEN, "Chasing the Doge"):
        targets = _solver_target_words(_ctx(name), "image")
        assert not set(targets) & (FUNCTION_WORDS | {"a"}), name
        assert set(answer_terms(name)) <= set(targets)


# --------------------------------------------------------------------------- #
# 6. A fase de revelação: só o conteúdo, e a linha fixa uma vez                 #
# --------------------------------------------------------------------------- #


def test_the_reveal_hands_over_the_content_words_only():
    ctx = _ctx(LADY)
    first = puzzle_clues_of(ctx) + 1
    facets = [relic_slot_for(i, ctx)[0] for i in range(first, first + 11)]
    assert set(facets) == {"name_word_1", "name_word_4", "image"}
    assert facets.count("image") == 1


def test_the_small_words_line_goes_out_with_the_first_reveal_clue_and_only_then():
    ctx = _ctx(LADY)
    first = puzzle_clues_of(ctx) + 1                 # 2 content words: the 7th clue
    assert first == 7 and small_words_for(ctx, first) == "___ of a ___"
    assert [i for i in range(1, 30) if small_words_for(ctx, i)] == [first]
    assert all(small_words_for(_ctx(PLAIN), i) is None for i in range(1, 30))


def test_the_post_carries_the_line_between_the_clue_and_the_jeer():
    post = target_clue_followup(8, "a clue", "a jeer", skeleton="___ of a ___")
    assert post == ("8th Clue:\n\na clue\n\n"
                    "the small words of the name are free: ___ of a ___\n\na jeer")
    assert TARGET_SMALL_WORDS_LINE.format(skeleton="x") in target_clue_followup(
        8, "a clue", "", skeleton="x")
    # no skeleton, no line — every other clue reads exactly as before
    assert target_clue_followup(8, "a clue", "a jeer") == "8th Clue:\n\na clue\n\na jeer"
    assert target_clue_followup(3, "a clue", "a jeer", skeleton=None).count("\n\n") == 3


def _clue_posts(monkeypatch, base: str, clues: int = 10) -> list[str]:
    """Uma hunt inteira, offline, com o Orchestrator verdadeiro (o mundo do
    dry-run, com outro nome no pool): a Clue 1 e mais `clues - 1` pistas,
    cada uma devida de imediato."""
    monkeypatch.setattr(dryrun, "POOL_NAME", base)
    w = TargetWorld()
    hunt = w.launch()
    assert hunt.ctx.display_name == base
    w.orch._clue_due_fn = lambda now: now
    index = 1
    for _ in range(clues - 1):
        w.rig.clock.sleep(60)
        index, _due = w.orch._maybe_post_clue(hunt, index, w.rig.clock.now())
    assert index == clues
    return [p for p in w.posts() if re.match(r"\d+(st|nd|rd|th) Clue:", p, re.IGNORECASE)]


def test_a_whole_hunt_shows_the_small_words_once_at_its_first_reveal_clue(monkeypatch):
    """Dois de conteúdo → seis pistas de puzzle → a linha sai na 7.ª."""
    posts = _clue_posts(monkeypatch, "Lantern of the Harbor")
    line = "the small words of the name are free: ___ of the ___"
    with_line = [p for p in posts if "small words" in p]
    assert len(with_line) == 1 and with_line[0].startswith("7th Clue:")
    assert line in with_line[0].split("\n\n")
    assert not any(term in " ".join(posts).lower() for term in ("lantern", "harbor"))


def test_a_hunt_on_a_plain_name_never_shows_the_line(monkeypatch):
    assert not any("small words" in p for p in _clue_posts(monkeypatch, "Whispering Harbor"))


# --------------------------------------------------------------------------- #
# 7. A despensa: o que cabe, o que não cabe, e a medição                        #
# --------------------------------------------------------------------------- #


def _deposit(name: str, n: int = 1):
    world = World(default_name=name)
    larder = Larder()
    rep = _finder(world).deposit(larder, [f"ethereum:{S.contract}:{i}" for i in range(5, 5 + n)])
    return rep, larder, world


def test_a_long_name_with_three_content_words_goes_in_and_is_counted():
    rep, larder, _world = _deposit(GARDEN)
    assert rep.added == 1 and larder.size() == 1
    assert rep.rejected.long_name == 0 and rep.rejected.long_fits == 1
    out = rep.render()
    assert out == "1 guardado(s) de 1 · nomes de 4+ palavras que cabem pelo conteúdo: 1"
    assert "nome-longo" not in out


def test_a_name_that_always_fitted_is_not_counted_as_let_in():
    rep, _larder, _world = _deposit("Chasing the Doge")          # 3 by spaces: it fitted before
    assert rep.added == 1 and rep.rejected.long_fits == 0
    assert rep.render() == "1 guardado(s) de 1"


@pytest.mark.parametrize("name, kind", [
    ("Quiet Lantern Above Water", "conteúdo-4"),
    ("Song for a Lost Friend", "conteúdo-4"),
    ("The Quiet Lantern Above the Dark Water", "conteúdo-5+"),
    ("One Two Three Four Five Six Seven", "conteúdo-5+"),
])
def test_a_refused_name_says_how_many_content_words_it_has(name, kind):
    rep, larder, world = _deposit(name)
    assert larder.size() == 0 and rep.rejected.long_name == 1
    assert rep.rejected.long_name_kinds == {kind: 1} and rep.rejected.long_fits == 0
    assert f"nome-longo 1 ({kind} 1)" in rep.render()
    assert world.probes == [] and world.paid_calls == 0          # nothing spent on it
    assert all(w not in rep.render() for w in name.split() if len(w) > 3)


def test_the_report_adds_up_and_keeps_the_measurement_apart():
    world = World(names={5: GARDEN, 6: "Quiet Lantern Above Water", 7: LADY,
                         8: "One Two Three Four Five Six", 9: PLAIN})
    larder = Larder()
    rep = _finder(world).deposit(larder, [f"ethereum:{S.contract}:{i}" for i in range(5, 10)])
    assert rep.added == 3 and rep.rejected.long_name == 2 and rep.rejected.long_fits == 2
    assert rep.render() == (
        "3 guardado(s) de 5 · nome-longo 2 (conteúdo-4 1, conteúdo-5+ 1) · "
        "nomes de 4+ palavras que cabem pelo conteúdo: 2")
    assert rep.rejected.causes() == "nome-longo 2 (conteúdo-4 1, conteúdo-5+ 1)"   # causes only


def test_the_fill_report_says_it_too():
    world = World(default_name=LADY)
    larder = Larder()
    tally = _finder(world).fill(larder, want=2, max_draws=10)
    assert larder.size() == 2 and tally.long_fits == 2
    assert tally.render().endswith(" · nomes de 4+ palavras que cabem pelo conteúdo: 2")
    assert Tally().notes() == "" and " · " not in Tally().render()


def _probe_one(name: str):
    contract = addr(1)
    chain = Chain({contract: {"tokens": {1: AR_META}}},
                  arweave={AR_META: {"name": name, "image": "ar://" + "Z" * 43}})
    return ContractProbe(
        source="manifold", chain="base", sampler=_Sampler([contract]),
        eth_call=chain.eth_call, token_uri=chain.token_uri,
        read_token=chain.read_token, arweave_json=chain.arweave_json,
        finder=_finder(World(), accepts_uri=_accepts), rng=random.Random(3)).run(1)


def test_the_probe_counts_a_long_name_that_now_fits():
    rep = _probe_one(GARDEN)
    assert rep.with_arweave.passed == 1 and rep.long_fits == 1
    assert rep.render().splitlines()[-1] == "nomes de 4+ palavras que cabem pelo conteúdo: 1"
    assert _probe_one(PLAIN).long_fits == 0


def test_the_probe_names_how_many_content_words_a_refused_name_has():
    rep = _probe_one("Quiet Lantern Above Water")
    assert rep.with_arweave.causes == {("nome-longo", "conteúdo-4"): 1}
    assert "com Arweave: passariam 0 de 1 — nome-longo 1 (conteúdo-4 1)" in rep.render()
    assert rep.long_fits == 0
    tally = Tally()
    tally.long_name += 1
    tally.long_name_kinds["conteúdo-5+"] = 1
    assert cause_of(tally) == ("nome-longo", "conteúdo-5+")


def test_prepare_seals_a_larder_target_whose_name_has_small_words():
    world = World(names={5: LADY})
    larder = Larder()
    larder.add(Candidate(chain="ethereum", contract=S.contract, token_id=5, name=LADY,
                         name_onchain=LADY, description="d", image="ipfs://img5",
                         token_uri="ipfs://bafy" + "a" * 40 + "5", artist="",
                         metadata={"name": LADY}))
    prepared, left = _preparer(world, _finder(world)).prepare(larder)
    assert prepared.target.name == LADY and left.size() == 0
    ctx = TargetClueContext.from_target(prepared.target, image_description="x")
    assert [f for f, _ in ctx.clue_facet_plan if f != "image"].count("name_word_2") == 0


# --------------------------------------------------------------------------- #
# 8. Cosmético: a linha de progresso diz o comando que está a correr            #
# --------------------------------------------------------------------------- #


def test_progress_lines_carry_the_label_of_the_running_job():
    src = inspect.getsource(main)
    assert "[snapshot] {line}" not in src                    # it said so for every command
    assert "target_flag.get('label')" in src
    assert 'target_flag["label"] = label' in src

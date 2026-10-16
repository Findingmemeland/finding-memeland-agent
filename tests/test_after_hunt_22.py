"""Depois da hunt #22 (16/10) — nenhuma pista de letras nas pistas 1–3.

A pista 1 da #22 foi um facto sobre as letras do nome. Medido pelo Pedro:
à 1 h, 28% de interacção e 24 expands, contra 43% e 78 na #20, que abriu
com uma pista de significado. A regra de julho (do motor das personas) põe
as letras nas pistas 4–5; o motor de alvos nunca a teve.

Três caminhos, lidos no código, e o que os fecha:
  1. O ÂNGULO: o baralho dava STRUCTURE à pista 1 em cerca de um puzzle em
     três (nomes sintéticos). Agora nunca antes da pista 4, nem como último
     recurso — STRUCTURE_EARLIEST, ao lado do RELATION_EARLIEST.
  2. A DECLARAÇÃO: qualquer ângulo podia afirmar um facto de letras
     declarando-o, e um verdadeiro passava tudo. Nas pistas 1–3, qualquer
     afirmação declarada e qualquer frase sobre letras é recusada
     ("early letters"), antes da guarda estrutural. A contagem de palavras
     também (D1).
  3. O PROMPT: uma linha nas pistas 1–3, e a das palavras enumeráveis deixa
     de oferecer "structure" nessas pistas.

As contagens de letras, sílabas, vogais e consoantes (D2) já eram recusadas
em todas as pistas pela regra 1c das guardas de texto (post-mortem da
Penelope): fica aqui provado, sem código novo.
"""
from __future__ import annotations

import logging
import random

import pytest
from test_target_clues import ctx, engine

from finding_memeland.content import relic_clues as rc
from finding_memeland.content.clue_engine import ClueDraft
from finding_memeland.content.relic_clues import (
    PUZZLE_ANGLES,
    RELATION_EARLIEST,
    STRUCTURE_EARLIEST,
    RelicClueContext,
    angle_for,
    angle_for_unverifiable,
    puzzle_clues_of,
    relic_ramp_plan,
    relic_slot_for,
)
from finding_memeland.target.clues import (
    CLAIM_TYPES,
    FIRST_THREE_CLUES_RULE,
    TargetClueContext,
    build_target_user_message,
    early_letter_errors,
)
from finding_memeland.target.selector import Target

_SYL = [c + v for c in "bdfgklmnprstvz" for v in "aeiou"]


def synthetic_names(n_words: int, k: int = 300) -> list[str]:
    """Nomes inventados (nunca um alvo): pseudo-palavras de 2–3 sílabas."""
    rng = random.Random(f"hunt22|{n_words}")
    out: set[str] = set()
    while len(out) < k:
        words = ["".join(rng.choice(_SYL) for _ in range(rng.choice((2, 3))))
                 for _ in range(n_words)]
        if len(set(words)) == n_words:
            out.add(" ".join(w.capitalize() for w in words))
    return sorted(out)


def target_ctx(name: str) -> TargetClueContext:
    t = Target(chain="ethereum", contract="0x" + "1" * 40, token_id=1, name=name,
               name_onchain=name, description="", image="ipfs://x",
               metadata_sha256="ab" * 32, epoch="e1")
    return TargetClueContext.from_target(t, image_description="a picture")


def label(angle) -> str:
    return (angle or "-").split(":")[0]


def draft(text="patience is a coin nobody spends", claims=None):
    return ClueDraft(text=text, claims=list(claims or []))


def assigned(clue_index):
    """O rótulo que o escritor obediente declara: o ângulo atribuído à
    pista, ou nada numa peça de arte."""
    a = angle_for_unverifiable(clue_index, ctx())
    return label(a) if a else None


# --------------------------------------------------------------------------- #
# 1. O ângulo                                                                  #
# --------------------------------------------------------------------------- #


def test_the_letters_wait_for_clue_four_and_relation_stays_at_two():
    """Os textos do escritor dizem "first three" e "clue 4": se o número
    mudar, os textos mudam com ele. A relação fica como estava (09/09)."""
    assert STRUCTURE_EARLIEST == 4
    assert RELATION_EARLIEST == 2
    assert "Letters may come from clue 4 on." in FIRST_THREE_CLUES_RULE


@pytest.mark.parametrize("n_words", [1, 2, 3])
def test_no_target_puzzle_hands_structure_to_clues_one_to_three(n_words):
    for name in synthetic_names(n_words):
        c = target_ctx(name)
        early = [label(angle_for_unverifiable(i, c)) for i in range(1, STRUCTURE_EARLIEST)]
        assert "STRUCTURE" not in early, (name, early)


@pytest.mark.parametrize("n_words, at_least", [(2, 0.5), (3, 0.6)])
def test_the_letters_are_moved_not_removed(n_words, at_least):
    """Com duas e três palavras, a STRUCTURE continua no puzzle — da pista 4
    em diante. Medido a 16/10 com 2000 nomes sintéticos: 61% e 77%."""
    names = synthetic_names(n_words)
    with_letters = 0
    for name in names:
        c = target_ctx(name)
        n = puzzle_clues_of(c)
        at = [i for i in range(1, n + 1) if label(angle_for_unverifiable(i, c)) == "STRUCTURE"]
        assert all(i >= STRUCTURE_EARLIEST for i in at), (name, at)
        with_letters += bool(at)
    assert with_letters / len(names) >= at_least


def test_a_one_word_target_has_no_letter_piece_in_its_puzzle():
    """A consequência, à vista: com uma palavra o puzzle é nome·arte·nome·arte
    (a única ordem legal), as duas peças do nome são as pistas 1 e 3, e a
    STRUCTURE já não cabe. As letras, se vierem, vêm no reveal (pista 5+)."""
    for name in synthetic_names(1, k=100):
        c = target_ctx(name)
        assert [f.startswith("name_word") for f, _o in c.clue_facet_plan] == \
            [True, False, True, False]
        assert "STRUCTURE" not in [label(angle_for_unverifiable(i, c)) for i in range(1, 5)]


@pytest.mark.parametrize("n_words", [1, 2, 3])
def test_relic_hunts_follow_the_same_rule(n_words):
    """A função é partilhada: as relics também nunca têm letras antes da
    pista 4 — com e sem a âncora."""
    for name in synthetic_names(n_words, k=150):
        c = RelicClueContext(display_name=name, image_description="", lore="",
                             backstory="", clue_facet_plan=relic_ramp_plan(name),
                             angle_offset=sum(ord(ch) for ch in name) % len(PUZZLE_ANGLES))
        for pick in (angle_for, angle_for_unverifiable):
            early = [label(pick(i, c)) for i in range(1, STRUCTURE_EARLIEST)]
            assert "STRUCTURE" not in early, (name, pick.__name__, early)


def test_the_last_resort_never_hands_out_the_letters_early():
    """Três peças da mesma palavra antes da pista 4, sozinha: na pista 3 os
    ângulos livres acabam e vale o último recurso — que antes apanhava o
    primeiro ângulo não usado na palavra, STRUCTURE incluída."""
    plan = [("name_word_1", 0.9)] * 3 + [("image", 0.8)]
    exercised = 0
    for name in synthetic_names(1, k=60):
        c = RelicClueContext(display_name=name, image_description="", lore="",
                             backstory="", clue_facet_plan=plan)
        seq = [label(a) for a in rc._angle_sequence(c, allow_anchor=False)]
        exercised += seq.index("STRUCTURE") < seq.index("RELATION")
        got = [label(angle_for_unverifiable(i, c)) for i in (1, 2, 3)]
        assert "STRUCTURE" not in got, (name, got)
        assert got[2] == "RELATION", (name, got)
    assert exercised        # o caso em que a regra antiga dava STRUCTURE existe


# --------------------------------------------------------------------------- #
# 2. A guarda                                                                  #
# --------------------------------------------------------------------------- #

_LETTER_TALK = [
    "the second word of the name ends with a consonant",      # a forma da #22
    "the first word starts with the letter S",
    "its first letter is a consonant",
    "word two carries a double letter",
    "the name is a palindrome",
    "the second word has no vowels",
    "the first word contains 'alt'",
    "a two-word name, both halves older than the medium",      # D1
    "word one hides a prefix",
]


@pytest.mark.parametrize("text", _LETTER_TALK)
def test_letter_talk_is_refused_on_clues_one_to_three_only(text):
    for i in range(1, STRUCTURE_EARLIEST):
        assert early_letter_errors(draft(text), i), (text, i)
    assert early_letter_errors(draft(text), STRUCTURE_EARLIEST) == []


@pytest.mark.parametrize("claim_type", CLAIM_TYPES)
def test_any_declared_claim_is_refused_on_clues_one_to_three(claim_type):
    """Todos os tipos, a contagem de palavras incluída (D1) — mesmo com a
    prosa limpa."""
    d = draft(claims=[{"type": claim_type, "word": 1, "value": "s"}])
    for i in range(1, STRUCTURE_EARLIEST):
        assert early_letter_errors(d, i), (claim_type, i)
    assert early_letter_errors(d, STRUCTURE_EARLIEST) == []


def test_a_clue_about_something_else_passes():
    for i in range(1, STRUCTURE_EARLIEST):
        assert early_letter_errors(draft(), i) == []


def test_the_feedback_is_the_approved_text():
    [fb] = early_letter_errors(draft("its first letter is a consonant"), 2)
    assert fb == ("clue #2 is one of the first three: it says NOTHING about the letters, "
                  "the spelling or any count of the name (word count included) — that "
                  'starts at clue 4. Rewrite from the assigned angle with "claims": []')


def _true_letter_fact(clue_index):
    """Um facto VERDADEIRO sobre as letras de "Salt Harbor", declarado e com
    o ângulo atribuído — o que passava em todas as guardas."""
    return {"clue": "the second word of the name ends with a consonant", "taunt": "",
            "angle": assigned(clue_index), "image_aspect": None,
            "claims": [{"type": "ends", "word": 2, "value": "consonant"}]}


def test_clue_one_with_a_true_letter_fact_is_refused_and_rewritten(caplog):
    e = engine([_true_letter_fact(1), "patience is a coin nobody spends"])
    with caplog.at_level(logging.WARNING):
        d = e.next_clue(ctx(), 1, [])
    assert d.text == "patience is a coin nobody spends"
    assert e.last_attempt_report == (2, {"early letters": 1})
    feedback = e._client.calls[1]["messages"][0]["content"]
    assert "clue #1 is one of the first three" in feedback
    # o log diz a pista e o número, nunca o texto nem o nome
    logged = " ".join(r.getMessage() for r in caplog.records
                      if r.name == "finding_memeland.target.clues").lower()
    assert "letter talk before clue 4" in logged
    assert "harbor" not in logged and "salt" not in logged


def test_the_same_fact_is_published_from_clue_four_on():
    """A regra é das pistas 1–3, não das letras: na pista 5 (STRUCTURE, na
    palavra 1) o mesmo tipo de facto verdadeiro sai à primeira."""
    i = 5
    assert label(angle_for_unverifiable(i, ctx())) == "STRUCTURE"
    fact = dict(_true_letter_fact(i), clue="the first word of the name ends with a consonant",
                claims=[{"type": "ends", "word": 1, "value": "consonant"}])
    e = engine([fact])
    d = e.next_clue(ctx(), i, ["c"] * (i - 1))
    assert d.text == fact["clue"]
    assert e.last_attempt_report == (1, {})


@pytest.mark.parametrize("clue_index, text, claims", [
    # falso para "Salt Harbor" (acaba em r): a guarda estrutural diria
    # "corrige a letra" — a regra das pistas 1–3 fala primeiro
    (1, "the second word of the name ends with a vowel",
     [{"type": "ends", "word": 2, "value": "vowel"}]),
    # sem declaração: antes era "declara-a"; agora é "nada de letras"
    (2, "its first letter is a consonant", []),
    # estrutura que não se pode verificar: antes era a guarda estrutural
    (3, "word one hides a prefix", []),
    # a contagem de palavras, certa e declarada (D1)
    (3, "a two-word name, both halves older than the medium",
     [{"type": "word_count", "word": 0, "value": 2}]),
])
def test_on_clues_one_to_three_the_letters_rule_speaks_first(clue_index, text, claims):
    e = engine([{"clue": text, "taunt": "", "claims": claims,
                 "angle": assigned(clue_index), "image_aspect": None},
                "patience is a coin nobody spends"])
    e.next_clue(ctx(), clue_index, ["c"] * (clue_index - 1))
    assert e.last_attempt_report[1] == {"early letters": 1}


@pytest.mark.parametrize("clue_index", [1, 5])
@pytest.mark.parametrize("text", ["the second word has six letters",
                                  "the second word has two syllables",
                                  "the first word holds one vowel"])
def test_counts_were_already_refused_on_every_clue(clue_index, text):
    """D2 sem código novo: a regra 1c das guardas de texto recusa contagens
    de letras, sílabas e vogais em qualquer pista, antes de tudo o resto."""
    e = engine([text, "patience is a coin nobody spends"])
    e.next_clue(ctx(), clue_index, ["c"] * (clue_index - 1))
    assert e.last_attempt_report[1] == {"text rules": 1}


# --------------------------------------------------------------------------- #
# 3. O prompt                                                                  #
# --------------------------------------------------------------------------- #


def test_the_first_three_clues_carry_the_rule_and_the_rest_do_not():
    c = ctx()
    for i in range(1, puzzle_clues_of(c) + 3):
        msg = build_target_user_message(c, i, ["c"] * (i - 1))
        assert (FIRST_THREE_CLUES_RULE in msg) is (i < STRUCTURE_EARLIEST), i
    assert FIRST_THREE_CLUES_RULE == (
        "FIRST THREE CLUES: say NOTHING about the letters, the spelling or the length "
        "of the name or its words — no first or last letter, no vowels, no doubled "
        "letter, no word spelt inside, no count of any kind (word count included). "
        '"claims" must be []. Letters may come from clue 4 on.\n')


def test_the_enumerable_line_offers_no_structure_before_clue_four():
    c = target_ctx("Blue Harbor")
    assert c.enumerable_words == ("blue",)
    early = build_target_user_message(c, 1, [])
    later = build_target_user_message(c, STRUCTURE_EARLIEST, ["c"] * 3)
    assert ("attack these words ONLY by cultural use or their relation to the "
            "other words.\n") in early
    assert "structure" not in early.split("ENUMERABLE")[1].split("\n")[0]
    assert ("attack these words ONLY by cultural use, structure, or their relation "
            "to the other words.\n") in later


def test_an_obedient_writer_publishes_every_puzzle_clue_at_the_first_attempt():
    """O escritor que segue o prompt nunca é recusado por esta regra: nas
    pistas 1–3 nenhum ângulo lhe pede letras."""
    c = ctx()
    for i in range(1, puzzle_clues_of(c) + 1):
        e = engine([f"patience is a coin nobody spends, piece {i}"])
        e.next_clue(c, i, ["c"] * (i - 1))
        assert e.last_attempt_report == (1, {}), (i, relic_slot_for(i, c))

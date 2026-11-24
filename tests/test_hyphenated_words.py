"""Depois da hunt #41 (Pedro, 24/11) — palavras com hífen.

Na #41, uma palavra do nome tinha hífen ("Tree-Contour"). Foi tratada como uma
palavra só, e as pistas descreviam apenas a parte "Contour". Quem as decifrava
ficava com "contour" e procurava a coisa errada.

As duas opções do Pedro: cada parte é uma palavra com pistas próprias, ou a
pista diz claramente que a palavra é composta com hífen. Fica a segunda. Mantém
a leitura única de 10/10 (uma palavra, um número), o plano das pistas e todas
as preparações já seladas. Agora:
  · o escritor vê a palavra marcada como hifenizada, com as partes, e a
    instrução de o dizer em todas as pistas sobre ela (também nas 1–3);
  · a pista tem de dizer "hyphenated" e declarar {"type": "hyphenated"},
    que é verificado como as outras afirmações;
  · é o único facto de grafia permitido antes da pista 4. As letras continuam
    proibidas lá;
  · "hyphenated compound" deixa de cair na família inverificável ("compound"
    sozinho continua a cair).

Nomes sintéticos ("Salt Sea-Harbor", "Sea-Harbor"), nunca um alvo.
"""
from __future__ import annotations

import pytest
from test_target_clues import ITEM_ID, FakeAnthropic

from finding_memeland.content.clue_engine import ClueDraft
from finding_memeland.content.name_words import name_words
from finding_memeland.content.relic_clues import puzzle_clues_of, relic_slot_for
from finding_memeland.target.clues import (
    TARGET_SYSTEM_PROMPT,
    TargetClueContext,
    TargetClueEngine,
    angle_for_unverifiable,
    angle_label,
    build_target_user_message,
    early_letter_errors,
    hyphen_errors,
    numbered_words,
    structural_claim_errors,
    verify_claims,
)
from finding_memeland.target.search_guard import ClueSearchGuard, FakeSearch
from finding_memeland.target.selector import Target

TWO = "Salt Sea-Harbor"
ONE = "Sea-Harbor"
HYPHEN = {"type": "hyphenated", "word": 2}


def _ctx(name: str) -> TargetClueContext:
    t = Target(chain="ethereum", contract="0x3b3ee1931dc30c1957379fac9aba94d1c48a5405",
               token_id=41234, name=name, name_onchain=f"{name} #3",
               description="a quiet place", image="ipfs://QmImage",
               metadata_sha256="ab" * 32, epoch="e1")
    return TargetClueContext.from_target(t, image_description="a lighthouse")


def _index_for(ctx, facet: str, *, reveal: bool = False) -> int:
    start = puzzle_clues_of(ctx) + 1 if reveal else 1
    return next(i for i in range(start, start + 40) if relic_slot_for(i, ctx)[0] == facet)


def _engine(drafts, name: str) -> TargetClueEngine:
    guard = ClueSearchGuard(search=FakeSearch({name.lower(): {ITEM_ID}}),
                            retries=0, sleep_s=0.0)
    return TargetClueEngine(FakeAnthropic(drafts), "model", search_guard=guard,
                            truth_judge=False, solver=False)


# --------------------------------------------------------------------------- #
# A palavra                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("token,hyphenated,parts", [
    ("Tree-Contour", True, ("Tree", "Contour")),
    ("Self-Portrait", True, ("Self", "Portrait")),
    ("co–op", True, ("co", "op")),                   # travessão curto
    ("Rock'n'Roll", False, ("Rock'n'Roll",)),             # apóstrofo não faz partes
    ("Salt", False, ("Salt",)),
    ("Harbor-", False, ("Harbor",)),                      # hífen sem letra depois
])
def test_a_hyphenated_word_is_one_word_with_parts(token, hyphenated, parts):
    (w,) = name_words(token)
    assert (w.hyphenated, w.parts) == (hyphenated, parts)


def test_still_one_word_and_one_number():
    """A leitura de 10/10 não muda: as posições são as de antes."""
    assert [(w.position, w.text) for w in name_words(TWO)] == [(1, "Salt"), (2, "Sea-Harbor")]
    assert numbered_words(TWO) == "1 'Salt', 2 'Sea-Harbor' (ONE hyphenated word)"


# --------------------------------------------------------------------------- #
# A regra                                                                      #
# --------------------------------------------------------------------------- #


def test_a_clue_about_the_hyphenated_word_must_say_it_and_declare_it():
    c = _ctx(TWO)
    i = _index_for(c, "name_word_2", reveal=True)
    only_part = ClueDraft(text="the second word is where boats wait out a storm")
    errs = hyphen_errors(only_part, c, i)
    assert len(errs) == 1
    assert "word 2 ('Sea-Harbor') is ONE word made of parts joined by a hyphen " \
           "('Sea' + 'Harbor')" in errs[0]
    said_only = ClueDraft(text="the second word is hyphenated; after the hyphen, "
                               "boats wait out a storm")
    assert hyphen_errors(said_only, c, i) != []                       # sem declarar
    declared_only = ClueDraft(text="the second word is where boats wait",
                              claims=[HYPHEN])
    assert hyphen_errors(declared_only, c, i) != []                   # sem o dizer
    both = ClueDraft(text=said_only.text, claims=[HYPHEN])
    assert hyphen_errors(both, c, i) == []
    wrong_word = ClueDraft(text=said_only.text, claims=[{"type": "hyphenated", "word": 1}])
    assert hyphen_errors(wrong_word, c, i) != []                      # declarado noutra


def test_the_hyphen_is_not_a_letter_fact_for_the_example_rule():
    """Dizer o hífen não torna a pista numa pista de letras: uma palavra entre
    aspas sobre o sentido continua livre."""
    from finding_memeland.target.clues import example_prose_errors
    assert example_prose_errors("the second word is hyphenated; after the hyphen, a "
                                "'safe' place for boats", [HYPHEN]) == []


def test_other_words_and_art_pieces_owe_nothing():
    c = _ctx(TWO)
    plain = ClueDraft(text="the first word keeps food from spoiling")
    assert hyphen_errors(plain, c, _index_for(c, "name_word_1", reveal=True)) == []
    assert hyphen_errors(plain, c, _index_for(c, "image")) == []


def test_the_claim_is_verified_like_the_others():
    assert verify_claims([HYPHEN], TWO) == []
    errs = verify_claims([{"type": "hyphenated", "word": 1}], TWO)
    assert len(errs) == 1 and "no hyphen joins two parts in 'Salt'" in errs[0]
    assert verify_claims([{"type": "hyphenated", "word": 0}], TWO) == []   # o nome tem um


def test_the_hyphen_is_the_one_spelling_fact_allowed_before_clue_four():
    said = "the word is hyphenated; after the hyphen, boats wait"
    assert early_letter_errors(ClueDraft(text=said, claims=[HYPHEN]), 1) == []
    assert early_letter_errors(ClueDraft(text=said), 1) == []
    letters = ClueDraft(text=said + " and it starts with an S",
                        claims=[HYPHEN, {"type": "starts", "word": 2, "value": "s"}])
    assert early_letter_errors(letters, 1) != []                      # as letras não


def test_hyphenated_compound_is_declarable_compound_alone_is_not():
    assert structural_claim_errors("a hyphenated compound, harbour after the hyphen", TWO) == []
    assert structural_claim_errors("a compound, harbour at the end", TWO) != []
    assert structural_claim_errors("two words joined at the waist", TWO) != []


def test_the_writer_is_told_on_that_word_only():
    c = _ctx(TWO)
    msg = build_target_user_message(c, _index_for(c, "name_word_2"), [])
    assert "HYPHENATED WORD: word 2 ('Sea-Harbor') is ONE word made of parts joined " \
           "by a hyphen ('Sea' + 'Harbor')" in msg
    assert '{"type": "hyphenated", "word": 2}' in msg
    assert "HYPHENATED WORD" not in build_target_user_message(c, _index_for(c, "name_word_1"), [])
    assert '"hyphenated" (no value' in TARGET_SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# De ponta a ponta                                                             #
# --------------------------------------------------------------------------- #


def test_engine_refuses_the_part_alone_and_publishes_the_hyphen():
    """Fase de revelação: a 1.ª versão descreve só a parte depois do hífen
    (como na #41) e é recusada; a 2.ª diz e declara o hífen e sai."""
    c = _ctx(TWO)
    i = _index_for(c, "name_word_2", reveal=True)
    part = {"clue": "the second word is where boats wait out a storm", "taunt": "",
            "angle": None, "image_aspect": None, "claims": []}
    fixed = {**part, "clue": "the second word is hyphenated; after the hyphen, "
                             "boats wait out a storm", "claims": [HYPHEN]}
    e = _engine([part, fixed], TWO)
    assert e.next_clue(c, i, ["c"] * (i - 1)).text == fixed["clue"]
    feedback = e._client.calls[1]["messages"][0]["content"]
    assert "('Sea' + 'Harbor')" in feedback and "use the word 'hyphenated'" in feedback
    assert e.last_attempt_report == (2, {"declaration": 1})


def test_engine_at_clue_one_takes_the_hyphen_and_nothing_else():
    """Pista 1 de um nome de uma palavra hifenizada: o hífen passa as regras
    das pistas 1–3; sem ele, a pista não sai."""
    c = _ctx(ONE)
    assert relic_slot_for(1, c)[0] == "name_word_1"
    angle = angle_label(angle_for_unverifiable(1, c))
    part = {"clue": "where boats wait out a storm", "taunt": "", "angle": angle,
            "image_aspect": None, "claims": []}
    fixed = {**part, "clue": "hyphenated — after the hyphen, where boats wait out a storm",
             "claims": [{"type": "hyphenated", "word": 1}]}
    e = _engine([part, fixed], ONE)
    assert e.next_clue(c, 1, []).text == fixed["clue"]
    assert e.last_attempt_report == (2, {"declaration": 1})

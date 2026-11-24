"""Depois da hunt #41 (Pedro, 24/11) — os exemplos de uma pista de letras são
verificados antes de publicar.

Na #41, a pista 4 deu "detour", "corridor" e "contender" como palavras que NÃO
acabam na mesma letra que "adventurer". As quatro acabam em "r". A declaração
cobria só o NOME, e o juiz lê a pista contra a resposta. Os exemplos não são a
resposta, e ninguém os verificava.

Agora um exemplo também é declarado:
  · dentro da sua afirmação, como "like" (partilha o facto) ou "unlike" (não
    partilha), e testado na sua própria grafia com o mesmo teste do nome;
  · numa pista de letras, uma palavra entre aspas que não esteja declarada é
    recusada;
  · uma comparação ("the same letter as …", "ends like '…'") sem uma
    afirmação starts/ends por trás é recusada;
  · nas pistas 1–3 a comparação conta como conversa de letras (recusada);
  · o juiz também passa a ler os exemplos.

As palavras de exemplo são as da pista 4 da #41 (pública). O nome é
sintético ("Salt Harbor": a palavra 2 acaba em "r", como na #41).
"""
from __future__ import annotations

import pytest
from test_target_clues import ITEM_ID, FakeAnthropic

from finding_memeland.content.clue_engine import ClueDraft
from finding_memeland.content.relic_clues import puzzle_clues_of, relic_slot_for
from finding_memeland.target.clues import (
    TARGET_SYSTEM_PROMPT,
    TRUTH_JUDGE_SYSTEM,
    TargetClueContext,
    TargetClueEngine,
    early_letter_errors,
    example_errors,
    example_prose_errors,
    verify_claims,
)
from finding_memeland.target.search_guard import ClueSearchGuard, FakeSearch
from finding_memeland.target.selector import Target

NAME = "Salt Harbor"
CLUE_41 = ("the second word ends with the same letter as 'adventurer' — not like "
           "'detour', 'corridor' or 'contender'")
CLAIM_41 = {"type": "ends", "word": 2, "value": "r", "like": ["adventurer"],
            "unlike": ["detour", "corridor", "contender"]}


def _ctx() -> TargetClueContext:
    t = Target(chain="ethereum", contract="0x3b3ee1931dc30c1957379fac9aba94d1c48a5405",
               token_id=41234, name=NAME, name_onchain=f"{NAME} #3",
               description="a quiet place by the sea", image="ipfs://QmImage",
               metadata_sha256="ab" * 32, epoch="e1")
    return TargetClueContext.from_target(t, image_description="a lighthouse")


# --------------------------------------------------------------------------- #
# O caso da #41                                                                #
# --------------------------------------------------------------------------- #


def test_the_hunt_41_examples_are_refused_one_by_one():
    errs = verify_claims([CLAIM_41], NAME)
    # o facto sobre o NOME é verdadeiro (harbor acaba em r) — só os exemplos caem
    assert len(errs) == 3
    for w in ("detour", "corridor", "contender"):
        assert any(f"example '{w}' is declared UNLIKE the fact (ends with r), but "
                   f"'{w}' ends with 'r'" in e for e in errs), (w, errs)
    assert not any("adventurer" in e for e in errs)          # o "like" é verdadeiro


def test_true_examples_pass():
    ok = {"type": "ends", "word": 2, "value": "r", "like": ["adventurer", "anchor"],
          "unlike": ["detail", "cloud"]}
    assert verify_claims([ok], NAME) == []


@pytest.mark.parametrize("claim,bad", [
    ({"type": "starts", "word": 1, "value": "s", "like": ["sail"], "unlike": ["stone"]},
     "stone"),
    ({"type": "starts", "word": 1, "value": "consonant", "like": ["rope"],
      "unlike": ["oar"]}, None),
    ({"type": "starts", "word": 1, "value": "consonant", "unlike": ["mast"]}, "mast"),
    ({"type": "contains", "word": 2, "value": "arb", "like": ["barbed"],
      "unlike": ["garbage"]}, "garbage"),
    ({"type": "double_letter", "word": 0, "like": ["anchor"]}, "anchor"),
    ({"type": "palindrome", "word": 0, "unlike": ["kayak"]}, "kayak"),
    ({"type": "no_vowels", "word": 0, "like": ["rhythm"], "unlike": ["sea"]}, None),
])
def test_every_letter_fact_tests_its_examples(claim, bad):
    errs = example_errors(claim)
    if bad is None:
        assert errs == []
    else:
        assert len(errs) == 1 and f"example '{bad}'" in errs[0], errs


def test_examples_only_go_with_a_letter_fact_and_must_be_words():
    assert "only go with a letter fact" in example_errors(
        {"type": "word_count", "value": 2, "like": ["two"]})[0]
    assert "must be lists" in example_errors({"type": "ends", "value": "r", "like": "anchor"})[0]
    assert "is not a word" in example_errors({"type": "ends", "value": "r", "like": ["7"]})[0]
    assert "is not a word" in example_errors({"type": "ends", "value": "r", "like": ["r"]})[0]


# --------------------------------------------------------------------------- #
# A prosa                                                                      #
# --------------------------------------------------------------------------- #


def test_quoted_words_in_a_letter_clue_must_be_declared():
    bare = {"type": "ends", "word": 2, "value": "r"}
    errs = example_prose_errors(CLUE_41, [bare])
    assert len(errs) == 4                                   # as quatro palavras
    assert all("without declaring it" in e for e in errs)
    assert example_prose_errors(CLUE_41, [CLAIM_41]) == []


def test_a_comparison_needs_a_starts_or_ends_claim():
    errs = example_prose_errors("the second word ends like 'adventurer'", [])
    assert any("compares letters ('ends like '')" in e for e in errs)
    errs = example_prose_errors("it shares its last letter with every river", [])
    assert len(errs) == 1 and "compares letters" in errs[0]
    # sem aspas, "ends like" é sobre o sentido: não é uma comparação de letras
    assert example_prose_errors("the second word ends like a sigh", []) == []


def test_quotes_in_a_clue_about_meaning_are_left_alone():
    assert example_prose_errors("the second word is where a 'skiff' sleeps", []) == []
    # o valor de um 'contains' não é um exemplo
    assert example_prose_errors("the second word contains 'arb'",
                                [{"type": "contains", "word": 2, "value": "arb"}]) == []
    # apóstrofos dentro de palavras não são aspas
    assert example_prose_errors("players' boats don't sleep; the second word ends "
                                "with an R", [{"type": "ends", "word": 2, "value": "r"}]) == []
    from finding_memeland.target.clues import _QUOTED_WORD_RE
    assert [m.group("w") for m in _QUOTED_WORD_RE.finditer("o'neill' and 'harbor'")] == ["harbor"]
    # "contains 'x'" sem a afirmação é da guarda da prosa não declarada, não daqui
    assert example_prose_errors("the second word contains 'arb'",
                                [{"type": "ends", "word": 2, "value": "r"}]) == []


def test_the_comparison_is_letter_talk_on_clues_one_to_three():
    d = ClueDraft(text="the second word ends the same way as 'adventurer'", claims=[])
    assert early_letter_errors(d, 2) != []
    assert early_letter_errors(d, 4) == []


def test_the_writer_and_the_judge_are_told():
    assert '"like": [the words that' in TARGET_SYSTEM_PROMPT
    assert '"unlike": [the words that do not]' in TARGET_SYSTEM_PROMPT
    assert "EXAMPLE words" in TRUTH_JUDGE_SYSTEM and "unlike Y" in TRUTH_JUDGE_SYSTEM
    TARGET_SYSTEM_PROMPT.format(index=4, obliqueness=0.5, hard_floor=0.6, phase_rules="")


# --------------------------------------------------------------------------- #
# De ponta a ponta                                                             #
# --------------------------------------------------------------------------- #


def test_engine_refuses_the_hunt_41_clue_and_publishes_a_true_one():
    c = _ctx()
    i = next(k for k in range(puzzle_clues_of(c) + 1, puzzle_clues_of(c) + 30)
             if relic_slot_for(k, c)[0] == "name_word_2")          # fase de revelação
    fixed = {"clue": "the second word ends with the same letter as 'adventurer' — "
                     "unlike 'detail'",
             "taunt": "", "angle": None, "image_aspect": None,
             "claims": [{"type": "ends", "word": 2, "value": "r",
                         "like": ["adventurer"], "unlike": ["detail"]}]}
    first = {**fixed, "clue": CLUE_41, "claims": [CLAIM_41]}
    guard = ClueSearchGuard(search=FakeSearch({NAME.lower(): {ITEM_ID}}),
                            retries=0, sleep_s=0.0)
    e = TargetClueEngine(FakeAnthropic([first, fixed]), "model", search_guard=guard,
                         truth_judge=False, solver=False)
    d = e.next_clue(c, i, ["c"] * (i - 1))
    assert d.text == fixed["clue"]
    feedback = e._client.calls[1]["messages"][0]["content"]
    assert "example 'detour' is declared UNLIKE" in feedback
    assert e.last_attempt_report == (2, {"declaration": 1})

    # o mesmo texto com o facto declarado mas SEM os exemplos: as aspas caem
    bare = {**first, "claims": [{"type": "ends", "word": 2, "value": "r"}]}
    e = TargetClueEngine(FakeAnthropic([bare, fixed]), "model", search_guard=guard,
                         truth_judge=False, solver=False)
    assert e.next_clue(c, i, ["c"] * (i - 1)).text == fixed["clue"]
    feedback = e._client.calls[1]["messages"][0]["content"]
    assert "the clue quotes 'detour' next to a letter fact without declaring it" in feedback

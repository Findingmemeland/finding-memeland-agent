"""Depois da hunt #40 (Pedro, 24/11) — as pistas numeram as palavras sempre da
mesma maneira, sem contar símbolos como "&".

Na #40, a pista 2 contou o "&" do nome como palavra e as pistas seguintes não:
a mesma palavra teve dois números no mesmo fio. A leitura única
(`name_words`, 10/10) nunca contou o "&". Quem o contou foi o escritor, sozinho,
e nada lia o número de volta. Uma posição para lá do fim do nome caía em
"qualquer palavra" (`_scope_at`, permissivo de propósito) e passava.

Agora:
  · o escritor recebe as palavras já numeradas, com a nota de que um símbolo
    não é palavra;
  · cada posição que a prosa afirma ("the third word", "word two", "the last
    word") é lida e tem de existir na leitura única;
  · uma pista sobre a palavra k chama-lhe k (uma pista de relação pode nomear
    a outra palavra, desde que nomeie também a sua).

Nomes sintéticos ("Salt & Harbor"), nunca um alvo.
"""
from __future__ import annotations

import pytest
from test_target_clues import ITEM_ID, FakeAnthropic

from finding_memeland.content.relic_clues import puzzle_clues_of, relic_slot_for
from finding_memeland.target.clues import (
    TargetClueContext,
    TargetClueEngine,
    build_target_user_message,
    numbered_words,
    stated_positions,
    word_position_errors,
)
from finding_memeland.target.search_guard import ClueSearchGuard, FakeSearch
from finding_memeland.target.selector import Target

AMP = "Salt & Harbor"                    # o caso da #40, sintético
AMP3 = "Salt & Harbor Lights"            # o "&" no meio de três palavras


def _ctx(name: str) -> TargetClueContext:
    t = Target(chain="ethereum", contract="0x3b3ee1931dc30c1957379fac9aba94d1c48a5405",
               token_id=41234, name=name, name_onchain=f"{name} #3",
               description="a quiet place by the sea", image="ipfs://QmImage",
               metadata_sha256="ab" * 32, epoch="e1")
    return TargetClueContext.from_target(t, image_description="a lighthouse")


def _engine(drafts, name: str) -> TargetClueEngine:
    guard = ClueSearchGuard(search=FakeSearch({name.lower(): {ITEM_ID}}),
                            retries=0, sleep_s=0.0)
    return TargetClueEngine(FakeAnthropic(drafts), "model", search_guard=guard,
                            truth_judge=False, solver=False)


def _index_for(ctx, facet: str, *, reveal: bool = False) -> int:
    """The first clue of the plan (or of the reveal phase) about `facet`."""
    start = puzzle_clues_of(ctx) + 1 if reveal else 1
    return next(i for i in range(start, start + 40) if relic_slot_for(i, ctx)[0] == facet)


# --------------------------------------------------------------------------- #
# A leitura das posições                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text,refs", [
    ("the third word is where the boats sleep", [3]),
    ("the 3rd word is where the boats sleep", [3]),
    ("word two is where the boats sleep", [2]),
    ("word 2 is where the boats sleep", [2]),
    ("the name's second word keeps the tide", [2]),
    ("the last word is a harbour of sorts", [2]),            # last = n (2)
    ("the second word is what the first word guards", [2, 1]),
    # não são posições
    ("famous last words, said at the dock", []),
    ("the last words of a sailor", []),                     # plural: não é posição
    ("the first two words were never the point", []),
    ("a word, then another", []),
    ("the second-to-last word", []),
])
def test_positions_are_read_off_the_prose(text, refs):
    assert [k for _phrase, k in stated_positions(text, 2)] == refs


def test_the_numbering_skips_the_ampersand():
    assert numbered_words(AMP) == "1 'Salt', 2 'Harbor'"
    assert numbered_words(AMP3) == "1 'Salt', 2 'Harbor', 3 'Lights'"


# --------------------------------------------------------------------------- #
# O caso da #40                                                                #
# --------------------------------------------------------------------------- #


def test_the_ampersand_counted_as_a_word_is_refused():
    """Duas palavras: "the third word" só existe se o "&" contar."""
    errs = word_position_errors("the third word is where the boats sleep", AMP, "name_word_2")
    assert len(errs) == 1
    assert "'the third word' is word 3, but the name has 2 words" in errs[0]
    assert "1 'Salt', 2 'Harbor'" in errs[0] and "'&' is not a word" in errs[0]
    assert word_position_errors("the second word is where the boats sleep", AMP,
                                "name_word_2") == []


def test_the_shift_inside_the_name_is_refused_too():
    """Três palavras: com o "&" contado, Harbor vira "the third word" — um
    número que existe (é Lights). A pista é sobre a palavra 2 e chama-lhe 3."""
    errs = word_position_errors("the third word is where the boats sleep", AMP3, "name_word_2")
    assert len(errs) == 1
    assert "this clue is about word 2 ('Harbor') but it says 'the third word'" in errs[0]
    assert "Call it word 2" in errs[0]


def test_a_relation_clue_may_name_the_other_word_as_long_as_it_names_its_own():
    assert word_position_errors("the second word is what the first word guards", AMP3,
                                "name_word_2") == []
    assert word_position_errors("what the first word guards", AMP3, "name_word_2") != []


def test_no_stated_position_no_check_and_art_pieces_only_need_a_real_number():
    assert word_position_errors("where the boats sleep", AMP, "name_word_2") == []
    assert word_position_errors("the picture holds the second word", AMP, "image") == []
    assert word_position_errors("the picture holds the third word", AMP, "image") != []


def test_the_writer_is_handed_the_numbered_words():
    c = _ctx(AMP)
    msg = build_target_user_message(c, _index_for(c, "name_word_2"), [])
    assert "numbered the ONE way every clue counts them: 1 'Salt', 2 'Harbor'" in msg
    assert "'&' is not a word" in msg
    assert "name (2 words): Salt & Harbor" in msg          # a linha de antes fica


@pytest.mark.parametrize("reveal", [False, True])
def test_engine_refuses_the_miscount_and_publishes_the_true_number(reveal):
    """De ponta a ponta, no puzzle e na revelação: a 1.ª versão conta o "&",
    é recusada com a numeração na resposta ao escritor, a 2.ª sai."""
    c = _ctx(AMP)
    i = _index_for(c, "name_word_2", reveal=reveal)
    e = _engine(["the third word is where the boats sleep",
                 "the second word is where the boats sleep"], AMP)
    d = e.next_clue(c, i, ["c"] * (i - 1))
    assert d.text == "the second word is where the boats sleep"
    feedback = e._client.calls[1]["messages"][0]["content"]
    assert "numbers the words of the name its own way" in feedback
    assert "1 'Salt', 2 'Harbor'" in feedback
    assert e.last_attempt_report == (2, {"word numbering": 1})

"""Palavras de função não são a resposta (07/10).

A lista de termos que uma pista nunca pode escrever saía de TODAS as
palavras do nome com duas ou mais letras, e a guarda 1b procura-as como
SUBSTRING. Uma palavra de função no nome recusava qualquer pista que a
tivesse dentro de outra palavra — "there", "often", "into", "water" — e
uma pista sem essas palavras quase não se consegue escrever.

A correcção é mínima: oito palavras deixam de entrar na lista (the, an,
of, and, in, on, to, at). A forma de procurar NÃO muda — a substring e as
raízes ficam como estavam — e a palavra distintiva continua a chumbar,
como palavra e como raiz.
"""
from __future__ import annotations

import pytest

from finding_memeland.content.guardrails import check_clue
from finding_memeland.content.relic_clues import _solver_target_words
from finding_memeland.target.clues import _FUNCTION_WORDS, TargetClueContext
from finding_memeland.target.selector import Target


def _ctx(name: str, metadata: dict | None = None) -> TargetClueContext:
    target = Target(chain="ethereum", contract="0x" + "ab" * 20, token_id=7,
                    name=name, name_onchain=name, description="",
                    image="ipfs://QmImage", metadata_sha256="ab" * 32, epoch="e1")
    return TargetClueContext.from_target(target, image_description="a lighthouse",
                                         metadata=metadata)


def _reasons(ctx: TargetClueContext, clue: str, terms=None) -> list[str]:
    """A guarda de texto, chamada como a engine a chama para cada rascunho."""
    return list(check_clue(
        clue, clue_index=3, persona_display_name=ctx.display_name,
        persona_handle=ctx.handle, persona_bio=ctx.bio,
        solution_terms=list(ctx.solution_terms if terms is None else terms)).reasons)


# Nomes de três palavras: o plano de pistas não aceita mais (limite antigo,
# alheio a isto).
NAME = "The Severus Lantern"

# Uma pista comum, com as oito palavras DENTRO de outras: there/other/they
# (the), another (an), often/soft (of), sand (and), within (in), stone (on),
# story (to), water (at).
ORDINARY = ("There is another road, though they often walk the other one: "
            "soft sand within, a stone story, and water at the end.")


# --------------------------------------------------------------------------- #
# 1. As oito palavras saem da lista — e só elas                                 #
# --------------------------------------------------------------------------- #


def test_the_eight_function_words_and_no_others():
    assert _FUNCTION_WORDS == {"the", "an", "of", "and", "in", "on", "to", "at"}


@pytest.mark.parametrize("name", [NAME, "Lantern of Severus", "Severus and Lantern",
                                  "Lantern in Severus", "Severus to Lantern"])
def test_a_name_s_function_words_leave_the_never_write_list(name):
    assert _ctx(name).solution_terms == ["lantern", "severus"]


@pytest.mark.parametrize("word", sorted(_FUNCTION_WORDS))
def test_each_of_the_eight_in_any_case(word):
    for form in (word, word.upper(), word.title()):
        assert word not in _ctx(f"{form} Crimson Harbor").solution_terms


@pytest.mark.parametrize("name, kept", [
    ("Song For Night", "for"), ("Day By Night", "by"), ("Sink Or Swim", "or"),
    ("It Is Night", "is"), ("It Is Night", "it"), ("Up From Night", "from"),
    ("Night With Song", "with"), ("Casa De Papel", "de"),
])
def test_other_short_words_stay_on_the_list(name, kept):
    """Só estas oito. Qualquer outra palavra curta continua a ser um termo."""
    assert kept in _ctx(name).solution_terms


def test_an_artist_s_function_words_leave_too_and_the_name_stays():
    terms = _ctx("Salt Harbor", {"artist": "Tom and The Quill"}).solution_terms
    assert {"salt", "harbor", "tom", "quill"} <= set(terms)
    assert "and" not in terms and "the" not in terms


def test_a_name_made_only_of_function_words_keeps_them_all():
    """Uma lista vazia deixava a pista escrever a resposta inteira."""
    assert _ctx("On And On").solution_terms == ["and", "on"]


# --------------------------------------------------------------------------- #
# 2. Uma pista comum já não chumba; a forma de procurar não mudou               #
# --------------------------------------------------------------------------- #


def test_a_name_with_the_no_longer_refuses_there_or_other():
    assert _reasons(_ctx(NAME), ORDINARY) == []


def test_the_matching_itself_is_untouched_it_is_still_a_substring():
    """Se a palavra estivesse na lista, 'there' e 'other' continuavam a
    chumbar — o que mudou foi a lista, não a procura."""
    reasons = _reasons(_ctx(NAME), ORDINARY, terms=["the", "lantern", "severus"])
    assert any("solution term(s): ['the']" in r for r in reasons), reasons


# --------------------------------------------------------------------------- #
# 3. A palavra distintiva continua a chumbar — como palavra e como raiz         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("clue", [
    "A lantern waits where the road gives out.",           # the word itself
    "Two lanterns wait where the road gives out.",         # inside another word
    "Somebody named SEVERUS once kept this light.",        # any case
])
def test_the_distinctive_word_is_still_refused_as_a_word(clue):
    reasons = _reasons(_ctx(NAME), clue)
    assert any("solution term(s)" in r for r in reasons), reasons


def test_the_distinctive_word_is_still_refused_as_a_root():
    reasons = _reasons(_ctx(NAME), "A severe keeper once tended this light.")
    assert any("ROOT VARIANT" in r and "severus~severe" in r for r in reasons), reasons


# --------------------------------------------------------------------------- #
# 4. O efeito indirecto, dito: a lista também alimenta o solucionador           #
# --------------------------------------------------------------------------- #


def test_the_solver_s_word_targets_follow_the_list():
    """O solucionador não foi tocado, mas mede os palpites contra esta mesma
    lista: numa pista sobre UMA palavra do nome, um palpite que apenas
    contenha 'the' deixa de contar como acerto. A palavra da própria pista e
    as distintivas continuam a contar."""
    targets = _solver_target_words(_ctx(NAME), "name_word_2")
    assert targets[0] == "severus"
    assert "the" not in targets
    assert {"lantern", "severus"} <= set(targets)
    assert "of" not in _solver_target_words(_ctx("Lantern of Severus"), "name_word_1")

"""A regra "nada de listas" também vê listas entre aspas (Pedro, 25/11).

Na #41, a pista 4 deu três palavras entre aspas ("detour", "corridor" e
"contender") e a regra das listas não a viu: a regex pedia a vírgula colada
à palavra, e entre as duas estava a aspa que fecha. As aspas saem antes de
procurar a lista: uma lista entre aspas, retas ou curvas, simples ou duplas,
com a vírgula fora ou dentro delas, continua a ser uma lista.

O resto fica igual: duas palavras ("unlike 'cloud' and 'rain'") não são uma
lista; a regra vale nas peças do puzzle e não no reveal. E o escritor fica a
saber que uma pista de letras dá no máximo dois exemplos.

As palavras de exemplo são as da pista 4 da #41 (pública). O nome é
sintético.
"""
from __future__ import annotations

import pytest

from finding_memeland.content.relic_clues import puzzle_clues_of, relic_slot_for
from finding_memeland.target.clues import (
    TARGET_SYSTEM_PROMPT,
    TargetClueContext,
    declaration_errors,
    enumerates_list,
)
from finding_memeland.target.selector import Target

NAME = "Salt Harbor"


def _ctx() -> TargetClueContext:
    t = Target(chain="ethereum", contract="0x3b3ee1931dc30c1957379fac9aba94d1c48a5405",
               token_id=41234, name=NAME, name_onchain=NAME, description="",
               image="ipfs://QmImage", metadata_sha256="ab" * 32, epoch="e1")
    return TargetClueContext.from_target(t, image_description="a lighthouse")


class _Draft:
    taunt, image_aspect, angle, claims = "", None, None, []

    def __init__(self, text: str):
        self.text = text


def _piece(c) -> int:
    """Uma peça do puzzle que não é da arte: onde a regra vale."""
    return next(i for i in range(1, puzzle_clues_of(c) + 1)
                if relic_slot_for(i, c)[0] != "image")


def _lists(errs) -> list[str]:
    return [e for e in errs if "enumerates a list" in e]


QUOTED_LISTS = [
    '"detour", "corridor" and "contender" do not end like "adventurer"',   # a #41
    "'detour', 'corridor' and 'contender'",                               # simples
    "“detour”, “corridor” and “contender”",  # curvas duplas
    "‘detour’, ‘corridor’, or ‘contender’",  # curvas simples
    '"detour," "corridor," and "contender"',                 # a vírgula dentro das aspas
    'neither "detour", "corridor" nor "contender"',
    '"detour", corridor and "contender"',                    # aspas só em algumas
]


@pytest.mark.parametrize("text", QUOTED_LISTS)
def test_a_list_in_quotes_is_a_list(text):
    assert enumerates_list(text)
    c = _ctx()
    assert _lists(declaration_errors(_Draft(text), c, _piece(c)))


def test_the_refusal_says_quotes_count_and_two_examples_is_the_limit():
    c = _ctx()
    (err,) = _lists(declaration_errors(_Draft(QUOTED_LISTS[0]), c, _piece(c)))
    assert "in quotes or not" in err and "at most two examples" in err


@pytest.mark.parametrize("text", [
    "unlike 'cloud' and 'rain', it closes on the same letter as 'anchor'",   # dois
    "the same last letter as 'anchor' — don't trust the sailors' maps",      # apóstrofos
    'the sign read "closed", and nobody asked why',
    '"salt", she said',
])
def test_two_words_or_a_quotation_are_not_a_list(text):
    assert not enumerates_list(text)
    c = _ctx()
    assert not _lists(declaration_errors(_Draft(text), c, _piece(c)))


def test_the_bare_list_is_still_refused_and_the_reveal_still_allows_both():
    c = _ctx()
    n = puzzle_clues_of(c)
    for text in ("red, green, and blue", QUOTED_LISTS[0]):
        assert _lists(declaration_errors(_Draft(text), c, _piece(c)))
        assert not _lists(declaration_errors(_Draft(text), c, n + 1))      # o reveal


def test_the_writer_is_told_two_examples_at_most():
    flat = " ".join(TARGET_SYSTEM_PROMPT.split())
    assert ("At most TWO examples: three in a row ('a', 'b' and 'c') is a list, "
            "refused in quotes or not.") in flat
    TARGET_SYSTEM_PROMPT.format(index=4, obliqueness=0.5, hard_floor=0.6, phase_rules="")

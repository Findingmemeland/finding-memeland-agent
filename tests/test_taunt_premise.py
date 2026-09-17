"""A jeer may mock. It may not deliver a verdict nobody computed.

HUNT #11, 17/09. @lovekillsduty replied "Aeth hopper" — the right name —
and the oracle answered "Bro really said 'let me hop right over the answer'
and stuck the landing in the wrong dimension." Everyone reading the thread
concluded Aeth Hopper was out. It was the answer. He won anyway, because he
was stubborn.

The engine did nothing wrong: it was handed the premise "a player just
replied with a WRONG guess" for a post where no token was named and nothing
was checked. It answered our lie correctly, in public, to an audience.

So the premise is now part of the call, and it has to be true:

  wrong_guess — a token was named, resolved and judged. Saying "wrong" is
                a fact we own.
  no_token    — a name or a comment. We know nothing. The jeer mocks the
                SHAPE and the reply carries the format the winner never got.
"""
from __future__ import annotations

from finding_memeland.claims.taunts import (
    NO_TOKEN_POOL,
    TAUNT_POOL,
    TauntEngine,
    _asserts_wrong,
)
from finding_memeland.target.templates import POST_REPLY_NAME_ONLY


def test_the_no_token_pool_never_says_wrong():
    """THE test. Every line has to survive being sent to someone who is
    right."""
    for line in NO_TOKEN_POOL:
        assert not _asserts_wrong(line), line


def test_the_wrong_guess_pool_is_free_to_say_it():
    """Because there the verdict is real: a token was judged."""
    assert any(_asserts_wrong(line) for line in TAUNT_POOL)


def test_the_premise_picks_the_pool():
    e = TauntEngine()                      # no LLM: pool only, deterministic
    got = {e.taunt("Quantum Bunny", (), kind="no_token") for _ in range(8)}
    assert got <= set(NO_TOKEN_POOL)
    assert {e.taunt("x", ()) for _ in range(8)} <= set(TAUNT_POOL)


def test_wrong_guess_stays_the_default_so_nothing_else_changes():
    assert TauntEngine().taunt("x", ()) in TAUNT_POOL


def test_an_llm_line_that_smuggles_a_verdict_is_refused():
    """The prompt forbids it; a model that slips once publishes a verdict we
    never computed. Prompt for it, then check it, then fall back."""

    class _Slips:
        class messages:
            @staticmethod
            def create(**kw):
                block = type("B", (), {"type": "text",
                                       "text": "wrong answer, fren. cold."})()
                return type("R", (), {"content": [block]})()

    e = TauntEngine(anthropic_client=_Slips(), model="m")
    # the fake really does reach the validator — otherwise this test would
    # pass for the wrong reason, which is how it was written the first time
    assert _asserts_wrong("wrong answer, fren. cold.")
    out = e.taunt("Aeth hopper", (), kind="no_token")
    assert out in NO_TOKEN_POOL            # the slip was dropped
    assert not _asserts_wrong(out)
    # and the SAME line is kept when the premise makes it true
    assert e.taunt("Aeth hopper", ()) == "wrong answer, fren. cold."


def test_an_llm_line_that_behaves_is_kept():
    class _Good:
        class messages:
            @staticmethod
            def create(**kw):
                block = type("B", (), {"type": "text",
                                       "text": "a noun, bravely alone. 🐸"})()
                return type("R", (), {"content": [block]})()

    out = TauntEngine(anthropic_client=_Good(), model="m").taunt(
        "Aeth hopper", (), kind="no_token")
    assert out == "a noun, bravely alone. 🐸"


def test_the_instruction_carries_no_balance():
    """Nobody who shouts a name believes they spent an attempt, so a
    "cost you nothing" there is noise. It belongs where someone could
    reasonably think they had — an address, a broken link."""
    assert "chain:contract:tokenId" in POST_REPLY_NAME_ONLY
    assert "cost you nothing" not in POST_REPLY_NAME_ONLY
    assert "wrong" not in POST_REPLY_NAME_ONLY.lower()

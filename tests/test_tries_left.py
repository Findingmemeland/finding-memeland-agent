"""The guess counter, said out loud — and only when it is true.

Hunt #11 (17/09): five guesses per account, and not one player knew where
they stood. Some had spent none and believed they had spent three, because
a format slip came back as a jeer. Others sprayed until the cap cut them off
without a word.

So the count becomes audible — but ONLY on a real guess, where a token was
named, resolved, judged and found wrong. Never on a format reply, whose
whole point is that nothing was spent: announcing a balance there says the
opposite of what the line means.

And silence whenever the number is not trustworthy. A player told "three
left" and cut off at the fourth is right to be angry; not saying it costs
nothing.
"""
from __future__ import annotations

from finding_memeland.orchestrator.state_machine import Orchestrator


def _orch(cap: int = 5):
    o = object.__new__(Orchestrator)
    o._claim_guess_cap = cap
    return o


def test_the_count_walks_down_in_words():
    o = _orch()
    assert o._tries_left_line({"a": 1}, "a") == "four left."
    assert o._tries_left_line({"a": 2}, "a") == "three left."
    assert o._tries_left_line({"a": 3}, "a") == "two left."


def test_the_last_one_gets_its_own_line():
    """It is the only moment in the hunt where the stakes are personal."""
    assert _orch()._tries_left_line({"a": 4}, "a") == "last one, fren."


def test_the_end_is_silence():
    """The count is done; the player is capped. Saying "zero left" is
    kicking someone already out."""
    o = _orch()
    assert o._tries_left_line({"a": 5}, "a") == ""
    assert o._tries_left_line({"a": 6}, "a") == ""


def test_a_number_we_cannot_trust_is_never_spoken():
    """The counter is rebuilt from the log on restart. If it reads as
    anything but a sane count, the honest move is to say nothing."""
    o = _orch()
    assert o._tries_left_line({}, "a") == ""              # no entry
    assert o._tries_left_line({"a": 0}, "a") == ""        # impossible
    assert o._tries_left_line({"a": None}, "a") == ""     # not a number
    assert o._tries_left_line({"a": "two"}, "a") == ""    # not a number
    assert _orch(cap=0)._tries_left_line({"a": 1}, "a") == ""   # no cap set


def test_a_bigger_cap_still_speaks_plainly():
    o = _orch(cap=8)
    assert o._tries_left_line({"a": 1}, "a") == "7 left."
    assert o._tries_left_line({"a": 4}, "a") == "four left."
    assert o._tries_left_line({"a": 7}, "a") == "last one, fren."

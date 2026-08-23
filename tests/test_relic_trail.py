"""Tests for trail clues (package 3b) — offline, fakes only.

The safety properties matter more than the feature: an unverified trail must NEVER
publish, and the hunt must always fall back to a working direct clue.
"""

from __future__ import annotations

import pytest

from finding_memeland.content.relic_clues import RelicClueContext, RelicClueEngine
from finding_memeland.content.relic_trail import (
    AlwaysDenyVerifier, TrailPolicy, WebSearchTrailVerifier, _parse_trail,
    generate_trail_clue,
)
from finding_memeland.persona.relic import new_identity


def _ctx():
    ident = new_identity(name="Maroon Ledger", description="kept the books",
                         image_prompt="a brass ledger", solution_terms=["Cassandra"])
    return RelicClueContext.from_identity(ident, backstory="the one who was right")


class _Blk:
    def __init__(self, t): self.type, self.text = "text", t


class _Resp:
    def __init__(self, t): self.content = [_Blk(t)]


class FakeClient:
    """Returns queued strings; counts calls."""

    def __init__(self, responses):
        self._r = list(responses)
        self.calls = 0

    @property
    def messages(self):
        outer = self

        class M:
            def create(self, **kw):
                outer.calls += 1
                return _Resp(outer._r[min(outer.calls - 1, len(outer._r) - 1)])

        return M()


class YesVerifier:
    def __init__(self): self.seen = []
    def verify(self, artifact, search_terms):
        self.seen.append((artifact, search_terms))
        return True


TRAIL_JSON = (
    '{"clue": "a climber summited in march 1999; the answer stayed home", '
    '"taunt": "", "artifact": "a 1999 newspaper article about an Everest ascent", '
    '"search_terms": ["everest march 1999", "everest 1999 climber wife"]}'
)
LEAKY_TRAIL_JSON = (
    '{"clue": "it is literally Maroon Ledger", "taunt": "", '
    '"artifact": "a real article", "search_terms": ["a", "b"]}'
)
DIRECT_JSON = '{"clue": "the colour of dried blood, kept in a book", "taunt": ""}'


# --------------------------------------------------------------------------- #
# policy                                                                       #
# --------------------------------------------------------------------------- #


def test_policy_allows_only_the_opening_clues():
    p = TrailPolicy(max_clue_index=3)
    assert p.allows(1) and p.allows(3)
    assert not p.allows(4) and not p.allows(9)


def test_policy_disabled_allows_nothing():
    assert not TrailPolicy(enabled=False).allows(1)


# --------------------------------------------------------------------------- #
# generation + verification                                                    #
# --------------------------------------------------------------------------- #


def test_verified_trail_is_returned_with_artifact_and_terms():
    eng = RelicClueEngine(FakeClient([TRAIL_JSON]), "m")
    v = YesVerifier()
    d = generate_trail_clue(eng, _ctx(), 1, [], verifier=v, policy=TrailPolicy())
    assert d is not None and d.verified
    assert "Everest" in d.artifact and len(d.search_terms) == 2
    assert v.seen[0][0] == d.artifact          # the verifier saw the real claim


def test_unverified_trail_returns_none_after_attempts():
    client = FakeClient([TRAIL_JSON, TRAIL_JSON])
    eng = RelicClueEngine(client, "m")
    d = generate_trail_clue(eng, _ctx(), 1, [], verifier=AlwaysDenyVerifier(),
                            policy=TrailPolicy(max_attempts=2))
    assert d is None and client.calls == 2     # retried, then gave up


def test_trail_not_attempted_outside_the_policy_window():
    client = FakeClient([TRAIL_JSON])
    d = generate_trail_clue(RelicClueEngine(client, "m"), _ctx(), 4, [],
                            verifier=YesVerifier(), policy=TrailPolicy(max_clue_index=3))
    assert d is None and client.calls == 0     # no LLM call at all


def test_missing_artifact_or_terms_is_never_verified():
    v = WebSearchTrailVerifier(lambda p: "VERIFIED")
    assert not v.verify("", ["x"])
    assert not v.verify("something", [])


def test_web_search_verifier_defaults_to_deny():
    assert WebSearchTrailVerifier(lambda p: "VERIFIED").verify("a", ["b"])
    assert not WebSearchTrailVerifier(lambda p: "UNVERIFIED").verify("a", ["b"])
    assert not WebSearchTrailVerifier(lambda p: "maybe, I think so").verify("a", ["b"])
    def _boom(p): raise RuntimeError("network down")
    assert not WebSearchTrailVerifier(_boom).verify("a", ["b"])


def test_bad_json_is_tolerated_and_falls_back():
    client = FakeClient(["not json at all", "still not json"])
    d = generate_trail_clue(RelicClueEngine(client, "m"), _ctx(), 1, [],
                            verifier=YesVerifier(), policy=TrailPolicy(max_attempts=2))
    assert d is None


def test_parse_trail_requires_clue_text():
    with pytest.raises(ValueError):
        _parse_trail('{"artifact": "x", "search_terms": ["y"]}')


# --------------------------------------------------------------------------- #
# engine integration — the safety net                                          #
# --------------------------------------------------------------------------- #


def test_engine_uses_verified_trail():
    eng = RelicClueEngine(FakeClient([TRAIL_JSON]), "m",
                          trail_verifier=YesVerifier(), trail_policy=TrailPolicy())
    draft = eng.next_clue(_ctx(), 1, [])
    assert "climber" in draft.text


def test_engine_falls_back_to_direct_when_unverified():
    """Trail denied -> the ordinary direct clue path produces the clue instead."""
    client = FakeClient([TRAIL_JSON, DIRECT_JSON, DIRECT_JSON, DIRECT_JSON])
    eng = RelicClueEngine(client, "m", trail_verifier=AlwaysDenyVerifier(),
                          trail_policy=TrailPolicy(max_attempts=1))
    draft = eng.next_clue(_ctx(), 1, [])
    assert "dried blood" in draft.text          # the direct clue won


def test_engine_discards_a_verified_trail_that_leaks_the_name():
    """Even verified, a trail that writes the name is thrown away (guardrails)."""
    client = FakeClient([LEAKY_TRAIL_JSON, DIRECT_JSON, DIRECT_JSON])
    eng = RelicClueEngine(client, "m", trail_verifier=YesVerifier(),
                          trail_policy=TrailPolicy())
    draft = eng.next_clue(_ctx(), 1, [])
    assert "maroon" not in draft.text.lower()


def test_engine_without_verifier_behaves_exactly_as_before():
    """No verifier wired => no trail attempt, pure direct path (safe default)."""
    client = FakeClient([DIRECT_JSON])
    eng = RelicClueEngine(client, "m")
    draft = eng.next_clue(_ctx(), 1, [])
    assert "dried blood" in draft.text and client.calls == 1


def test_late_clues_never_use_trails_even_when_enabled():
    """From clue 4 on the ramp is always direct — a hallucinated trail can never
    make a hunt unsolvable in the phase that has to converge."""
    client = FakeClient([DIRECT_JSON])
    eng = RelicClueEngine(client, "m", trail_verifier=YesVerifier(),
                          trail_policy=TrailPolicy(max_clue_index=3))
    draft = eng.next_clue(_ctx(), 5, ["a", "b", "c", "d"])
    assert "dried blood" in draft.text

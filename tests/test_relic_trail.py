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


def test_policy_window_still_caps_the_clue_index():
    """The window rule is unchanged; only the extra anchor condition is new, so
    the ceiling is checked with that condition switched off."""
    p = TrailPolicy(max_clue_index=3, only_on_anchor_angle=False)
    assert p.allows(1) and p.allows(3)
    assert not p.allows(4) and not p.allows(9)


def test_policy_defaults_to_anchor_angle_only():
    """A trail IS the CONCRETE ANCHOR angle (2026-08-23) — binding them makes the
    number of trails per hunt self-limiting instead of needing a quota."""
    p = TrailPolicy()
    assert p.allows(1, "CONCRETE ANCHOR: name ONE specific real-world artefact...")
    assert not p.allows(1, "SOUND/RHYTHM: how the word sounds...")
    assert not p.allows(1)          # no angle given == not an anchor == no trail


def test_policy_disabled_allows_nothing():
    assert not TrailPolicy(enabled=False).allows(1, "CONCRETE ANCHOR: ...")


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


# --------------------------------------------------------------------------- #
# real web-search adapter (2026-08-23)                                         #
# --------------------------------------------------------------------------- #


def _blk(kind, text="text"):
    import types
    return types.SimpleNamespace(type=kind, text=text)


class _SearchClient:
    def __init__(self, blocks, boom=False):
        self._blocks = blocks
        self.boom = boom
        self.last_kwargs = None

        class _Messages:
            def create(inner, **kw):  # noqa: N805
                self.last_kwargs = kw
                if self.boom:
                    raise RuntimeError("api down")
                return type("R", (), {"content": self._blocks})()

        self.messages = _Messages()


def test_web_search_adapter_sends_the_server_side_tool():
    from finding_memeland.content.relic_trail import (
        WEB_SEARCH_TOOL_TYPE, AnthropicWebSearch,
    )
    client = _SearchClient([_blk("text", "VERIFIED")])
    AnthropicWebSearch(client, "m")("check this")
    tools = client.last_kwargs["tools"]
    assert tools[0]["type"] == WEB_SEARCH_TOOL_TYPE
    assert tools[0]["name"] == "web_search"


def test_web_search_adapter_reads_the_last_text_block_only():
    """A searching model emits interim commentary before its conclusion; gluing
    the blocks together would put stray words in front of the verdict."""
    from finding_memeland.content.relic_trail import AnthropicWebSearch
    client = _SearchClient([
        _blk("text", "Let me search for that."),
        _blk("server_tool_use"),
        _blk("web_search_tool_result"),
        _blk("text", "VERIFIED"),
    ])
    assert AnthropicWebSearch(client, "m")("p") == "VERIFIED"


def test_verifier_is_fail_closed_on_every_failure_mode():
    """An unverifiable trail is treated exactly like a false one — a boring
    direct clue always beats a broken hunt."""
    from finding_memeland.content.relic_trail import (
        AnthropicWebSearch, WebSearchTrailVerifier,
    )
    ok = _SearchClient([_blk("text", "VERIFIED")])
    assert WebSearchTrailVerifier(AnthropicWebSearch(ok, "m")).verify("a", ["b"])

    no = _SearchClient([_blk("text", "I searched."), _blk("text", "UNVERIFIED")])
    assert not WebSearchTrailVerifier(AnthropicWebSearch(no, "m")).verify("a", ["b"])

    down = _SearchClient([], boom=True)
    assert not WebSearchTrailVerifier(AnthropicWebSearch(down, "m")).verify("a", ["b"])

    empty = _SearchClient([])
    assert not WebSearchTrailVerifier(AnthropicWebSearch(empty, "m")).verify("a", ["b"])

    # No search terms: refuse without spending an API call at all.
    unused = _SearchClient([_blk("text", "VERIFIED")])
    assert not WebSearchTrailVerifier(AnthropicWebSearch(unused, "m")).verify("a", [])
    assert unused.last_kwargs is None

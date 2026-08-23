"""Tests for relic_hunt_flow (package 3) — offline, fakes only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from finding_memeland.content.relic_clues import (
    IMAGE_EASY_OBLIQUENESS, MIN_PIECES_PER_WORD, PUZZLE_ANGLES, PUZZLE_CLUES,
    PUZZLE_IMAGE_PIECES, PUZZLE_OBLIQUENESS, REVEAL_FLOOR, RelicClueContext,
    RelicClueEngine, angle_for, build_relic_user_message, relic_ramp_plan,
    relic_slot_for,
)
from finding_memeland.claims.relic_claim import (
    RelicClaimOutcome, name_present, verify_relic_claim,
)
from finding_memeland.persona.relic import (
    Relic, RelicState, new_identity, relic_canonical_id,
)
from finding_memeland.persona.relic_pool import FakeRelicRepo, NullPoolCipher, RelicPool
from finding_memeland.persona.relic_findability import FakeFindability, FindabilityRefused
from finding_memeland.telegram.relic_launch import (
    IdentityLeak, RelicLaunchSummary, assert_no_identity_leak,
    build_launch_prompt, stage_relic_launch,
)


def _ctx(name="Maroon Ledger"):
    ident = new_identity(name=name, description="kept the books before books kept themselves",
                         image_prompt="a brass ledger with glowing eyes",
                         solution_terms=["Cassandra"])
    return RelicClueContext.from_identity(ident, backstory="the one who was right"), ident


# --------------------------------------------------------------------------- #
# ramp                                                                         #
# --------------------------------------------------------------------------- #


def test_ramp_head_never_opens_on_the_image():
    for _ in range(50):
        plan = relic_ramp_plan("Maroon Ledger")
        assert plan[0][0] != "image"


def test_puzzle_phase_name_pieces_are_all_hard():
    """Name pieces carry the hard curve; art is the documented exception."""
    ctx, _ = _ctx()
    slots = [relic_slot_for(i, ctx) for i in range(1, PUZZLE_CLUES + 1)]
    names = [(f, o) for f, o in slots if f != "image"]
    assert names and min(o for _, o in names) >= 0.65
    assert {f for f, _ in slots} <= {"name_word_1", "name_word_2", "image"}


def test_artwork_gets_one_hard_piece_and_one_easy():
    """You cannot SEARCH an image, so the art is for recognition: one oblique
    piece, the rest plain descriptions (Pedro, 2026-08-22)."""
    for _ in range(50):
        plan = relic_ramp_plan("Maroon Ledger")
        art = [o for f, o in plan if f == "image"]
        assert len(art) == PUZZLE_IMAGE_PIECES
        assert sum(1 for o in art if o == IMAGE_EASY_OBLIQUENESS) == 1
        assert sum(1 for o in art if o >= 0.65) == 1


def test_no_name_word_is_ever_starved_of_pieces():
    """Regression (measured 2026-08-22, Uncle Pump): both art slots landed on
    word-2 positions, leaving word 2 with ONE puzzle clue. A player needs BOTH
    words to search the relic, so starving one word makes the hunt unwinnable."""
    from collections import Counter
    for _ in range(500):
        counts = Counter(f for f, _ in relic_ramp_plan("Uncle Pump"))
        assert counts["name_word_1"] >= MIN_PIECES_PER_WORD
        assert counts["name_word_2"] >= MIN_PIECES_PER_WORD


def test_each_name_piece_uses_a_different_angle():
    """Nine clues collapsing into three rephrasings was the measured failure on
    2026-08-22; every piece on a word must attack a NEW angle."""
    ctx, _ = _ctx()
    by_word = {}
    for i in range(1, PUZZLE_CLUES + 1):
        facet, _ = relic_slot_for(i, ctx)
        angle = angle_for(i, ctx)
        if angle:
            by_word.setdefault(facet, []).append(angle)
    assert by_word, "no name pieces at all?"
    for facet, angles in by_word.items():
        assert len(angles) == len(set(angles)), f"{facet} repeated an angle"
        assert all(a in PUZZLE_ANGLES for a in angles)


def test_art_pieces_and_reveal_phase_carry_no_angle():
    ctx, _ = _ctx()
    for i in range(1, PUZZLE_CLUES + 1):
        if relic_slot_for(i, ctx)[0] == "image":
            assert angle_for(i, ctx) is None
    assert angle_for(PUZZLE_CLUES + 5, ctx) is None


def test_prompt_carries_the_angle_and_forbids_spent_ones():
    ctx, _ = _ctx()
    first = build_relic_user_message(ctx, 1, [])
    assert "ANGLE FOR THIS PIECE" in first
    assert f"PUZZLE PIECE 1 of {PUZZLE_CLUES}" in first
    word1 = [i for i in range(1, PUZZLE_CLUES + 1)
             if relic_slot_for(i, ctx)[0] == "name_word_1"]
    if len(word1) > 1:
        second = build_relic_user_message(ctx, word1[1], ["c"] * (word1[1] - 1))
        assert "ALREADY SPENT" in second


def test_puzzle_phase_always_has_the_configured_art_pieces():
    for _ in range(50):
        plan = relic_ramp_plan("Maroon Ledger")
        assert len(plan) == PUZZLE_CLUES
        assert sum(1 for f, _ in plan if f == "image") == PUZZLE_IMAGE_PIECES


def test_reveal_phase_alternates_name_words_and_only_eases():
    """From clue 8 on: plain name clues, alternating the two words, getting
    easier every time, forever (no lore, no 'where to search')."""
    ctx, _ = _ctx()
    rev = [relic_slot_for(i, ctx) for i in range(PUZZLE_CLUES + 1, PUZZLE_CLUES + 9)]
    assert all(f.startswith("name_word_") for f, _ in rev)
    assert [f for f, _ in rev][:4] == ["name_word_1", "name_word_2",
                                       "name_word_1", "name_word_2"]
    obl = [o for _, o in rev]
    assert obl == sorted(obl, reverse=True) and min(obl) >= REVEAL_FLOOR


def test_reveal_never_goes_below_the_floor():
    ctx, _ = _ctx()
    assert relic_slot_for(200, ctx)[1] == REVEAL_FLOOR


def test_only_name_and_image_facets_exist_ever():
    """A relic has no handle, no bio, no posts — and (2026-08-22) no lore facet
    (an NFT description isn't searchable) and no 'where to search' facet (the
    pinned rules say it)."""
    ctx, _ = _ctx()
    facets = {relic_slot_for(i, ctx)[0] for i in range(1, 60)}
    assert facets == {"name_word_1", "name_word_2", "image"}


# --------------------------------------------------------------------------- #
# prompt                                                                       #
# --------------------------------------------------------------------------- #


def test_user_message_carries_relic_attributes_and_no_account_fields():
    ctx, _ = _ctx()
    msg = build_relic_user_message(ctx, 1, [])
    assert "Maroon Ledger" in msg and "brass ledger" in msg
    assert "@handle" not in msg and "bio:" not in msg and "pinned" not in msg


def test_prompt_never_mentions_lore_or_where_to_search():
    ctx, _ = _ctx()
    for i in (1, 6, 9, 20):
        msg = build_relic_user_message(ctx, i, ["c"] * (i - 1))
        assert "lore" not in msg.lower()


def test_puzzle_doctrine_only_in_the_puzzle_phase():
    ctx, _ = _ctx()
    assert "PUZZLE PIECE" in build_relic_user_message(ctx, 1, [])
    assert "PUZZLE PIECE" in build_relic_user_message(ctx, PUZZLE_CLUES, ["c"] * 6)
    assert "PUZZLE PIECE" not in build_relic_user_message(ctx, PUZZLE_CLUES + 1, ["c"] * 7)


def test_engine_inherits_guardrail_loop_and_regenerates(monkeypatch):
    """A first draft that leaks the name is rejected by the inherited guardrails
    and regenerated — proving next_clue was reused unchanged."""
    ctx, _ = _ctx()

    class _Blk:
        def __init__(self, t): self.type, self.text = "text", t

    class _Resp:
        def __init__(self, t): self.content = [_Blk(t)]

    drafts = ['{"clue": "the Maroon Ledger itself", "taunt": ""}',
              '{"clue": "the colour of dried blood, kept in a book", "taunt": ""}']

    class _Client:
        def __init__(self): self.calls = 0
        @property
        def messages(self):
            outer = self
            class M:
                def create(self, **kw):
                    outer.calls += 1
                    return _Resp(drafts[min(outer.calls - 1, len(drafts) - 1)])
            return M()

    client = _Client()
    eng = RelicClueEngine(client, "m")
    draft = eng.next_clue(ctx, 1, [])
    assert client.calls == 2                     # first was rejected
    assert "maroon" not in draft.text.lower()    # the leak never survives


# --------------------------------------------------------------------------- #
# claim (name + code against the commitment)                                   #
# --------------------------------------------------------------------------- #


def _claim_fixture():
    ident = new_identity(name="Maroon Ledger", description="d", image_prompt="p",
                         solution_terms=["x"])
    cid = relic_canonical_id("base", "0xaa", "1")
    return ident, cid, ident.commitment_for(cid)


def test_claim_wins_with_name_and_code():
    ident, cid, commit = _claim_fixture()
    out = verify_relic_claim(f"found it! Maroon Ledger {ident.claim_code}",
                             relic_name=ident.name, canonical_id=cid, salt=ident.salt,
                             commitment=commit)
    assert out.won and out.submitted_code == ident.claim_code


def test_code_alone_does_not_win_and_reports_missing_name():
    ident, cid, commit = _claim_fixture()
    out = verify_relic_claim(ident.claim_code, relic_name=ident.name, canonical_id=cid,
                             salt=ident.salt, commitment=commit)
    assert not out.won and out.code_ok and out.partial == "missing_name"


def test_name_alone_does_not_win():
    ident, cid, commit = _claim_fixture()
    out = verify_relic_claim("it's Maroon Ledger obviously", relic_name=ident.name,
                             canonical_id=cid, salt=ident.salt, commitment=commit)
    assert not out.won and out.name_ok and not out.code_ok


def test_wrong_code_with_right_name_is_rejected():
    ident, cid, commit = _claim_fixture()
    out = verify_relic_claim("Maroon Ledger ZZZZ2345", relic_name=ident.name,
                             canonical_id=cid, salt=ident.salt, commitment=commit)
    assert not out.won and out.code_like and out.partial == "wrong_code"


def test_claim_is_case_and_punctuation_tolerant():
    ident, cid, commit = _claim_fixture()
    out = verify_relic_claim(f"maroon-ledger! {ident.claim_code.lower()}",
                             relic_name=ident.name, canonical_id=cid, salt=ident.salt,
                             commitment=commit)
    assert out.won


def test_name_present_requires_all_words():
    assert name_present("Maroon Ledger", "Maroon Ledger")
    assert not name_present("just Maroon", "Maroon Ledger")


# --------------------------------------------------------------------------- #
# launch — BLIND at the interface (the critical requirement)                    #
# --------------------------------------------------------------------------- #


def _staged(indexed=True):
    repo = FakeRelicRepo(); pool = RelicPool(repo, NullPoolCipher())
    ident = new_identity(name="Maroon Ledger", description="kept the books",
                         image_prompt="a brass ledger", solution_terms=["x"])
    pool.add(Relic(id="r1"), ident)
    cid = relic_canonical_id("base", "0xaa", "1")
    pool.mark_minted("r1", chain="base", contract="0xaa", token_id="1",
                     mint_wallet_ref="W1", image_uri="ipfs://x",
                     commitment=ident.commitment_for(cid),
                     minted_at=datetime.now(timezone.utc) - timedelta(days=21))
    canon = FakeFindability("basescan", {"Maroon Ledger"} if indexed else set())
    return pool, ident, canon


def test_launch_prompt_never_shows_the_name_or_code():
    pool, ident, canon = _staged()
    _, prompt, _, _ = stage_relic_launch(pool=pool, prize_fmml=500_000_000,
                                         ladder_exempt=True, canonical_findability=canon,
                                         hunt_number=7)
    low = prompt.lower()
    assert "maroon" not in low and "ledger" not in low
    assert ident.claim_code.lower() not in low
    assert "brass" not in low            # artwork prompt never leaks either
    assert "blind" in low and "relic r1" in low and "aged 21d" in low


def test_leak_backstop_raises_on_any_identity_string():
    _, ident, _ = _staged()
    with pytest.raises(IdentityLeak, match="name"):
        assert_no_identity_leak("Hunt #7 with Maroon Ledger", ident)
    with pytest.raises(IdentityLeak, match="claim code"):
        assert_no_identity_leak(f"code is {ident.claim_code}", ident)


def test_launch_is_refused_when_not_indexed_fail_closed():
    pool, _, canon = _staged(indexed=False)
    with pytest.raises(FindabilityRefused):
        stage_relic_launch(pool=pool, prize_fmml=1, ladder_exempt=False,
                           canonical_findability=canon)


def test_empty_handle_does_not_ban_everything():
    """Regression for the guardrails patch (PATCH_guardrails.md): a target with no
    handle (a relic) must not make EVERY clue read as an identity leak."""
    from finding_memeland.content.guardrails import check_clue
    r = check_clue("a perfectly innocent clue about nothing", clue_index=1,
                   persona_display_name="Maroon Ledger", persona_handle="", persona_bio="")
    assert r.ok


def test_summary_marks_surprise_exemption():
    pool, _, canon = _staged()
    summary, prompt, _, _ = stage_relic_launch(pool=pool, prize_fmml=500_000_000,
                                               ladder_exempt=True,
                                               canonical_findability=canon)
    assert summary.ladder_exempt and "SURPRISE" in prompt

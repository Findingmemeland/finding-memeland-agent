"""Tests for relic_hunt_flow (package 3) — offline, fakes only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from finding_memeland.content.relic_clues import (
    IMAGE_EASY_OBLIQUENESS, MIN_PIECES_PER_WORD, PUZZLE_ANGLES, PUZZLE_CLUES,
    PUZZLE_IMAGE_PIECES, PUZZLE_OBLIQUENESS, REVEAL_FLOOR, RelicClueContext,
    RelicClueEngine, angle_for, build_relic_user_message, enumerable_words_in,
    relic_ramp_plan, relic_slot_for,
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


def test_ramp_order_is_free_but_opens_on_a_name_and_art_pieces_never_touch():
    """Pedro (27/08): clues 1 and 2 need not be word 1 and word 2 — two pieces on
    the same word back to back is fine, the art can come second. Opus (27/08):
    clue 1 is the cold-traffic post under "work out the two-word name", so it
    always opens on a NAME piece. Two art pieces are never adjacent."""
    names = ["Maroon Ledger", "Uncle Pump", "goblin accountant", "soggy firewall",
             "burnt oracle", "leaky astronaut", "pickled Ptolemy", "Clinging Shrimp",
             "clone brunch", "velvet landlord", "midnight plumber", "quiet auditor"]
    same_word_twice = art_second = 0
    for name in names:
        plan = relic_ramp_plan(name)
        facets = [f for f, _ in plan]
        assert facets[0] != "image", name
        assert not any(a == b == "image" for a, b in zip(facets, facets[1:], strict=False)), name
        art_second += facets[1] == "image"
        same_word_twice += any(a == b != "image" for a, b in zip(facets, facets[1:], strict=False))
    assert same_word_twice >= 1        # the freedom is real, not theoretical
    assert art_second >= 1


def test_relation_angle_never_opens_the_hunt():
    """A constraint on the OTHER word is unusable before that word had a piece —
    and clue 1 is the most-read post (Opus, 27/08)."""
    from finding_memeland.content.relic_clues import angle_for_unverifiable
    for name in ["Maroon Ledger", "Uncle Pump", "goblin accountant", "soggy firewall",
                 "burnt oracle", "leaky astronaut", "pickled Ptolemy", "Clinging Shrimp",
                 "clone brunch", "velvet landlord", "midnight plumber", "quiet auditor"]:
        ident = new_identity(name=name, description="d", image_prompt="p", solution_terms=["x"])
        ctx = RelicClueContext.from_identity(ident)
        for pick in (angle_for, angle_for_unverifiable):
            assert not (pick(1, ctx) or "").startswith("RELATION"), name


def test_puzzle_phase_name_pieces_are_all_hard():
    """Name pieces carry the hard curve; art is the documented exception."""
    ctx, _ = _ctx()
    slots = [relic_slot_for(i, ctx) for i in range(1, PUZZLE_CLUES + 1)]
    names = [(f, o) for f, o in slots if f != "image"]
    assert names and min(o for _, o in names) >= 0.65
    assert {f for f, _ in slots} <= {"name_word_1", "name_word_2", "image"}


def test_every_puzzle_piece_is_hard_including_the_art():
    """Hunt #7 post-mortem: the art is generated FROM the name, so a plain
    description of it is the name in other words. All 7 pieces take the hard
    curve; the plain art description lives in the reveal phase now."""
    from finding_memeland.content.relic_clues import REVEAL_IMAGE_SLOT
    ctx, _ = _ctx()
    plan = relic_ramp_plan("Maroon Ledger")
    art = [o for f, o in plan if f == "image"]
    assert len(art) == PUZZLE_IMAGE_PIECES
    assert all(o >= 0.65 for _, o in plan)
    assert [o for _, o in plan] == list(PUZZLE_OBLIQUENESS)
    easy_art = relic_slot_for(PUZZLE_CLUES + REVEAL_IMAGE_SLOT, ctx)
    assert easy_art == ("image", IMAGE_EASY_OBLIQUENESS)


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
    names = [(f, o) for f, o in rev if f != "image"]
    assert len(rev) - len(names) == 1          # exactly one plain art description
    assert [f for f, _ in names][:4] == ["name_word_1", "name_word_2",
                                         "name_word_1", "name_word_2"]
    obl = [o for _, o in names]
    assert obl == sorted(obl, reverse=True) and min(obl) >= REVEAL_FLOOR
    assert obl[0] == 0.4 and obl[1] == 0.35     # the image slot doesn't skip a step


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
    # 3 calls: leaky draft (rejected by text rules, no solver), clean draft,
    # then the blind solver on the clean draft (it answers with the clue JSON,
    # which has no guesses — a pass).
    assert client.calls == 3
    assert "maroon" not in draft.text.lower()    # the leak never survives


# --------------------------------------------------------------------------- #
# Hunt #7 post-mortem (27/08): the opening pair, emoji, rhyme, the blind solver #
# --------------------------------------------------------------------------- #


def _scripted_engine(responses, **kw):
    class _Blk:
        def __init__(self, t): self.type, self.text = "text", t

    class _Resp:
        def __init__(self, t): self.content = [_Blk(t)]

    class _Client:
        def __init__(self):
            self.calls = 0
            self.systems = []
        @property
        def messages(self):
            outer = self
            class M:
                def create(self, **kw):
                    outer.calls += 1
                    outer.systems.append(kw.get("system", ""))
                    return _Resp(responses[min(outer.calls - 1, len(responses) - 1)])
            return M()

    client = _Client()
    return RelicClueEngine(client, "m", **kw), client


def test_sound_is_not_a_puzzle_angle():
    """Hunt #7: "rhymes with 'singing'", "rhymes with 'blimp'" — a rhyme fixes
    the word's ending and any second fact finishes it. Reveal phase only."""
    assert not any(a.startswith("SOUND") for a in PUZZLE_ANGLES)


def test_clue_1_and_clue_2_never_share_an_angle():
    """The structural bug behind Hunt #7: each word restarted the angle sequence
    at the same offset, so the first piece of word 1 and the first piece of
    word 2 — clues 1 and 2 in the old fixed order — always got the SAME angle."""
    from finding_memeland.content.relic_clues import angle_for_unverifiable
    names = ["Maroon Ledger", "Uncle Pump", "goblin accountant", "soggy firewall",
             "burnt oracle", "leaky astronaut", "pickled Ptolemy", "Clinging Shrimp",
             "clone brunch", "velvet landlord", "midnight plumber", "quiet auditor"]
    for name in names:
        ident = new_identity(name=name, description="d", image_prompt="p", solution_terms=["x"])
        ctx = RelicClueContext.from_identity(ident)
        for pick in (angle_for, angle_for_unverifiable):
            angles = [pick(i, ctx) for i in range(1, PUZZLE_CLUES + 1)]
            named = [a for a in angles if a]
            # Consecutive name pieces never share an angle, whichever words they hit.
            for a, b in zip(angles, angles[1:], strict=False):
                assert not (a and b and a == b), (name, a)
            # And the first piece of each word differs from the other word's first.
            firsts = {}
            for i in range(1, PUZZLE_CLUES + 1):
                f = relic_slot_for(i, ctx)[0]
                if f != "image" and f not in firsts:
                    firsts[f] = angles[i - 1]
            assert len(set(firsts.values())) == len(firsts), (name, firsts)
            # (Per-word uniqueness is test_each_name_piece_uses_a_different_angle;
            # across words a repeat is legal on the 4-angle direct path.)
            assert named


def test_word_guidance_is_a_constraint_in_the_puzzle_and_a_hint_in_the_reveal():
    """The old guidance told the model to make the player 'arrive at the literal
    word' via 'meaning, a synonym, or wordplay' — one line under the puzzle
    doctrine. That's the second vector Hunt #7's clues carried."""
    from finding_memeland.content.relic_clues import relic_guidance_for
    ctx, _ = _ctx()
    hard = relic_guidance_for("name_word_1", ctx, 1)
    easy = relic_guidance_for("name_word_1", ctx, PUZZLE_CLUES + 1)
    assert "NOT its meaning" in hard and "NOT a rhyme" in hard
    assert "several candidates" in hard
    assert "synonym" in easy and "rhyme" in easy
    assert "arrives at the literal word" in easy
    assert "arrives at the literal word" not in hard


def test_system_prompt_carries_the_phase_rules():
    eng, client = _scripted_engine(['{"clue": "kept where the numbers sleep", "taunt": ""}',
                                    '{"guesses": ["vault", "bank"]}'])
    eng.next_clue(_ctx()[0], 1, [])
    assert "NO EMOJI" in client.systems[0] and "NO SOUND" in client.systems[0]
    assert "BLIND SOLVER" in client.systems[0]
    eng, client = _scripted_engine(['{"clue": "kept where the numbers sleep", "taunt": "x"}'])
    eng.next_clue(_ctx()[0], PUZZLE_CLUES + 1, ["c"] * PUZZLE_CLUES)
    assert "REVEAL PHASE" in client.systems[0] and "NO EMOJI" not in client.systems[0]


def test_puzzle_phase_rejects_emoji_and_rhyme_through_the_engine():
    """The two Hunt #7 clues, verbatim, must not survive the loop; the third
    attempt (clean) is what gets published. The solver answers 'no idea'."""
    ctx, _ = _ctx()
    eng, client = _scripted_engine([
        '{"clue": "word one of two: rhymes with singing but carries more weight 🎒", '
        '"taunt": ""}',
        '{"clue": "word two of two: leans smaller — the sound alone should make you feel sorry '
        '🦐", "taunt": ""}',
        '{"clue": "kept where the numbers sleep", "taunt": ""}',
        '{"guesses": ["vault", "bank", "safe"]}',
    ])
    draft = eng.next_clue(ctx, 1, [])
    assert draft.text == "kept where the numbers sleep"
    assert client.calls == 4                     # 3 drafts + 1 solver call


def test_emoji_is_allowed_again_in_the_reveal_phase():
    ctx, _ = _ctx()
    eng, client = _scripted_engine(['{"clue": "the colour of dried blood 📕", "taunt": "x"}'])
    draft = eng.next_clue(ctx, PUZZLE_CLUES + 1, ["c"] * PUZZLE_CLUES)
    assert "📕" in draft.text and client.calls == 1     # no solver after the puzzle


def test_blind_solver_rejects_a_clue_it_can_solve():
    """A clue that passes every text rule but is a definition in disguise: the
    solver names the word, the clue is regenerated, and the feedback says why."""
    ctx, _ = _ctx()
    eng, client = _scripted_engine([
        '{"clue": "the colour of dried blood, kept in a book", "taunt": ""}',
        '{"guesses": ["maroon", "crimson", "burgundy"]}',
        '{"clue": "the shade every football club claims is nothing like red", "taunt": ""}',
        '{"guesses": ["claret", "burgundy", "scarlet"]}',
    ])
    draft = eng.next_clue(ctx, 1, [])
    assert "football" in draft.text and client.calls == 4


def test_blind_solver_matches_on_the_stem_and_on_the_facet_word_only():
    from finding_memeland.content.relic_clues import _solver_target_words, solver_hits
    ctx, _ = _ctx(name="Clinging Shrimp")
    w1, art = _solver_target_words(ctx, "name_word_1"), _solver_target_words(ctx, "image")
    assert solver_hits(["cling", "Clingy!"], w1) == ["Clingy!", "cling"]
    assert solver_hits(["shrimp"], w1) == []              # the other word: not this facet
    assert solver_hits(["shrimp"], art) == ["shrimp"]      # art piece: any name word
    assert solver_hits(["prawn", "crab"], _solver_target_words(ctx, "name_word_2")) == []


class _DownSolver:
    name = "down"
    model = "x"

    def guess(self, clues, word_count):
        raise ConnectionError("solver down")


def test_blind_solver_failure_is_fail_closed_on_clue_1(caplog):
    """Nothing is published yet and the operator can still abort: raise, so the
    alert reaches Telegram at once (Opus, 27/08)."""
    ctx, _ = _ctx()
    eng, client = _scripted_engine(['{"clue": "kept where the numbers sleep", "taunt": ""}'],
                                   solver=_DownSolver())
    with pytest.raises(RuntimeError, match="blind solver"):
        eng.next_clue(ctx, 1, [])
    assert client.calls == 1                     # no six wasted regenerations


def test_blind_solver_failure_is_fail_open_from_clue_2_and_logged(caplog):
    """The hunt is live: stopping is worse than publishing on the text rules.
    The solver's absence is logged, the clue goes out."""
    ctx, _ = _ctx()
    eng, client = _scripted_engine(['{"clue": "kept where the numbers sleep", "taunt": "x"}'],
                                   solver=_DownSolver())
    with caplog.at_level("WARNING"):
        draft = eng.next_clue(ctx, 2, ["c1"])
    assert draft.text == "kept where the numbers sleep"
    assert any("blind solver unavailable" in r.message for r in caplog.records)


class _ScriptedSolver:
    """Answers per call: first the ALONE guess list, then the ACCUMULATED one."""
    name = "scripted"
    model = "x"

    def __init__(self, answers):
        self.answers, self.seen = list(answers), []

    def guess(self, clues, word_count):
        self.seen.append(list(clues))
        return self.answers.pop(0) if self.answers else []


def test_accumulated_solver_rejects_at_clue_2_but_only_logs_later(caplog):
    """Pedro (27/08): the answer must stay ambiguous after one clue or two.
    Clue 2 solved TOGETHER with clue 1 is rejected; from clue 3 a converging
    solver is the design working and is only recorded."""
    from finding_memeland.content.relic_clues import SOLVER_STRICT_ACCUMULATED_UNTIL
    assert SOLVER_STRICT_ACCUMULATED_UNTIL == 2
    ctx, _ = _ctx()
    # clue 2: alone -> miss, accumulated -> HIT (reject); second draft: miss, miss.
    solver = _ScriptedSolver([["vault"], ["maroon", "ledger"], ["vault"], ["bank"]])
    eng, client = _scripted_engine([
        '{"clue": "a book that never closes", "taunt": "x"}',
        '{"clue": "kept where the numbers sleep", "taunt": "x"}',
    ], solver=solver)
    draft = eng.next_clue(ctx, 2, ["the colour of dried blood"])
    assert draft.text == "kept where the numbers sleep" and client.calls == 2
    assert solver.seen[1] == ["the colour of dried blood", "a book that never closes"]
    # clue 3: accumulated HIT is logged, not rejected.
    solver = _ScriptedSolver([["vault"], ["maroon", "ledger"]])
    eng, client = _scripted_engine(['{"clue": "a book that never closes", "taunt": "x"}'],
                                   solver=solver)
    with caplog.at_level("INFO"):
        draft = eng.next_clue(ctx, 3, ["c1", "c2"])
    assert draft.text == "a book that never closes" and client.calls == 1
    assert any("converged at clue #3" in r.message for r in caplog.records)


def test_openai_solver_adapter_parses_chat_completions():
    from finding_memeland.content.relic_clues import OpenAIBlindSolver

    class _Msg:
        content = '{"guesses": ["ledger", "book"]}'

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Client:
        def __init__(self):
            self.kw = None
        @property
        def chat(self):
            outer = self
            class _C:
                @property
                def completions(self):
                    class _CC:
                        def create(self, **kw):
                            outer.kw = kw
                            return _Resp()
                    return _CC()
            return _C()

    c = _Client()
    out = OpenAIBlindSolver(c, "gpt-test").guess(["c1", "c2"], 2)
    assert out == ["ledger", "book"] and c.kw["model"] == "gpt-test"
    assert "Last clue (solve this one): c2" in c.kw["messages"][1]["content"]
    assert "exactly 2 word(s)" in c.kw["messages"][0]["content"]


def test_blind_solver_can_be_switched_off():
    ctx, _ = _ctx()
    eng, client = _scripted_engine(
        ['{"clue": "kept where the numbers sleep", "taunt": ""}'], solver=False
    )
    eng.next_clue(ctx, 1, [])
    assert client.calls == 1


# --------------------------------------------------------------------------- #
# claim (name + code against the commitment)                                   #
# --------------------------------------------------------------------------- #


# Os testes do claim nome+código viviam aqui. Foram removidos com
# claims/relic_claim.py (auditoria 2026-08-26, P0-2): exercitavam um módulo
# que nenhum caminho vivo chamava, e por isso davam confiança numa regra que
# o jogo não tinha. A regra publicada — e a implementada — é só o código.


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


# --------------------------------------------------------------------------- #
# enumerable words + concrete anchor (2026-08-23)                              #
# --------------------------------------------------------------------------- #


def test_enumerable_words_are_detected_from_the_name():
    assert enumerable_words_in("Uncle Pump") == ("pump", "uncle")
    assert enumerable_words_in("tuesday's Gremlin") == ("tuesday",)
    assert enumerable_words_in("goblin accountant") == ()


def test_clue_prompt_forbids_gesturing_at_the_category():
    """The failure that cost mini hunt #1 was never the word 'uncle' — it was
    'a title that skips a generation', which hands over the category and leaves
    ten candidates. The word stays legal; the clue is what must change."""
    ident = new_identity(name="Uncle Pump", description="he calls the bottom",
                         image_prompt="a man with a pump", solution_terms=["uncle", "pump"])
    ctx = RelicClueContext.from_identity(ident)
    assert ctx.enumerable_words == ("pump", "uncle")
    msg = build_relic_user_message(ctx, 1, [])
    assert "ENUMERABLE WORD" in msg
    assert "gesture at the" in msg.lower() or "CATEGORY" in msg


def test_no_enumerable_rule_when_the_name_is_open_field():
    ident = new_identity(name="goblin accountant", description="d",
                         image_prompt="p", solution_terms=["goblin"])
    ctx = RelicClueContext.from_identity(ident)
    assert "ENUMERABLE WORD" not in build_relic_user_message(ctx, 1, [])


def test_enumerable_rule_is_puzzle_phase_only():
    """The reveal phase exists to END the hunt — after clue PUZZLE_CLUES the
    clues are meant to hand the word over."""
    ident = new_identity(name="Uncle Pump", description="d",
                         image_prompt="p", solution_terms=["uncle"])
    ctx = RelicClueContext.from_identity(ident)
    assert "ENUMERABLE WORD" not in build_relic_user_message(ctx, PUZZLE_CLUES + 2, [])


def test_concrete_anchor_is_an_available_angle():
    """Pedro's angle: point at one real-world artefact where the answer physically
    shows up, instead of circling what the word means."""
    anchor = [a for a in PUZZLE_ANGLES if a.startswith("CONCRETE ANCHOR")]
    assert len(anchor) == 1
    assert "CERTAIN" in anchor[0]          # hallucinated anchors are hunt-killers
    assert "English" in anchor[0]          # must be reachable by the audience


def test_anchor_angle_never_reaches_the_direct_path():
    """An artefact nobody checked is worse than a clue that is merely hard: a
    wrong anchor makes players eliminate the RIGHT answer. So anchors only ever
    reach players through the verified trail path."""
    from finding_memeland.content.relic_clues import (
        angle_for_unverifiable, is_anchor_angle,
    )
    for name in ("pickled Ptolemy", "goblin accountant", "Uncle Pump", "clone brunch"):
        ident = new_identity(name=name, description="d", image_prompt="p",
                             solution_terms=["x"])
        ctx = RelicClueContext.from_identity(ident)
        for i in range(1, PUZZLE_CLUES + 1):
            assert not is_anchor_angle(angle_for_unverifiable(i, ctx))
            assert "CONCRETE ANCHOR" not in build_relic_user_message(
                ctx, i, [], allow_anchor=False
            )


def test_anchor_substitute_never_collides_with_another_piece():
    """Regression: a word's pieces take CONSECUTIVE angles, so substituting the
    anchor with the NEXT one hands this piece the next piece's angle and rebuilds
    the 2026-08-22 failure (nine clues, three angles). Measured: the naive +1
    shift collided on 7 of 10 sample names."""
    from collections import Counter

    from finding_memeland.content.relic_clues import angle_for_unverifiable
    for name in ("pickled Ptolemy", "goblin accountant", "Uncle Pump",
                 "leaky astronaut", "soggy firewall", "burnt oracle"):
        for _ in range(40):
            ident = new_identity(name=name, description="d", image_prompt="p",
                                 solution_terms=["x"])
            ctx = RelicClueContext.from_identity(ident)
            per_word = {}
            for i in range(1, PUZZLE_CLUES + 1):
                facet, _ = relic_slot_for(i, ctx)
                angle = angle_for_unverifiable(i, ctx)
                if facet != "image" and angle:
                    per_word.setdefault(facet, []).append(angle)
            for facet, angles in per_word.items():
                assert len(angles) == len(set(angles)), (name, facet, Counter(angles))


def test_trail_policy_binds_trails_to_the_anchor_angle():
    """A trail IS the concrete-anchor angle: both mean 'point at something real'.
    Binding them makes the count self-limiting (~1-2 per hunt) instead of needing
    a separate quota."""
    from finding_memeland.content.relic_trail import TrailPolicy
    policy = TrailPolicy()
    assert policy.allows(1, "CONCRETE ANCHOR: name ONE specific...")
    assert not policy.allows(1, "SOUND/RHYTHM: how the word sounds...")
    assert not policy.allows(1, None)
    # The reveal phase must hand the word over — never send players researching.
    assert not policy.allows(PUZZLE_CLUES + 1, "CONCRETE ANCHOR: ...")


def test_engine_without_a_verifier_stays_on_the_direct_path():
    """Fail-safe: no verifier wired == no anchors published, silently."""
    engine = RelicClueEngine(object(), "m")
    ident = new_identity(name="Uncle Pump", description="d", image_prompt="p",
                         solution_terms=["x"])
    ctx = RelicClueContext.from_identity(ident)
    assert engine._try_trail(ctx, 1, []) is None


# --------------------------------------------------------------------------- #
# P1-4 · a rampa é a MESMA depois de um crash-resume                           #
# --------------------------------------------------------------------------- #


def test_ramp_plan_is_deterministic_for_a_given_name():
    """O plano é reconstruído no resume. Sem semente, uma hunt retomada publicava
    a primeira metade de um plano e a segunda de outro — e 11,3% dessas misturas
    deixavam uma palavra do nome com uma só pista de puzzle."""
    plans = [relic_ramp_plan("Maroon Ledger") for _ in range(30)]
    assert all(p == plans[0] for p in plans)
    assert relic_ramp_plan("Uncle Pump") != plans[0]   # continua a variar por hunt


# --------------------------------------------------------------------------- #
# P1-7 · o floor de elegibilidade volta ao prompt do relic                     #
# --------------------------------------------------------------------------- #


def _summary(**kw):
    base = dict(
        relic_id="r1", commitment="c" * 64, minted_at=None, contract="0xabc",
        prize_fmml=1_000_000_000, ladder_exempt=False, findability_ok=True,
        findability_surface="rarible", hunt_number=9,
    )
    base.update(kw)
    return RelicLaunchSummary(**base)


def test_launch_prompt_shows_the_eligibility_floor():
    text = build_launch_prompt(_summary(holding_floor_fmml=10_000_000,
                                       non_holder_prize_pct=10))
    assert "10,000,000 $FIND" in text and "10%" in text


def test_launch_prompt_screams_when_the_floor_is_zero():
    """Um floor a zero numa hunt de 1B é a diferença entre pagar 100M e 1B."""
    assert "floor ZERO" in build_launch_prompt(_summary(holding_floor_fmml=0))


# --------------------------------------------------------------------------- #
# P0-A · a Clue 1 de uma hunt relic não pode publicar o explainer das personas #
# --------------------------------------------------------------------------- #


def test_relic_clue_one_never_tells_players_to_search_x():
    """O explainer antigo dizia "hide their account somewhere on X … the code in
    its bio". Numa hunt relic isso é instrução ERRADA, publicada, na primeira
    coisa que um estranho lê."""
    from finding_memeland.content.templates import clue_one

    post = clue_one(hunt_n=8, clue_text="c", prize="1", integrity_hash="h", relic=True)
    low = post.lower()
    assert "somewhere on x" not in low and "in its bio" not in low
    assert "marketplace" in low and "onchain on base" in low


def test_persona_clue_one_is_untouched():
    from finding_memeland.content.templates import clue_one

    assert "somewhere on X" in clue_one(
        hunt_n=8, clue_text="c", prize="1", integrity_hash="h"
    )


# --------------------------------------------------------------------------- #
# P0-C · blind mode a sério: o contrato é o nome e o código a um clique        #
# --------------------------------------------------------------------------- #


def test_launch_prompt_never_shows_the_contract_address():
    text = build_launch_prompt(_summary(contract="0xdeadbeef"))
    assert "0xdeadbeef" not in text
    assert "commitment" in text        # o que identifica o relic sem o abrir


# --------------------------------------------------------------------------- #
# Auditoria v3 · texto público de uma hunt relic não fala de personas          #
# --------------------------------------------------------------------------- #


def _winner_data(**kw):
    from finding_memeland.content.templates import WinnerData

    base = dict(
        hunt_n=8, winner_handle="@x", time_to_win="12m",
        prize_amount="100,000,000", tx_link="0xtx",
        persona_handle="relic:abc12345", persona_user_id="base:0xa:1",
        claim_code="ABCD2345", salt="s",
    )
    base.update(kw)
    return WinnerData(**base)


def test_relic_reveal_talks_about_the_relic_not_a_persona():
    """O post mais lido da hunt dizia "The hidden persona was @relic:abc… — the
    profile stays up as a trophy", num jogo onde não há perfil nenhum."""
    from finding_memeland.content.templates import winner_announcement

    text = winner_announcement(
        _winner_data(relic_name="Maroon Ledger", relic_link="basescan.org/x")
    ).lower()
    assert "persona" not in text and "profile" not in text
    assert "maroon ledger" in text and "there is no second one" in text
    assert "integrity check" in text          # a prova continua lá


def test_persona_reveal_is_untouched():
    from finding_memeland.content.templates import winner_announcement

    assert "The hidden persona was" in winner_announcement(_winner_data())

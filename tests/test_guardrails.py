from finding_memeland.content.guardrails import check_clue

PERSONA = dict(
    persona_display_name="Sarah Kovac",
    persona_handle="@sarah_k392",
    persona_bio="just here for the vibes",
)


def test_blocks_clue_that_leaks_name():
    r = check_clue("Sarah knows the way", clue_index=2, **PERSONA)
    assert not r.ok
    assert any("identity" in x for x in r.reasons)


def test_blocks_url_in_clue():
    r = check_clue("look at https://x.com/foo", clue_index=5, **PERSONA)
    assert not r.ok
    assert any("URL" in x for x in r.reasons)


def test_blocks_handle_reference_in_early_clue():
    r = check_clue("follow @someone for a hint", clue_index=2, **PERSONA)
    assert not r.ok


def test_clean_oblique_clue_passes():
    r = check_clue("I count in fours but never reach five.", clue_index=1, **PERSONA)
    assert r.ok, r.reasons


# --------------------------------------------------------------------------- #
# Emoji + puzzle phase (Hunt #7 post-mortem, 27/08)                            #
# --------------------------------------------------------------------------- #

RELIC = dict(persona_display_name="Clinging Shrimp", persona_handle="",
             persona_bio="", solution_terms=["clinging", "shrimp"])


def test_emoji_that_depicts_the_answer_is_read_by_name():
    """🦐 is U+1F990 SHRIMP. The Hunt #7 clue 2 passed because the guardrail only
    compared text; the emoji IS the answer."""
    r = check_clue("leans smaller — feel a little sorry for it 🦐", clue_index=9, **RELIC)
    assert not r.ok
    assert any("emoji draws the answer" in x for x in r.reasons)
    assert any("shrimp" in x for x in r.reasons)


def test_unrelated_emoji_is_fine_outside_the_puzzle_phase():
    r = check_clue("it lives in a bucket 🪣 by the marsh", clue_index=9, **RELIC)
    assert r.ok


def test_puzzle_phase_bans_every_emoji():
    r = check_clue("it lives in a bucket 🪣 by the marsh", clue_index=1, puzzle_phase=True, **RELIC)
    assert not r.ok and any("NO emoji" in x for x in r.reasons)
    clean = check_clue("it lives in a bucket by the marsh", clue_index=1, puzzle_phase=True, **RELIC)
    assert clean.ok


def test_puzzle_phase_bans_rhymes_and_sound_alikes():
    for text in ("rhymes with 'blimp' but leans smaller",
                 "sounds like a warning label",
                 "say it out loud and you'll hear it",
                 "the sound alone should make you feel sorry"):
        r = check_clue(text, clue_index=2, puzzle_phase=True, **RELIC)
        assert not r.ok and any("SOUND" in x for x in r.reasons), text
    # The same texts are legal in the reveal phase.
    assert check_clue("rhymes with 'blimp' but leans smaller", clue_index=9, **RELIC).ok


def test_the_two_hunt7_clues_verbatim_are_rejected():
    c1 = ("word one of two: rhymes with 'singing' but carries more weight, sounds like it "
          "belongs on a warning label for someone who won't let go of their bags 🎒")
    c2 = ("word two of two: rhymes with 'blimp' but leans smaller — the sound alone should "
          "make you feel a little sorry for it 🦐")
    assert not check_clue(c1, clue_index=1, puzzle_phase=True, **RELIC).ok
    assert not check_clue(c2, clue_index=2, puzzle_phase=True, **RELIC).ok


def test_persona_hunts_are_untouched_by_the_puzzle_switch():
    """Default puzzle_phase=False: the persona engine's clues keep their emoji."""
    assert check_clue("check what hangs above their head 👀", clue_index=2, **PERSONA).ok

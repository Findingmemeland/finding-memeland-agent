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

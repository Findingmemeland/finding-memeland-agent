from finding_memeland.content.guardrails import check_clue

PERSONA = dict(
    persona_display_name="Celestial Mechanic",
    persona_handle="@kepler_77",
    persona_bio="everything moves, everything pulls",
)


def test_blocks_literal_solution_term_any_clue():
    # Late clue (index 6) may be structural, but still must not name the answer.
    r = check_clue(
        "the man who found Neptune with only a pen",
        clue_index=6,
        solution_terms=["Le Verrier", "Neptune"],
        **PERSONA,
    )
    assert not r.ok
    assert any("solution term" in x for x in r.reasons)


def test_allows_oblique_clue_without_solution_terms():
    r = check_clue(
        "found a whole world without ever looking up. then chased one that wasn't there.",
        clue_index=2,
        solution_terms=["Le Verrier", "Neptune", "Vulcan"],
        **PERSONA,
    )
    assert r.ok, r.reasons


def test_solution_term_match_is_case_insensitive():
    r = check_clue("all roads lead to neptune", clue_index=5,
                   solution_terms=["Neptune"], **PERSONA)
    assert not r.ok


# --- Pack 2, item 2 (post-mortem P2a): counting claims -------------------
# Clue 2 of the Genesis hunt said "five syllables" for Penelope (four): anyone
# who counted eliminated the RIGHT answer. Unverifiable units are banned; word
# counts are verified against the display name.

def test_blocks_syllable_count_claim():
    r = check_clue("her name hums in five syllables, if you listen",
                   clue_index=2, **PERSONA)
    assert not r.ok
    assert any("count" in x for x in r.reasons)


def test_blocks_digit_letter_count_claim():
    r = check_clue("8 letters stand between you and the prize",
                   clue_index=4, **PERSONA)
    assert not r.ok


def test_blocks_hyphenated_and_vowel_variants():
    assert not check_clue("a three-syllable secret", clue_index=3, **PERSONA).ok
    assert not check_clue("count the vowels: four vowels exactly",
                          clue_index=5, **PERSONA).ok


def test_wrong_word_count_is_rejected():
    # PERSONA display name "Celestial Mechanic" has TWO words.
    r = check_clue("the name is three words long", clue_index=4, **PERSONA)
    assert not r.ok
    assert any("2 word" in x for x in r.reasons)


def test_correct_word_count_is_allowed():
    r = check_clue("two words, one obsession with orbits", clue_index=4, **PERSONA)
    assert r.ok, r.reasons


def test_counting_without_units_still_passes():
    # Numbers that are not sub-word counting claims stay legal.
    r = check_clue("I count in fours but never reach five.", clue_index=1, **PERSONA)
    assert r.ok, r.reasons

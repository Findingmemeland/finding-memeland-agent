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

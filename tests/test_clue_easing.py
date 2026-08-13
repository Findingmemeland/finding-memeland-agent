"""Obliqueness: ramp-driven with a persona (2026-08-13), legacy easing without."""

from finding_memeland.content.clue_engine import (
    RAMP_HANDLE_START,
    RAMP_NAME_EASY,
    RAMP_NAME_HARD,
    RAMP_POSTS,
    PersonaContext,
    obliqueness_for,
    ramp_plan,
)


def test_legacy_easing_without_persona_decreases_monotonically():
    vals = [obliqueness_for(i) for i in range(1, 8)]
    assert vals[0] == 1.0
    assert all(a > b for a, b in zip(vals, vals[1:]))
    assert abs(obliqueness_for(2) - 0.70) < 1e-9


def _persona():
    p = PersonaContext(
        display_name="Cassandra Tired", handle="@x", bio="b",
        avatar_description="a", voice="v", backstory="bs",
        solution_terms=["s"],
    )
    p.clue_facet_plan = ramp_plan("Cassandra Tired")
    return p


def test_ramp_obliqueness_is_per_role_not_monotonic():
    """The ramp deliberately breaks monotonicity: the avatar clue (obvious)
    can land at clue 1 while a hard name clue lands at clue 3."""
    p = _persona()
    head = {obliqueness_for(i, p) for i in (1, 2, 3)}
    assert head == {RAMP_NAME_HARD, 0.25}            # two hards + one obvious


def test_ramp_phases_have_the_agreed_levels():
    p = _persona()
    assert obliqueness_for(4, p) == RAMP_NAME_EASY
    assert obliqueness_for(5, p) == RAMP_NAME_EASY
    assert tuple(obliqueness_for(i, p) for i in (6, 7, 8)) == RAMP_POSTS
    assert obliqueness_for(9, p) == RAMP_HANDLE_START

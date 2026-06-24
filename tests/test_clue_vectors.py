from finding_memeland.content.clue_engine import (
    VECTOR_GUIDANCE,
    PersonaContext,
    clue_plan,
    clue_vector_for,
)


def _persona(name):
    return PersonaContext(
        display_name=name, handle="@x", bio="b", avatar_description="a",
        voice="v", backstory="bs", solution_terms=["secret"],
        banner_description="banner", findable_post="a very distinctive phrase here",
    )


def test_two_word_name_splits_into_first_and_last():
    plan = clue_plan(_persona("Celestial Mechanic"))
    assert "first_name" in plan and "last_name" in plan
    assert plan[0] == "identity" and plan[-1] == "signature_post"


def test_single_word_name_uses_name_vector():
    plan = clue_plan(_persona("icarus"))
    assert "name" in plan
    assert "first_name" not in plan


def test_early_clues_target_identity():
    p = _persona("Celestial Mechanic")
    assert clue_vector_for(1, p) == "identity"
    assert clue_vector_for(2, p) == "identity"


def test_progression_hits_visual_then_name():
    p = _persona("Celestial Mechanic")
    assert clue_vector_for(3, p) == "avatar"
    assert clue_vector_for(4, p) == "banner"
    assert clue_vector_for(5, p) == "first_name"


def test_late_clues_clamp_to_locator_post():
    p = _persona("Celestial Mechanic")
    assert clue_vector_for(7, p) == "signature_post"
    assert clue_vector_for(20, p) == "signature_post"  # the longer it runs, the more it points there


def test_every_vector_has_guidance():
    p = _persona("Celestial Mechanic")
    for i in range(1, 10):
        assert clue_vector_for(i, p) in VECTOR_GUIDANCE

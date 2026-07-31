from finding_memeland.content.clue_engine import (
    PersonaContext,
    clue_plan,
    clue_vector_for,
    guidance_for,
    shuffled_facet_plan,
)


def _persona(name):
    return PersonaContext(
        display_name=name, handle="@x", bio="b", avatar_description="a",
        voice="v", backstory="bs", solution_terms=["secret"],
        banner_description="banner", findable_post="a very distinctive phrase here",
    )


def test_single_word_name_has_one_name_facet():
    plan = clue_plan(_persona("icarus"))
    assert "name_word_1" in plan and "name_word_2" not in plan


def test_two_word_name_has_two_name_facets():
    plan = clue_plan(_persona("Celestial Mechanic"))
    assert "name_word_1" in plan and "name_word_2" in plan and "name_word_3" not in plan


def test_three_word_name_has_a_facet_for_every_word():
    # The whole point of this change: 3 words -> 3 name clues, not just first+last.
    plan = clue_plan(_persona("Arachne Spinnerette Weaver"))
    assert {"name_word_1", "name_word_2", "name_word_3"} <= set(plan)


def test_guidance_resolves_each_name_word_to_the_right_word():
    p = _persona("Arachne Spinnerette Weaver")
    assert "Arachne" in guidance_for("name_word_1", p)
    assert "Spinnerette" in guidance_for("name_word_2", p)
    assert "Weaver" in guidance_for("name_word_3", p)


def test_ordered_fallback_plan():
    # Pedro's difficulty spec (2026-07-29): two rounds over {name words, avatar}
    # (hard pass + clearer revisit), banner/bio OUT, posts only from clue 7.
    p = _persona("Celestial Mechanic")
    assert clue_plan(p) == [
        "name_word_1", "name_word_2", "avatar",
        "name_word_1", "name_word_2", "avatar",
        "signature_post",
    ]
    assert clue_vector_for(len(clue_plan(p)), p) == "signature_post"


def test_shuffled_plan_always_ends_in_signature_post():
    for _ in range(50):
        plan = shuffled_facet_plan("Arachne Spinnerette Weaver")
        assert plan[-1] == "signature_post"
        assert "signature_post" not in plan[:-1]


def test_shuffled_plan_contains_all_facets_three_word_name():
    plan = shuffled_facet_plan("Arachne Spinnerette Weaver")
    assert set(plan) == {
        "name_word_1", "name_word_2", "name_word_3", "avatar", "signature_post",
    }
    # Every oblique facet gets exactly TWO clues (hard + clearer revisit).
    for facet in ("name_word_1", "name_word_2", "name_word_3", "avatar"):
        assert plan.count(facet) == 2
    # Banner and bio are out of the game — they don't help the search.
    assert "banner" not in plan and "bio" not in plan


def test_order_actually_varies_across_hunts():
    plans = {tuple(shuffled_facet_plan("Celestial Mechanic")) for _ in range(30)}
    assert len(plans) > 1


def test_late_clues_clamp_to_locator_post():
    plan = shuffled_facet_plan("icarus")
    p = _persona("icarus")
    p.clue_facet_plan = plan
    assert clue_vector_for(len(plan), p) == "signature_post"
    assert clue_vector_for(len(plan) + 5, p) == "signature_post"


def test_every_planned_facet_has_resolvable_guidance():
    p = _persona("Arachne Spinnerette Weaver")
    for facet in clue_plan(p):
        assert guidance_for(facet, p)  # non-empty, no KeyError


def test_no_facet_repeats_back_to_back():
    for _ in range(100):
        plan = shuffled_facet_plan("volt drizzle")
        for a, b in zip(plan, plan[1:]):
            assert a != b, plan


def test_posts_only_from_clue_seven_and_anchors_alternate():
    """Clues 1-6 = oblique rounds; clue 7 = pinned locator; from clue 8 the
    prep-window anchor posts alternate in (when they exist)."""
    p = _persona("volt drizzle")
    p.clue_facet_plan = shuffled_facet_plan("volt drizzle")
    for i in range(1, 7):
        assert clue_vector_for(i, p) in {"name_word_1", "name_word_2", "avatar"}
    assert clue_vector_for(7, p) == "signature_post"
    assert clue_vector_for(9, p) == "signature_post"  # no anchors -> pinned only
    p.anchor_posts = ["a real prep post"]
    assert clue_vector_for(7, p) == "signature_post"  # pinned still opens the phase
    assert clue_vector_for(8, p) == "anchor_post"
    assert clue_vector_for(9, p) == "signature_post"
    assert clue_vector_for(10, p) == "anchor_post"


def test_anchor_texts_hidden_from_prompt_before_clue_seven():
    from finding_memeland.content.clue_engine import _build_user_message

    p = _persona("volt drizzle")
    p.clue_facet_plan = shuffled_facet_plan("volt drizzle")
    p.anchor_posts = ["the pile of coins on a wet cloth"]
    for i in range(1, 7):
        assert "pile of coins" not in _build_user_message(p, i, [])
    assert "pile of coins" in _build_user_message(p, 7, [])


def test_round_two_revisits_ask_for_a_clearer_angle():
    from finding_memeland.content.clue_engine import _build_user_message

    p = _persona("volt drizzle")
    p.clue_facet_plan = [
        "name_word_1", "avatar", "name_word_2",
        "avatar", "name_word_1", "name_word_2", "signature_post",
    ]
    assert "already hinted" not in _build_user_message(p, 1, [])
    assert "already hinted" in _build_user_message(p, 4, ["c1", "c2", "c3"])

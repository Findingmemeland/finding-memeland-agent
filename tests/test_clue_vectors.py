"""A rampa de pistas (Pedro, 2026-08-13 — substitui o plano de 29/07).

Para um nome de 2 palavras:
  1-3  (ordem ALEATÓRIA entre si): nome1 DIFÍCIL, nome2 DIFÍCIL, avatar ÓBVIO
       (a foto é o menos importante para a pesquisa → UMA pista, óbvia)
  4-5  nome1 FÁCIL, nome2 FÁCIL (ordem fixa)
  6-7-8  POSTS em escada: 6 mais difícil, 7 mais óbvia, 8 ainda mais fácil
       (locator primeiro; anchors entram quando existem)
  9+   HANDLE (hint do operador), quase explícito e cada vez mais claro
"""

from finding_memeland.content.clue_engine import (
    RAMP_AVATAR,
    RAMP_HANDLE_FLOOR,
    RAMP_HANDLE_START,
    RAMP_NAME_EASY,
    RAMP_NAME_HARD,
    RAMP_POSTS,
    PersonaContext,
    clue_plan,
    clue_slot_for,
    clue_vector_for,
    guidance_for,
    obliqueness_for,
    post_phase_start,
    ramp_plan,
)


def _persona(name, *, hint="", anchors=()):
    p = PersonaContext(
        display_name=name, handle="@ExpressoTitgo", bio="b",
        avatar_description="a", voice="v", backstory="bs",
        solution_terms=["secret"], banner_description="banner",
        findable_post="a very distinctive phrase here",
        handle_hint=hint,
    )
    p.clue_facet_plan = ramp_plan(name)
    p.anchor_posts = list(anchors)
    return p


# ---------------------------------------------------------------------------
# The head: clues 1-3, random order, difficulty tied to ROLE not position
# ---------------------------------------------------------------------------
def test_head_is_names_hard_plus_one_obvious_avatar_clue():
    plan = ramp_plan("Cassandra Tired")
    head, tail = plan[:3], plan[3:]
    assert sorted(head) == sorted([
        ("name_word_1", RAMP_NAME_HARD),
        ("name_word_2", RAMP_NAME_HARD),
        ("avatar", RAMP_AVATAR),
    ])
    assert tail == [
        ("name_word_1", RAMP_NAME_EASY),
        ("name_word_2", RAMP_NAME_EASY),
    ]


def test_head_order_actually_varies_across_hunts():
    heads = {tuple(ramp_plan("Cassandra Tired")[:3]) for _ in range(50)}
    assert len(heads) > 1


def test_avatar_gets_exactly_one_clue_and_it_is_obvious():
    plan = ramp_plan("Cassandra Tired")
    avatar = [(f, o) for f, o in plan if f == "avatar"]
    assert avatar == [("avatar", RAMP_AVATAR)]
    assert RAMP_AVATAR < RAMP_NAME_EASY < RAMP_NAME_HARD


def test_easy_name_revisits_are_fixed_order_after_the_head():
    for _ in range(20):
        plan = ramp_plan("Cassandra Tired")
        assert plan[3:] == [
            ("name_word_1", RAMP_NAME_EASY),
            ("name_word_2", RAMP_NAME_EASY),
        ]


def test_obliqueness_follows_the_role_not_the_position():
    """The whole point: the avatar clue is OBVIOUS even when it lands at
    clue 1; a hard name clue is hard even at clue 3."""
    p = _persona("Cassandra Tired")
    for i in (1, 2, 3):
        facet, obl = clue_slot_for(i, p)
        expected = RAMP_AVATAR if facet == "avatar" else RAMP_NAME_HARD
        assert obl == expected
        assert obliqueness_for(i, p) == expected


# ---------------------------------------------------------------------------
# The post ladder: clues 6-7-8, each step clearer
# ---------------------------------------------------------------------------
def test_post_ladder_positions_and_descending_difficulty():
    p = _persona("Cassandra Tired", anchors=["anchor one", "anchor two"])
    assert post_phase_start(p) == 6
    slots = [clue_slot_for(i, p) for i in (6, 7, 8)]
    assert [o for _, o in slots] == list(RAMP_POSTS)
    assert slots[0][0] == "signature_post"          # the locator opens the phase
    assert slots[1][0] == "anchor_post"
    assert slots[2][0] == "anchor_post"
    assert RAMP_POSTS[0] > RAMP_POSTS[1] > RAMP_POSTS[2]


def test_post_ladder_without_anchors_stays_on_the_locator():
    p = _persona("Cassandra Tired")
    assert [clue_vector_for(i, p) for i in (6, 7, 8)] == ["signature_post"] * 3
    # ...but still gets easier at each step
    assert [clue_slot_for(i, p)[1] for i in (6, 7, 8)] == list(RAMP_POSTS)


def test_clues_one_to_five_never_touch_posts_or_handle():
    p = _persona("Cassandra Tired", anchors=["a"], hint="h")
    for i in range(1, 6):
        assert clue_vector_for(i, p) in {"name_word_1", "name_word_2", "avatar"}


# ---------------------------------------------------------------------------
# The handle phase: clue 9+, near-explicit, clearer forever
# ---------------------------------------------------------------------------
def test_handle_phase_starts_at_nine_and_never_ends():
    p = _persona("Cassandra Tired", hint="expresso = small coffee")
    assert clue_vector_for(9, p) == "handle"
    assert clue_vector_for(15, p) == "handle"
    assert clue_slot_for(9, p)[1] == RAMP_HANDLE_START


def test_handle_clues_get_clearer_but_never_below_the_floor():
    p = _persona("Cassandra Tired", hint="h")
    obls = [clue_slot_for(i, p)[1] for i in range(9, 30)]
    assert all(a >= b for a, b in zip(obls, obls[1:]))
    assert min(obls) == RAMP_HANDLE_FLOOR


def test_handle_guidance_never_asks_to_write_the_handle():
    p = _persona("Cassandra Tired", hint="h")
    g = guidance_for("handle", p)
    assert "Never write the handle" in g


# ---------------------------------------------------------------------------
# Prompt need-to-know: anchors and hint only enter in their phases
# ---------------------------------------------------------------------------
def test_anchor_texts_hidden_from_prompt_before_the_post_phase():
    from finding_memeland.content.clue_engine import _build_user_message

    p = _persona("Cassandra Tired", anchors=["the pile of coins on a wet cloth"])
    for i in range(1, 6):
        assert "pile of coins" not in _build_user_message(p, i, [])
    assert "pile of coins" in _build_user_message(p, 6, [])


def test_handle_hint_only_enters_the_prompt_in_the_handle_phase():
    from finding_memeland.content.clue_engine import _build_user_message

    p = _persona("Cassandra Tired", hint="expresso = um cafe pequeno")
    for i in range(1, 9):
        assert "um cafe pequeno" not in _build_user_message(p, i, [])
    assert "um cafe pequeno" in _build_user_message(p, 9, [])


def test_missing_hint_degrades_to_the_handle_itself():
    from finding_memeland.content.clue_engine import _build_user_message

    p = _persona("Cassandra Tired", hint="")
    assert "derive oblique hints from the handle itself" in _build_user_message(p, 9, [])


def test_easy_name_revisit_asks_for_a_clearer_angle():
    from finding_memeland.content.clue_engine import _build_user_message

    p = _persona("Cassandra Tired")
    # positions 4-5 are always the easy name revisits
    assert "already hinted" in _build_user_message(p, 4, ["c1", "c2", "c3"])
    assert "already hinted" not in _build_user_message(p, 1, [])


def test_second_post_and_handle_clues_are_marked_as_revisits():
    from finding_memeland.content.clue_engine import _build_user_message

    p = _persona("Cassandra Tired", anchors=["anchor"], hint="h")
    assert "already hinted" not in _build_user_message(p, 6, [])   # phase opener
    assert "already hinted" in _build_user_message(p, 7, [])
    assert "already hinted" not in _build_user_message(p, 9, [])   # first handle clue
    assert "already hinted" in _build_user_message(p, 10, [])


# ---------------------------------------------------------------------------
# Name lengths + fallback plan
# ---------------------------------------------------------------------------
def test_single_word_name_compresses_the_ramp():
    p = _persona("icarus")
    assert post_phase_start(p) == 4                  # 2 head + 1 easy revisit
    assert clue_vector_for(4, p) == "signature_post"
    assert clue_vector_for(7, p) == "handle"


def test_three_word_name_stretches_the_ramp():
    p = _persona("Arachne Spinnerette Weaver")
    assert post_phase_start(p) == 8                  # 4 head + 3 easy revisits
    assert clue_vector_for(11, p) == "handle"


def test_guidance_resolves_each_name_word_to_the_right_word():
    p = _persona("Arachne Spinnerette Weaver")
    assert "Arachne" in guidance_for("name_word_1", p)
    assert "Spinnerette" in guidance_for("name_word_2", p)
    assert "Weaver" in guidance_for("name_word_3", p)


def test_deterministic_fallback_plan_matches_the_ramp_shape():
    p = _persona("Cassandra Tired")
    p.clue_facet_plan = []                           # force the fallback
    assert clue_plan(p) == [
        ("name_word_1", RAMP_NAME_HARD),
        ("name_word_2", RAMP_NAME_HARD),
        ("avatar", RAMP_AVATAR),
        ("name_word_1", RAMP_NAME_EASY),
        ("name_word_2", RAMP_NAME_EASY),
    ]
    assert clue_vector_for(6, p) == "signature_post"


def test_every_planned_facet_has_resolvable_guidance():
    p = _persona("Arachne Spinnerette Weaver")
    for facet, _ in p.clue_facet_plan:
        assert guidance_for(facet, p)
    for facet in ("signature_post", "anchor_post", "handle"):
        assert guidance_for(facet, p)


def test_banner_and_bio_are_out_of_the_game():
    plan = ramp_plan("Cassandra Tired")
    facets = {f for f, _ in plan}
    assert "banner" not in facets and "bio" not in facets


# ---------------------------------------------------------------------------
# Opus (revisão Fase 4): a pista 1 nunca é a foto — abre sempre num nome
# difícil; a foto varia entre a 2 e a 3 (a variedade não morre)
# ---------------------------------------------------------------------------
def test_clue_one_is_never_the_avatar():
    for _ in range(300):
        plan = ramp_plan("Cassandra Tired")
        assert plan[0][0].startswith("name_word_")
        assert plan[0][1] == RAMP_NAME_HARD


def test_avatar_still_varies_between_positions_two_and_three():
    positions = {  # 0-based index where the avatar landed
        next(i for i, (f, _) in enumerate(ramp_plan("Cassandra Tired")) if f == "avatar")
        for _ in range(300)
    }
    assert positions == {1, 2}          # both slots occur; never slot 0


def test_head_still_contains_all_three_roles_after_the_swap():
    for _ in range(100):
        head = ramp_plan("Cassandra Tired")[:3]
        assert sorted(head) == sorted([
            ("name_word_1", RAMP_NAME_HARD),
            ("name_word_2", RAMP_NAME_HARD),
            ("avatar", RAMP_AVATAR),
        ])


# ---------------------------------------------------------------------------
# Segurança pré-deploy (Opus): sub-tokens do handle e partes do hint banidos
# ---------------------------------------------------------------------------
def test_hint_terms_extracts_the_left_hand_parts():
    from finding_memeland.content.clue_engine import hint_terms

    hint = ("expresso = um cafe pequeno; tit = mothers have two, this is only "
            "one; go = what you say after ready, set")
    assert hint_terms(hint) == ["expresso", "tit"]   # 'go' < 3 chars: fora
    assert hint_terms("") == []
    assert hint_terms("just prose without separators") == []


# ---------------------------------------------------------------------------
# Hard-clue calibration (Cassandra dry run, 16/08): the 0.9 clues came out as
# synonym lists / story summaries. Hard slots get an explicit one-angle rule.
# ---------------------------------------------------------------------------
def test_hard_name_slots_carry_the_one_angle_rule_and_easy_ones_do_not():
    from finding_memeland.content.clue_engine import (
        HARD_CLUE_FLOOR, RAMP_NAME_HARD, RAMP_NAME_EASY, RAMP_AVATAR,
        _build_user_message, clue_slot_for,
    )

    p = _persona("Cassandra Tired")
    assert RAMP_NAME_HARD >= HARD_CLUE_FLOOR > RAMP_NAME_EASY > RAMP_AVATAR
    seen_hard = seen_easy = 0
    for i in range(1, 11):
        _, ob = clue_slot_for(i, p)
        msg = _build_user_message(p, i, [])
        if ob >= HARD_CLUE_FLOOR:
            seen_hard += 1
            assert "HARD CLUE: one oblique angle only" in msg
        else:
            seen_easy += 1
            assert "HARD CLUE" not in msg
    # exactly the two hard name passes; the avatar/easy/post/handle slots are not
    assert seen_hard == 2 and seen_easy == 8


def test_system_prompt_formats_with_hard_floor_and_clue_count_for_taunts():
    from finding_memeland.content.clue_engine import HARD_CLUE_FLOOR, SYSTEM_PROMPT

    s = SYSTEM_PROMPT.format(index=7, obliqueness=0.3, hard_floor=HARD_CLUE_FLOOR)
    assert "obliqueness 0.8 or higher" in s
    assert "RECOGNITION vs INFERENCE" in s                # the Opus test
    assert "she told them and they laughed" in s          # closed/dry example
    assert "nobody would've aped" in s                    # crypto-native example
    assert "the number is exactly 7" in s                  # taunt off-by-one guard
    assert '{"clue": "...", "taunt": "..."}' in s          # JSON contract intact


def test_clue_prompt_never_sends_players_to_dms_and_bans_lookups_in_hard_clues():
    """Hunt 6 dry run (16/08): the clue prompt still said players win by 'DMing'
    the persona — pre claim-by-post wording that surfaced as 'still not in their
    DMs?' in a handle clue. The oracle never reads DMs: a clue that says DM sends
    a player through the wrong door."""
    from finding_memeland.content.clue_engine import HARD_CLUE_FLOOR, SYSTEM_PROMPT

    s = SYSTEM_PROMPT.format(index=9, obliqueness=0.1, hard_floor=HARD_CLUE_FLOOR)
    assert "DMing" not in s
    assert "There are NO DMs in this game" in s
    assert "REPLY to the Clue 1 post" in s
    # hard clues: etymology / meaning is a lookup, not a hard clue
    assert "NEVER an etymology" in s
    # counts and jabs live in the taunt, never at the head of the clue text
    assert "the clue text never opens with" in s


# ---------------------------------------------------------------------------
# Deterministic clean-up of leading clue counts / "final clue" tags
# ---------------------------------------------------------------------------
def test_leading_clue_count_is_stripped_from_the_clue_text_only():
    from finding_memeland.content.clue_engine import _parse_clue

    raw = ('{"clue": "five clues deep and you still need help with a single word? '
           'how are you feeling right now? that is the second word.", '
           '"taunt": "five clues out. FIVE."}')
    d = _parse_clue(raw)
    assert d.text.startswith("how are you feeling right now?")
    assert "five clues" not in d.text
    assert d.taunt == "five clues out. FIVE."          # the taunt keeps its count

    # digits, other verbs, and a leading "ok"/"fine" are handled too
    for lead in ("8 clues and you're basically being handed the W. ",
                 "two clues in and still lurking? ",
                 "ok 9 clues and you're still not typing? ",
                 "Still here after 7 clues? "):
        d = _parse_clue('{"clue": "' + lead + 'search the phrase.", "taunt": ""}')
        assert d.text == "search the phrase.", (lead, d.text)


def test_final_clue_tag_is_stripped_and_prompt_forbids_it():
    from finding_memeland.content.clue_engine import (
        HARD_CLUE_FLOOR, SYSTEM_PROMPT, _parse_clue,
    )

    for lead in ("final clue. ", "Final clue: ", "last one — ", "ok, final clue. ",
                 "final answer time. "):
        d = _parse_clue('{"clue": "' + lead + 'the @ has three parts.", "taunt": ""}')
        assert d.text == "the @ has three parts.", (lead, d.text)
    s = SYSTEM_PROMPT.format(index=9, obliqueness=0.1, hard_floor=HARD_CLUE_FLOOR)
    assert 'Never call a clue "final" or "last"' in s


def test_meta_stripping_never_empties_or_touches_real_clues():
    from finding_memeland.content.clue_engine import _parse_clue

    # a clue that IS only a count sentence stays (never return empty)
    d = _parse_clue('{"clue": "two clues in and still lurking?", "taunt": "x"}')
    assert d.text == "two clues in and still lurking?"
    # numbers/'clue' mid-sentence, or 'last' as an ordinary word, are untouched
    for clue in ("she told them and they laughed.",
                 "the horse is hollow. I said it three times.",
                 "the last thing anyone believed. one word.",
                 "10 out of 10 would warn again.",
                 "one clue away from glory: search the phrase."):
        d = _parse_clue('{"clue": "' + clue.replace('"', '\\"') + '", "taunt": ""}')
        assert d.text == clue, clue

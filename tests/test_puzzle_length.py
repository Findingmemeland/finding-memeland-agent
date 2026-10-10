"""O puzzle tem o tamanho que o nome pede — já não são sete pistas fixas.

10/10, regra do Pedro: "antes de a dificuldade baixar, o bot publica SEMPRE
pelo menos 2 pistas por palavra de conteúdo e 2 da imagem. O puzzle passa a
ter 2 × (palavras de conteúdo) + 2 pistas — 4, 6 ou 8 — em vez de 7 fixas.
Nenhuma pista é suprimida para dar lugar a outra."

As sete fixas faziam as duas coisas que a regra proíbe: com três palavras a
arte perdia uma das suas duas peças, e com duas palavras uma delas levava uma
terceira peça que a outra nunca tinha.

O que isto fixa:

  · o plano de uma hunt de ALVO: 2 peças por palavra de conteúdo + 2 de arte;
  · a curva de dificuldade é a mesma (0,9 a 0,65), esticada sobre as peças
    que houver;
  · a fronteira puzzle/reveal é a do plano DESTA hunt — e com ela andam as
    regras de pista difícil, o adivinho cego, a guarda de pesquisa, o
    anti-spray, a linha das palavras pequenas e o que acontece se o alvo
    mudar a meio (relançar no puzzle, void com reveal depois);
  · um nome com UMA palavra de conteúdo continua aceite (4 pistas);
  · as RELICS ficam nas 7;
  · o "worst case" do /status é o plano maior possível — 8 pistas de puzzle
    e a rampa de reveal inteira — ao intervalo máximo, e já não "10 pistas".

Todos os nomes aqui são sintéticos.
"""
from __future__ import annotations

import inspect
import random

import pytest
from test_target_clues import FakeAnthropic, FakeSearch, FakeTruthJudge

from finding_memeland import main
from finding_memeland.content.clue_engine import ASSUMED_MAX_CLUES, worst_case_hunt_hours
from finding_memeland.content.name_words import content_words
from finding_memeland.content.relic_clues import (
    MAX_NAME_WORDS,
    PUZZLE_ANGLES,
    PUZZLE_CLUES,
    PUZZLE_OBLIQUENESS,
    PUZZLE_PHASE_RULES,
    REVEAL_FLOOR,
    REVEAL_PHASE_RULES,
    REVEAL_START,
    RelicClueContext,
    RelicClueEngine,
    _placed,
    angle_for,
    angle_for_unverifiable,
    longest_target_hunt_clues,
    puzzle_clues_of,
    relic_ramp_plan,
    relic_slot_for,
    reveal_clues_to_floor,
    stretched_obliqueness,
    target_ramp_plan,
)
from finding_memeland.runtime import cadence_status_line
from finding_memeland.target import dryrun
from finding_memeland.target.clues import (
    TargetClueContext,
    TargetClueEngine,
    build_target_user_message,
    declaration_errors,
    small_words_for,
)
from finding_memeland.target.dryrun import TargetWorld
from finding_memeland.target.hunt import (
    ACT_RELAUNCH,
    ACT_VOID_REVEAL,
    LIVE_MUTATED,
    PHASE_PUZZLE,
    PHASE_REVEAL,
    SprayParams,
    live_policy,
)
from finding_memeland.target.integration import phase_for, spray_check
from finding_memeland.target.search_guard import ClueSearchGuard
from finding_memeland.target.selector import Target

ONE = "The Doge"                                  # 1 content word
TWO = "Salt Harbor"                               # 2
TWO_SMALL = "Portrait of a Lady"                  # 2, with small words
THREE = "The Garden of Earthly Delights"          # 3
NAMES = (ONE, TWO, TWO_SMALL, THREE)
ITEM_ID = "ETHEREUM:0x3b3ee1931dc30c1957379fac9aba94d1c48a5405:41234"


def _target(name: str) -> Target:
    return Target(chain="ethereum", contract="0x3b3ee1931dc30c1957379fac9aba94d1c48a5405",
                  token_id=41234, name=name, name_onchain=name, description="",
                  image="ipfs://QmImage", metadata_sha256="ab" * 32, epoch="e1")


def ctx(name: str) -> TargetClueContext:
    return TargetClueContext.from_target(_target(name), image_description="a lighthouse")


def relic_ctx(name: str = "Uncle Pump") -> RelicClueContext:
    return RelicClueContext(
        display_name=name, image_description="", lore="", backstory="",
        clue_facet_plan=relic_ramp_plan(name),
        angle_offset=sum(ord(c) for c in name) % len(PUZZLE_ANGLES))


def pieces(plan) -> dict[str, int]:
    out: dict[str, int] = {}
    for facet, _obl in plan:
        out[facet] = out.get(facet, 0) + 1
    return out


# --------------------------------------------------------------------------- #
# 1. O plano: 2 por palavra de conteúdo + 2 de arte                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name, clues", [(ONE, 4), (TWO, 6), (TWO_SMALL, 6), (THREE, 8)])
def test_the_puzzle_is_two_pieces_a_content_word_plus_two_for_the_artwork(name, clues):
    plan = target_ramp_plan(name)
    words = content_words(name)
    assert len(plan) == clues == 2 * len(words) + 2
    got = pieces(plan)
    assert got.pop("image") == 2
    assert got == {f"name_word_{w.position}": 2 for w in words}      # nobody gets a third
    assert puzzle_clues_of(ctx(name)) == clues


def test_no_piece_is_dropped_to_make_room_for_another():
    """Com três palavras, as sete fixas davam UMA peça à arte; com duas,
    davam uma terceira a uma das palavras."""
    assert pieces(relic_ramp_plan(THREE))["image"] == 1               # what seven clues did
    assert pieces(target_ramp_plan(THREE))["image"] == 2
    assert sorted(pieces(relic_ramp_plan(TWO)).values()) == [2, 2, 3]
    assert sorted(pieces(target_ramp_plan(TWO)).values()) == [2, 2, 2]


def test_the_order_keeps_its_two_rules_and_is_the_same_every_time():
    rng = random.Random(3)
    for _ in range(400):
        n = rng.randint(1, 3)
        name = " ".join("".join(rng.choice("abcdefghijklmnopqrstuvwxyz")
                                for _ in range(rng.randint(4, 9))) for _ in range(n))
        plan = target_ramp_plan(name)
        facets = [f for f, _ in plan]
        assert len(plan) == 2 * n + 2
        assert facets[0] != "image"                                    # clue 1: a name piece
        assert not any(a == b == "image" for a, b in zip(facets, facets[1:], strict=False))
        assert plan == target_ramp_plan(name)                          # seeded by the name


def test_a_one_word_puzzle_has_exactly_one_legal_order():
    assert [f for f, _ in target_ramp_plan(ONE)] == [
        "name_word_2", "image", "name_word_2", "image"]
    assert [f for f, _ in target_ramp_plan("Lantern")] == [
        "name_word_1", "image", "name_word_1", "image"]


def test_a_run_of_bad_shuffles_lays_the_pieces_out_by_hand():
    class NeverLegal(random.Random):
        def shuffle(self, x):                     # always art first: never legal
            x.sort(key=lambda s: s != "image")

    assert _placed(["w", "w", "image", "image"], NeverLegal()) == ["w", "image", "w", "image"]
    assert _placed(["a", "a", "b", "b", "image", "image"], NeverLegal()) == [
        "a", "image", "a", "b", "b", "image"]


def test_three_content_words_is_still_the_most_a_plan_holds():
    assert MAX_NAME_WORDS == 3
    with pytest.raises(ValueError) as e:
        target_ramp_plan("Quiet Lantern Above Water")
    assert "4 content words" in str(e.value) and "Lantern" not in str(e.value)


# --------------------------------------------------------------------------- #
# 2. A curva: a mesma, esticada                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("n, curve", [
    (4, (0.9, 0.85, 0.75, 0.65)),
    (6, (0.9, 0.89, 0.83, 0.77, 0.71, 0.65)),
    (8, (0.9, 0.9, 0.86, 0.82, 0.78, 0.74, 0.69, 0.65)),
])
def test_the_curve_is_the_same_one_stretched(n, curve):
    got = stretched_obliqueness(n)
    assert got == curve
    assert (got[0], got[-1]) == (PUZZLE_OBLIQUENESS[0], PUZZLE_OBLIQUENESS[-1]) == (0.9, 0.65)
    assert all(a >= b for a, b in zip(got, got[1:], strict=False))     # never back up


def test_seven_pieces_give_the_curve_itself():
    assert stretched_obliqueness(7) == PUZZLE_OBLIQUENESS
    assert stretched_obliqueness(1) == (0.9,) and stretched_obliqueness(0) == ()


@pytest.mark.parametrize("name", NAMES)
def test_every_piece_takes_the_curve_by_its_position(name):
    plan = target_ramp_plan(name)
    assert [obl for _f, obl in plan] == list(stretched_obliqueness(len(plan)))
    c = ctx(name)
    assert [relic_slot_for(i, c)[1] for i in range(1, len(plan) + 1)] == [o for _f, o in plan]


# --------------------------------------------------------------------------- #
# 3. A fronteira puzzle / reveal é a do plano desta hunt                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", NAMES)
def test_the_reveal_starts_right_after_the_last_piece(name):
    c = ctx(name)
    n = puzzle_clues_of(c)
    assert relic_slot_for(n, c)[1] == 0.65                             # the last hard piece
    facet, obl = relic_slot_for(n + 1, c)
    assert obl == REVEAL_START and facet.startswith("name_word_")
    assert phase_for(n, c) == PHASE_PUZZLE and phase_for(n + 1, c) == PHASE_REVEAL
    assert angle_for_unverifiable(n + 1, c) is None                    # no angle after the puzzle


def test_the_phase_without_a_plan_is_the_fixed_seven():
    assert puzzle_clues_of(None) == PUZZLE_CLUES == 7
    assert phase_for(7) == PHASE_PUZZLE and phase_for(8) == PHASE_REVEAL


@pytest.mark.parametrize("name", NAMES)
def test_the_writer_is_told_the_piece_and_how_many_there_are(name):
    c = ctx(name)
    n = puzzle_clues_of(c)
    for i in range(1, n + 1):
        assert f"PUZZLE PIECE {i} of {n}:" in build_target_user_message(c, i, ["x"] * (i - 1))
    assert "PUZZLE PIECE" not in build_target_user_message(c, n + 1, ["x"] * n)


def _engine(drafts, *, guard=None, judge=None):
    if guard is None:
        guard = ClueSearchGuard(search=FakeSearch({}), retries=0, sleep_s=0.0)
    return TargetClueEngine(FakeAnthropic(drafts), "model", search_guard=guard,
                            truth_judge=judge or FakeTruthJudge(), solver=False)


@pytest.mark.parametrize("name", NAMES)
def test_the_phase_rules_change_with_the_plan_not_at_clue_eight(name):
    c = ctx(name)
    n = puzzle_clues_of(c)
    e = _engine(["a clue that names nothing"] * 4, guard=False)
    e.next_clue(c, n, ["x"] * (n - 1))
    assert PUZZLE_PHASE_RULES in e._client.calls[-1]["system"]
    e.next_clue(c, n + 1, ["x"] * n)
    assert REVEAL_PHASE_RULES in e._client.calls[-1]["system"]
    assert e._guardrail_kwargs(c, n)["puzzle_phase"] is True
    assert e._guardrail_kwargs(c, n + 1)["puzzle_phase"] is False


@pytest.mark.parametrize("name", [ONE, TWO, THREE])
def test_the_search_guard_reads_every_puzzle_piece_and_no_reveal_clue(name):
    """Uma pista que É uma pesquisa: recusada até à última peça do puzzle,
    e deixada passar na primeira do reveal — onde quer que ela caia."""
    c = ctx(name)
    n = puzzle_clues_of(c)
    hits = {name.lower(): {ITEM_ID}, "a lighthouse guards it": {ITEM_ID}}

    def guard():
        return ClueSearchGuard(search=FakeSearch(hits), retries=0, sleep_s=0.0)

    last = _engine(["a lighthouse guards it", "nothing a search would find"], guard=guard())
    assert last.next_clue(c, n, ["x"] * (n - 1)).text == "nothing a search would find"
    first_reveal = _engine(["a lighthouse guards it"], guard=guard())
    assert first_reveal.next_clue(c, n + 1, ["x"] * n).text == "a lighthouse guards it"


@pytest.mark.parametrize("name", [ONE, TWO, THREE])
def test_the_blind_solver_reads_every_puzzle_piece_and_no_reveal_clue(name):
    c = ctx(name)
    n = puzzle_clues_of(c)
    asked: list[int] = []

    class Solver:
        name = "fake"

        def guess(self, clues, word_count):
            asked.append(len(clues))
            return []

    e = TargetClueEngine(FakeAnthropic(["a clue that names nothing"] * 4), "model",
                         search_guard=False, truth_judge=FakeTruthJudge(), solver=Solver())
    e.next_clue(c, n, ["x"] * (n - 1))
    assert asked == [1, n]                          # alone, then with the earlier ones
    e.next_clue(c, n + 1, ["x"] * n)
    assert asked == [1, n]                          # the reveal is not the solver's


@pytest.mark.parametrize("name", NAMES)
def test_a_structure_piece_must_declare_its_claim_only_while_it_is_a_piece(name):
    c = ctx(name)
    n = puzzle_clues_of(c)

    class Draft:
        text, taunt, angle, image_aspect, claims = "a clue", "", None, None, []

    assert any("ASSIGNED" in e or "image_aspect" in e
               for i in range(1, n + 1) for e in declaration_errors(Draft(), c, i))
    assert declaration_errors(Draft(), c, n + 1) == []                 # reveal: nothing assigned


@pytest.mark.parametrize("name", NAMES)
def test_a_list_is_refused_in_a_piece_and_allowed_once_the_puzzle_is_over(name):
    """"a, b, and c" lê-se como lista de sinónimos — proibida numa peça do
    puzzle, permitida no reveal, que começa onde o plano DESTA hunt acaba."""
    c = ctx(name)
    n = puzzle_clues_of(c)

    class Listing:
        text, taunt, image_aspect, claims = "red, green, and blue", "", None, []
        angle = None

    piece = next(i for i in range(1, n + 1) if relic_slot_for(i, c)[0] != "image")
    assert any("enumerates a list" in e for e in declaration_errors(Listing(), c, piece))
    assert declaration_errors(Listing(), c, n + 1) == []


@pytest.mark.parametrize("name", NAMES)
def test_the_wording_of_a_name_clue_changes_where_the_puzzle_ends(name):
    c = ctx(name)
    n = puzzle_clues_of(c)
    piece = next(i for i in range(1, n + 1) if relic_slot_for(i, c)[0] != "image")
    hard = build_target_user_message(c, piece, ["x"] * (piece - 1))
    assert "ONE constraint on THAT EXACT word" in hard and "a synonym, a rhyme" not in hard
    plain = build_target_user_message(c, n + 1, ["x"] * n)
    assert "hint at THAT EXACT word (its meaning, a synonym, a rhyme" in plain
    assert "ONE constraint on THAT EXACT word" not in plain


def test_the_small_words_line_goes_out_with_the_first_reveal_clue_whatever_its_number():
    for name, first in ((TWO_SMALL, 7), (THREE, 9), (ONE, 5)):
        c = ctx(name)
        assert [i for i in range(1, 20) if small_words_for(c, i)] == [first]


# --------------------------------------------------------------------------- #
# 4. O que acontece ao jogo com a fronteira                                     #
# --------------------------------------------------------------------------- #


def test_a_mutation_relaunches_during_the_puzzle_and_voids_after_it():
    for name in NAMES:
        c = ctx(name)
        n = puzzle_clues_of(c)
        assert live_policy(LIVE_MUTATED, phase=phase_for(n, c)) == ACT_RELAUNCH
        assert live_policy(LIVE_MUTATED, phase=phase_for(n + 1, c)) == ACT_VOID_REVEAL


def _hunt_until(monkeypatch, base: str, clues: int):
    monkeypatch.setattr(dryrun, "POOL_NAME", base)
    w = TargetWorld()
    hunt = w.launch()
    w.orch._clue_due_fn = lambda now: now
    index = 1
    for _ in range(clues - 1):
        w.rig.clock.sleep(60)
        index, _due = w.orch._maybe_post_clue(hunt, index, w.rig.clock.now())
    assert index == clues
    return w, hunt, index


def _mutate(w, hunt) -> None:
    t = hunt.target.target
    w.live[(t.chain, t.contract, t.token_id)] = (
        "ipfs://Qm" + "9" * 44 + "/metadata.json", "0xowner")


@pytest.mark.parametrize("posted, relaunch", [(5, True), (6, False)])
def test_a_live_hunt_relaunches_or_voids_by_its_own_plan(monkeypatch, posted, relaunch):
    """Dois de conteúdo → seis pistas de puzzle. O alvo muda antes da 6.ª:
    relança-se. Muda antes da 7.ª (já reveal): void, o prémio volta ao cofre.
    Com as sete fixas, a 7.ª ainda relançava."""
    w, hunt, index = _hunt_until(monkeypatch, "Whispering Harbor", posted)
    assert puzzle_clues_of(hunt.ctx) == 6
    _mutate(w, hunt)
    w.rig.clock.sleep(60)
    w.orch._maybe_post_clue(hunt, index, w.rig.clock.now())
    (void,) = [m for m in w.notices() if " VOID (" in m]
    assert ("relaunch with /launch" in void) is relaunch
    assert ("prize back to the vault" in void) is not relaunch


@pytest.mark.parametrize("name, puzzle", [(ONE, 4), (TWO, 6), (THREE, 8)])
def test_the_anti_spray_detector_watches_the_puzzle_and_only_the_puzzle(name, puzzle):
    w = TargetWorld(live_params=SprayParams(min_total_guesses=5, min_distinct_ratio=0.9))

    class Hunt:
        number = 1

    Hunt.ctx = ctx(name)
    log = [(f"user{i}", f"ethereum:0x{i:040x}:1") for i in range(10)]
    assert w.ports.spray.evaluate(log).triggered                       # it would fire
    after: dict = {}
    spray_check(w.orch, Hunt, puzzle + 1, log, after)
    assert not after.get("fired")                                      # reveal: not watched
    during: dict = {}
    spray_check(w.orch, Hunt, puzzle, log, during)
    assert during.get("fired") is True


# --------------------------------------------------------------------------- #
# 5. Uma palavra de conteúdo                                                    #
# --------------------------------------------------------------------------- #


def test_a_name_with_one_content_word_is_still_a_hunt():
    c = ctx(ONE)
    assert puzzle_clues_of(c) == 4
    msg = build_target_user_message(c, 1, [])
    assert "- name (2 words): The Doge\n" in msg and "SMALL WORDS: word 1 ('The')" in msg
    assert "the 2nd word of the treasure's NAME (the word 'Doge')" in msg


@pytest.mark.parametrize("name", [ONE, "Lantern", "A Lighthouse", "The Garden of the"])
def test_with_one_content_word_no_piece_is_about_its_relation_to_another(name):
    """RELATION é "como esta palavra se encosta à OUTRA" — não há outra, e
    com duas peças de nome não se gasta uma nisso."""
    c = ctx(name)
    angles = [angle_for_unverifiable(i, c) for i in range(1, puzzle_clues_of(c) + 1)
              if relic_slot_for(i, c)[0] != "image"]
    assert len(angles) == 2 and len(set(angles)) == 2
    assert not any(a.startswith("RELATION") for a in angles)


def test_with_two_words_or_three_the_relation_is_still_an_angle():
    seen = set()
    rng = random.Random(9)
    for _ in range(200):
        name = " ".join("".join(rng.choice("abcdefghijklmnopqrstuvwxyz")
                                for _ in range(6)) for _ in range(rng.choice((2, 3))))
        c = ctx(name)
        used: dict[str, list[str]] = {}
        for i in range(1, puzzle_clues_of(c) + 1):
            facet = relic_slot_for(i, c)[0]
            if facet == "image":
                continue
            angle = angle_for_unverifiable(i, c).split(":")[0]
            assert angle not in used.get(facet, [])                    # never twice on a word
            used.setdefault(facet, []).append(angle)
            seen.add(angle)
    assert "RELATION" in seen and len(seen) == 4


# --------------------------------------------------------------------------- #
# 6. As relics ficam nas 7                                                      #
# --------------------------------------------------------------------------- #


def test_a_relic_keeps_its_seven_pieces_and_its_curve():
    for name in ("Uncle Pump", "Lantern", "Quiet Lantern Above"):
        plan = relic_ramp_plan(name)
        assert len(plan) == PUZZLE_CLUES == 7
        assert [obl for _f, obl in plan] == list(PUZZLE_OBLIQUENESS)
        assert puzzle_clues_of(relic_ctx(name)) == 7
    assert sorted(pieces(relic_ramp_plan("Uncle Pump")).values()) == [2, 2, 3]


def test_a_relic_built_from_its_identity_gets_the_seven_piece_plan():
    class Identity:
        name = "Uncle Pump"
        image_prompt = "a pump"
        description = "lore"
        solution_terms = ["uncle", "pump"]

    c = RelicClueContext.from_identity(Identity())
    assert c.clue_facet_plan == relic_ramp_plan("Uncle Pump")
    assert len(c.clue_facet_plan) == 7 and puzzle_clues_of(c) == 7
    assert sorted(pieces(c.clue_facet_plan).values()) == [2, 2, 3]


def test_a_relic_s_phases_are_where_they_were():
    c = relic_ctx()
    e = RelicClueEngine(FakeAnthropic([]), "model", solver=False)
    assert e._guardrail_kwargs(c, 7) == {"puzzle_phase": True}
    assert e._guardrail_kwargs(c, 8) == {"puzzle_phase": False}
    assert phase_for(7, c) == PHASE_PUZZLE and phase_for(8, c) == PHASE_REVEAL
    assert angle_for_unverifiable(7, c) is not None or relic_slot_for(7, c)[0] == "image"
    assert angle_for_unverifiable(8, c) is None


@pytest.mark.parametrize("name, direct, anchored", [
    ("Lantern",
     ["CULTURAL USE", "RELATION", "SEMANTIC FIELD", "-", "STRUCTURE", "RELATION", "-"],
     ["STRUCTURE", "CONCRETE ANCHOR", "SEMANTIC FIELD", "-", "RELATION", "CULTURAL USE", "-"]),
    ("Harbor",
     ["STRUCTURE", "CULTURAL USE", "-", "RELATION", "SEMANTIC FIELD", "-", "STRUCTURE"],
     ["SEMANTIC FIELD", "CULTURAL USE", "-", "CONCRETE ANCHOR", "RELATION", "-", "STRUCTURE"]),
    ("Uncle Pump",
     ["CULTURAL USE", "RELATION", "SEMANTIC FIELD", "-", "CULTURAL USE", "RELATION", "-"],
     ["CULTURAL USE", "RELATION", "STRUCTURE", "-", "CULTURAL USE", "RELATION", "-"]),
    ("Quiet Lantern Above",
     ["CULTURAL USE", "SEMANTIC FIELD", "STRUCTURE", "-", "CULTURAL USE", "STRUCTURE",
      "CULTURAL USE"],
     ["CULTURAL USE", "RELATION", "SEMANTIC FIELD", "-", "CULTURAL USE", "SEMANTIC FIELD",
      "RELATION"]),
])
def test_a_relic_s_angles_are_exactly_what_they_were(name, direct, anchored):
    """Medidos no código de antes desta mudança (10/10), peça por peça —
    com uma, duas e três palavras, com e sem o ângulo da âncora."""
    c = relic_ctx(name)
    assert [(angle_for_unverifiable(i, c) or "-").split(":")[0] for i in range(1, 8)] == direct
    assert [(angle_for(i, c) or "-").split(":")[0] for i in range(1, 8)] == anchored


def test_a_one_word_relic_still_falls_back_on_relation_as_it_did():
    """Cinco peças de nome e só três outros ângulos: a RELATION continua a
    ser usada — as relics não mudam."""
    c = relic_ctx("Lantern")
    angles = [angle_for_unverifiable(i, c).split(":")[0] for i in range(1, 8)
              if relic_slot_for(i, c)[0] != "image"]
    assert len(angles) == 5 and "RELATION" in angles


# --------------------------------------------------------------------------- #
# 7. O "worst case" do /status vem do plano maior possível                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", NAMES)
def test_the_reveal_is_as_plain_as_it_gets_at_its_ninth_clue(name):
    """Oito degraus de 0,4 a 0,05 nas pistas de nome, e a descrição simples
    da arte pelo meio: à 9.ª pista de reveal chega-se ao chão, e não sai de
    lá."""
    assert reveal_clues_to_floor() == 9
    c = ctx(name)
    n = puzzle_clues_of(c)
    slots = [relic_slot_for(n + k, c) for k in range(1, 15)]
    assert [f for f, _o in slots[:9]].count("image") == 1
    assert all(o > REVEAL_FLOOR for f, o in slots[:8] if f != "image")
    assert all(o == REVEAL_FLOOR for _f, o in slots[8:])


def test_the_longest_hunt_is_the_biggest_puzzle_and_the_whole_reveal():
    assert longest_target_hunt_clues() == 17 == 8 + 9
    biggest = max(len(target_ramp_plan(n)) for n in NAMES)
    assert longest_target_hunt_clues() == biggest + reveal_clues_to_floor()
    assert biggest == 2 * MAX_NAME_WORDS + 2


def test_the_status_line_takes_its_worst_case_from_the_plan():
    """Em produção as pistas saem a 6–26 min: 17 pistas a 26 min são 7,4 h.
    As dez de antes davam 4,3 h — e com três palavras o reveal só começa na
    9.ª pista."""
    assert cadence_status_line(360, 1560, 24) == (
        "clues: 6-26min → worst case 7.4h (17 clues) ✅ (< 24h)")
    assert ASSUMED_MAX_CLUES == 10
    assert worst_case_hunt_hours(1560) == pytest.approx(4.33, abs=0.01)
    assert worst_case_hunt_hours(1560, longest_target_hunt_clues()) == pytest.approx(7.37, abs=0.01)


def test_the_status_line_still_says_when_the_window_does_not_cover_the_hunt():
    line = cadence_status_line(3600, 10800, 24)
    assert line == ("clues: 60-180min → worst case 51.0h (17 clues) "
                    "❌ EXCEEDS the 24h window — a mid-hunt buyer could win")
    # equal is not enough: 17 clues at one hour is 17 h — the window must be longer
    assert "❌" in cadence_status_line(60, 3600, 17)
    assert "✅ (< 18h)" in cadence_status_line(60, 3600, 18)


def test_status_builds_the_line_through_the_helper():
    src = inspect.getsource(main)
    assert "cadence_status_line(" in src
    assert "worst_case_hunt_hours" not in src            # no second copy of the sum

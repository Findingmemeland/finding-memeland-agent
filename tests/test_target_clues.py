"""Target clue engine — context without the address, prompt without the
chain, forbidden chain/platform words, search guard in the loop (fail-closed
when unverifiable), batched vision fetch."""

from __future__ import annotations

import json
import random
import re

import pytest

from finding_memeland.content.relic_clues import PUZZLE_CLUES
from finding_memeland.target.clues import (
    TARGET_SYSTEM_PROMPT,
    ImageUnavailable,
    SearchGuardUnavailable,
    TargetClueContext,
    TargetClueEngine,
    build_target_user_message,
    describe_image_batched,
    forbidden_address_words,
)
from finding_memeland.target.search_guard import ClueSearchGuard, FakeSearch
from finding_memeland.target.selector import Target

TARGET = Target(chain="ethereum", contract="0x3b3ee1931dc30c1957379fac9aba94d1c48a5405",
                token_id=41234, name="Salt Harbor",
                name_onchain="Salt Harbor #3", description="a quiet place by the sea",
                image="ipfs://QmImage", metadata_sha256="ab" * 32, epoch="e1")
TARGET_ID = TARGET.id()
ITEM_ID = "ETHEREUM:0x3b3ee1931dc30c1957379fac9aba94d1c48a5405:41234"


# --------------------------------------------------------------------------- #
# Forbidden words                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text,found", [
    ("it lives on Base, obviously", ["base"]),
    ("minted on ETHEREUM back in the day", ["ethereum"]),
    ("the Foundation piece everyone forgot", ["foundation"]),
    ("check OpenSea or Rarible", ["opensea", "rarible"]),
    ("a foundational, basic, based idea", []),          # word boundaries
    ("the basement stairs creak", []),
    ("l2 season", ["l2"]),
    ("", []),
])
def test_forbidden_address_words(text, found):
    assert forbidden_address_words(text) == found


# --------------------------------------------------------------------------- #
# Context and prompt never carry the address                                   #
# --------------------------------------------------------------------------- #


def test_context_from_target_and_terms():
    ctx = TargetClueContext.from_target(
        TARGET, image_description="a lighthouse on a rock",
        metadata={"name": "Salt Harbor #3", "created_by": "Mara Quill"})
    assert ctx.display_name == "Salt Harbor"
    assert ctx.target_id == TARGET_ID and ctx.name_onchain == "Salt Harbor #3"
    assert {"salt", "harbor", "mara", "quill"} <= set(ctx.solution_terms)
    assert ctx.handle == "" and ctx.bio == ""            # inherited machinery
    assert len(ctx.clue_facet_plan) == PUZZLE_CLUES


def test_user_message_never_contains_chain_contract_or_token():
    ctx = TargetClueContext.from_target(TARGET, image_description="a lighthouse")
    for i in (1, 4, 8):
        msg = build_target_user_message(ctx, i, ["earlier clue"])
        low = msg.lower()
        assert "ethereum" not in low and TARGET.contract not in low
        assert "41234" not in msg and TARGET_ID not in msg
        assert "Salt Harbor" in msg                        # the writer sees the name


def test_system_prompt_has_no_base_and_no_claim_code():
    p = TARGET_SYSTEM_PROMPT.lower()
    assert "minted on base" not in p and "on base" not in p
    assert "claim code" not in p
    assert "chain:contract:tokenid" in p
    assert "never say or hint which blockchain" in p


# --------------------------------------------------------------------------- #
# Batched vision fetch                                                         #
# --------------------------------------------------------------------------- #


def test_describe_image_batched_hides_the_target_in_decoys():
    fetched = []

    def fetch(url):
        fetched.append(url)
        return b"img:" + url.encode()

    positions = set()
    for seed in range(8):
        fetched.clear()
        text = describe_image_batched(
            target_image_url="ipfs://target",
            decoy_image_urls=[f"ipfs://d{i}" for i in range(20)],
            fetch_bytes_generic=fetch, describe=lambda b: "described " + b.decode()[4:],
            decoys=5, rng=random.Random(seed))
        assert text == "described ipfs://target"
        assert len(fetched) == 6 and fetched.count("ipfs://target") == 1
        positions.add(fetched.index("ipfs://target"))
    assert len(positions) > 1


def test_describe_image_batched_decoy_failure_is_noise_target_failure_is_not():
    def fetch(url):
        if url != "ipfs://target":
            raise RuntimeError("decoy gateway blip")
        return b"ok"
    assert describe_image_batched(
        target_image_url="ipfs://target", decoy_image_urls=["ipfs://d1", "ipfs://d2"],
        fetch_bytes_generic=fetch, describe=lambda b: "fine", decoys=2,
        rng=random.Random(0)) == "fine"
    with pytest.raises(ImageUnavailable):
        describe_image_batched(
            target_image_url="ipfs://target", decoy_image_urls=["ipfs://d1"],
            fetch_bytes_generic=lambda u: None, describe=lambda b: "fine", decoys=1)
    with pytest.raises(ImageUnavailable):
        describe_image_batched(
            target_image_url="ipfs://target", decoy_image_urls=[],
            fetch_bytes_generic=lambda u: b"ok", describe=lambda b: "  ", decoys=0)


def test_image_batch_is_exactly_decoys_plus_one_or_refuses():
    """P1-5 (auditoria 09/09): um decoy sem imagem, ou com imagem https://
    (vai directa ao host, não passa pelo gateway do lote), encolhia o
    conjunto de anonimato em silêncio — pedíamos 8, a Pinata via 4. Agora o
    lote é exactamente decoys+1 pelo gateway, ou recusa."""
    fetched: list[str] = []

    def fetch(url):
        fetched.append(url)
        return b"ok"
    good = [f"ipfs://d{i}" for i in range(7)]
    assert describe_image_batched(
        target_image_url="ipfs://target", decoy_image_urls=good,
        fetch_bytes_generic=fetch, describe=lambda b: "x", rng=random.Random(0)) == "x"
    assert len(fetched) == 8
    for bad in (good[:6] + [""], good[:6] + ["https://cdn.example.com/1.png"], good[:6]):
        with pytest.raises(ImageUnavailable) as e:
            describe_image_batched(
                target_image_url="ipfs://target", decoy_image_urls=bad,
                fetch_bytes_generic=fetch, describe=lambda b: "x", rng=random.Random(0))
        assert "anonymity set" in str(e.value)
    with pytest.raises(ImageUnavailable):        # the target's own image, too
        describe_image_batched(
            target_image_url="https://cdn.example.com/t.png", decoy_image_urls=good,
            fetch_bytes_generic=fetch, describe=lambda b: "x", rng=random.Random(0))


def test_content_guard_runs_on_the_same_bytes_and_fails_closed():
    """Opus 06/09: o content_ok corre na passagem de visão, sobre os bytes já
    em mãos — zero leituras extra. False ⇒ ContentRefused com o id (o
    chamador exclui e re-sorteia); None ⇒ ImageUnavailable (fail-closed);
    a descrição só se pede depois do guarda aprovar."""
    from finding_memeland.target.clues import ContentRefused
    fetched: list[str] = []
    described: list[bytes] = []

    def fetch(url):
        fetched.append(url)
        return b"bytes-" + url.encode()

    def run(verdict):
        fetched.clear()
        described.clear()
        return describe_image_batched(
            target_image_url="ipfs://target", decoy_image_urls=["ipfs://d1", "ipfs://d2"],
            fetch_bytes_generic=fetch, decoys=2,
            describe=lambda b: described.append(b) or "fine",
            content_ok=lambda b: verdict if b == b"bytes-ipfs://target" else None,
            target_id="ethereum:0xabc:1", rng=random.Random(0))
    assert run(True) == "fine" and len(fetched) == 3 and described == [b"bytes-ipfs://target"]
    with pytest.raises(ContentRefused) as e:
        run(False)
    assert e.value.target_id == "ethereum:0xabc:1" and described == []
    assert len(fetched) == 3                          # nenhuma leitura extra
    with pytest.raises(ImageUnavailable):
        run(None)


# --------------------------------------------------------------------------- #
# Engine loop with fakes                                                       #
# --------------------------------------------------------------------------- #


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


_ANGLE_LINE = re.compile(r'as "angle"\): ([A-Z ]+):')
_ASPECT_LINE = re.compile(r'as "image_aspect"\): (\w+) —')


RAW = object()      # marker: the next draft is raw text, sent as-is (no JSON)


class FakeAnthropic:
    """Scripted clue drafts, in order. A str draft is an OBEDIENT writer: it
    declares the angle/aspect the prompt assigned and no claims. A dict
    draft is the JSON as-is (to script disobedience or claims). A (RAW,
    text) pair is sent verbatim — a writer that reasoned out loud."""

    def __init__(self, drafts):
        self._drafts = list(drafts)
        self.calls = []

        class _Msgs:
            def create(_s, **kw):
                self.calls.append(kw)
                d = self._drafts.pop(0)
                if isinstance(d, tuple) and d[0] is RAW:
                    return _Resp(d[1])
                if isinstance(d, str):
                    user = kw["messages"][0]["content"]
                    ang = _ANGLE_LINE.search(user)
                    asp = _ASPECT_LINE.search(user)
                    n = re.search(r"name \((\d+) words\)", user)
                    claims = ([{"type": "word_count", "word": 0, "value": int(n.group(1))}]
                              if ang and ang.group(1).strip() == "STRUCTURE" and n else [])
                    d = {"clue": d, "taunt": "", "claims": claims,
                         "angle": ang.group(1).strip() if ang else None,
                         "image_aspect": asp.group(1) if asp else None}
                return _Resp(json.dumps(d))
        self.messages = _Msgs()


def ctx():
    return TargetClueContext.from_target(TARGET, image_description="a lighthouse")


class FakeTruthJudge:
    """Scripted verdicts by substring of the clue: {"substring": (ok, reason)};
    unknown clue → consistent. `down=True` → None (unavailable)."""

    name = "fake-judge"

    def __init__(self, table=None, down=False):
        self.table = table or {}
        self.down = down
        self.seen = []

    def check(self, clue, *, name, word, artwork):
        from finding_memeland.target.clues import TruthVerdict
        self.seen.append((clue, name, word, artwork))
        if self.down:
            return TruthVerdict(None, "api down")
        for k, (ok, why) in self.table.items():
            if k in clue:
                return TruthVerdict(ok, why)
        return TruthVerdict(True, "fits")


def engine(drafts, *, guard=None, guard_hits=None, judge=False):
    if guard is None:
        hits = guard_hits if guard_hits is not None else {"salt harbor": {ITEM_ID}}
        guard = ClueSearchGuard(search=FakeSearch(hits), retries=0, sleep_s=0.0)
    return TargetClueEngine(FakeAnthropic(drafts), "model", search_guard=guard,
                            truth_judge=judge, solver=False)


def test_engine_requires_a_search_guard_and_a_truth_judge():
    with pytest.raises(ValueError):
        TargetClueEngine(FakeAnthropic([]), "m", search_guard=None, truth_judge=False, solver=False)
    with pytest.raises(ValueError):
        TargetClueEngine(FakeAnthropic([]), "m", search_guard=False, truth_judge=None, solver=False)


def test_chain_word_is_rejected_then_regenerated():
    e = engine(["patience is a coin on base", "patience is a coin nobody spends"])
    d = e.next_clue(ctx(), 1, [])
    assert d.text == "patience is a coin nobody spends"
    assert "REJECTED" in e._client.calls[1]["messages"][0]["content"]
    assert "blockchain or a platform" in e._client.calls[1]["messages"][0]["content"]


def test_search_hit_is_rejected_in_puzzle_phase():
    # o FakeSearch devolve o alvo para qualquer texto com "salt harbor" —
    # o primeiro rascunho cita o nome (também apanhado pelos guardrails de
    # texto), o segundo usa uma frase que o índice liga ao alvo
    guard = ClueSearchGuard(search=FakeSearch({"salt harbor": {ITEM_ID},
                                               "lighthouse": {ITEM_ID}}),
                            retries=0, sleep_s=0.0)
    e = engine(["a lighthouse guards it", "patience is a coin nobody spends"],
               guard=guard)
    d = e.next_clue(ctx(), 2, ["first clue"])
    assert d.text == "patience is a coin nobody spends"
    assert "SEARCH GUARD" in e._client.calls[1]["messages"][0]["content"]


def test_search_guard_not_applied_in_reveal_phase():
    guard = ClueSearchGuard(search=FakeSearch({"salt harbor": {ITEM_ID},
                                               "lighthouse": {ITEM_ID}}),
                            retries=0, sleep_s=0.0)
    e = engine(["a lighthouse guards it"], guard=guard)
    d = e.next_clue(ctx(), PUZZLE_CLUES + 1, ["c"] * PUZZLE_CLUES)
    assert d.text == "a lighthouse guards it"


def test_unverifiable_guard_raises_fail_closed_on_any_clue():
    blind = ClueSearchGuard(search=FakeSearch({}), retries=0, sleep_s=0.0)  # canário cego
    for idx in (1, 5):
        e = engine(["patience is a coin nobody spends"] * 3, guard=blind)
        with pytest.raises(SearchGuardUnavailable) as err:
            e.next_clue(ctx(), idx, ["c"] * (idx - 1))
        assert "patience" not in str(err.value)          # nunca a pista


def test_clean_clue_passes_first_time():
    e = engine(["patience is a coin nobody spends"])
    d = e.next_clue(ctx(), 1, [])
    assert d.text == "patience is a coin nobody spends"
    assert len(e._client.calls) == 1


# --------------------------------------------------------------------------- #
# Structural-claim guard (Opus, live test 09/09 — Hunt #9 finding repeated)    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text,rejected", [
    # the real one: "Ancient Future" — FUTURE is not a compound
    ("word two of the name is a compound hiding in plain sight — two complete "
     "words fused, each carrying full weight alone. find that seam.", True),
    ("a portmanteau at the end", True),
    # Opus: close the class a little wider than the case
    ("the first word is an anagram of a Roman city", True),
    ("word two rhymes with suture", True),
    ("shares its initials with a famous duo", True),
    ("the second word has no vowels", True),
    ("sounds like a word for later", True),
    # the SECOND real one (09/09, after the guard): "carries a hidden smaller
    # word inside it that also names a tense" — FUTURE hides no such word
    ("word 2: not a root, not a suffix acting alone — it carries a hidden smaller "
     "word inside it that also names a tense. the container and the contained "
     "both do grammatical work.", True),
    ("its roots are Latin, its mood is tomorrow", False),        # etymology stays legal
    # 3rd --real-clues (09/09): the declared claim was true, the prose added
    # "that single letter is the only one doing that job" — ANCIENT has three
    # vowels. Uniqueness-of-a-letter-role cannot be declared → refused.
    ("word 1 opens with a vowel — that single letter is the only one doing that job", True),
    ("the only vowel in the word is at the front", True),
    ("the second word has five letters", True),
    ("the second word of this name has six letters and ends with a vowel", False),
    ("the first word starts with a vowel; the last word ends with a vowel", False),
    ("the first word ends with a vowel", True),
    ("the second word starts with a vowel", True),
    ("the name has three words", True),
    ("a two-word name, both halves older than the medium", False),
    ("the first word has seven letters. the second ends with a consonant", True),
    ("the first word carries a double letter", True),
    ("the first word contains 'cien'", False),
    ("the last word contains 'past'", True),
    ("the name is a palindrome", True),
    ("the second word begins with the letter F", False),
    ("the second word begins with the letter T", True),
    ("word one ends in 't'", False),
    ("word one ends in 'e'", True),
    # unspecified word: true if it holds for ANY word of the name
    ("the last letter is a consonant", False),
    ("six letters, and the seam runs through the middle", False),
    # prose that is not a claim about the string
    ("nothing structural here, only the flicker of a tape", False),
    ("the first to find it wins; the tape ends with a hiss", False),
])
def test_structural_claims_are_checked_against_the_name(text, rejected):
    from finding_memeland.target.clues import structural_claim_errors
    assert bool(structural_claim_errors(text, "ancient future")) is rejected, \
        structural_claim_errors(text, "ancient future")


def test_false_structural_claim_is_rejected_then_regenerated():
    """Hunt #9: 'pista 1 factualmente falsa à letra — ver se repete'. It
    repeated (clue 4, 09/09). A false claim about the letters is now a
    guardrail reason, in every phase, and the feedback names the word."""
    e = engine(["the second word is a compound — two complete words fused, find that seam",
                "the second word starts with a vowel, like a door left open",
                "patience is a coin nobody spends"])
    d = e.next_clue(ctx(), 9, ["c"] * 8)                 # reveal phase too
    assert d.text == "patience is a coin nobody spends"
    fb1 = e._client.calls[1]["messages"][0]["content"]
    fb2 = e._client.calls[2]["messages"][0]["content"]
    assert "NAME'S LETTERS" in fb1 and "cannot be checked" in fb1
    assert "'starts with a vowel' is false for harbor" in fb2
    # letter COUNTS were already banned by the base guardrail (models miscount)


# --------------------------------------------------------------------------- #
# DECLARE-AND-VERIFY (Opus, 09/09) — the guard tests the data, not the prose   #
# --------------------------------------------------------------------------- #


def test_verify_claims_checks_every_declared_claim_against_the_spelling():
    from finding_memeland.target.clues import verify_claims
    N = "ancient future"
    ok = [{"type": "starts", "word": 1, "value": "vowel"},
          {"type": "ends", "word": 2, "value": "e"},
          {"type": "contains", "word": 1, "value": "cien"},
          {"type": "word_count", "word": 0, "value": 2},
          {"type": "starts", "word": 0, "value": "a"},
          {"type": "ends", "word": 0, "value": "vowel"}]
    assert verify_claims(ok, N) == []
    bad = verify_claims([{"type": "contains", "word": 2, "value": "past"},
                         {"type": "double_letter", "word": 2},
                         {"type": "palindrome", "word": 1},
                         {"type": "no_vowels", "word": 0},
                         {"type": "starts", "word": 2, "value": "vowel"},
                         {"type": "word_count", "word": 0, "value": 3},
                         {"type": "compound", "word": 2},
                         {"type": "starts", "word": 5, "value": "a"},
                         {"type": "starts", "word": 1, "value": "abc"}], N)
    assert len(bad) == 9
    assert any("'past' is not spelt inside future" in e for e in bad)
    assert any("unknown type" in e and "compound" in e for e in bad)
    assert any("'word' must be 1..2" in e for e in bad)
    assert verify_claims("nope", N) == ["'claims' must be a list"]


def test_undeclared_prose_is_rejected_and_unverifiable_family_always():
    from finding_memeland.target.clues import undeclared_prose_errors
    # a structural statement in prose needs a matching declared claim
    assert undeclared_prose_errors("the second word starts with a vowel", []) != []
    assert undeclared_prose_errors("the second word starts with a vowel",
                                   [{"type": "starts", "word": 2, "value": "vowel"}]) == []
    assert undeclared_prose_errors("the first letter is a consonant", []) != []
    # the family nobody can declare is refused even with a claim attached
    e = undeclared_prose_errors(
        "it carries a hidden smaller word inside it that also names a tense",
        [{"type": "contains", "word": 2, "value": "ure"}])
    assert e and "cannot check" in e[0]
    assert undeclared_prose_errors("only the flicker of a tape", []) == []


def test_image_aspects_are_assigned_distinct_and_stable():
    from finding_memeland.target.clues import IMAGE_ASPECTS, image_aspect_for
    from finding_memeland.content.relic_clues import relic_slot_for
    c = ctx()
    art = [i for i in range(1, PUZZLE_CLUES + 1) if relic_slot_for(i, c)[0] == "image"]
    aspects = [image_aspect_for(i, c) for i in art]
    assert len(art) == 2 and len(set(aspects)) == 2
    assert all(a in IMAGE_ASPECTS for a in aspects)
    assert aspects == [image_aspect_for(i, ctx()) for i in art]          # crash-resume
    assert all(image_aspect_for(i, c) is None for i in range(1, PUZZLE_CLUES + 1)
               if i not in art)
    assert image_aspect_for(PUZZLE_CLUES + 3, c) is None


def test_declared_angle_must_be_the_assigned_one():
    """The obedient str drafts echo the prompt's angle; a dict draft that
    declares another angle (or none) is sent back with the assignment."""
    from finding_memeland.target.clues import angle_label
    from finding_memeland.content.relic_clues import angle_for_unverifiable, relic_slot_for
    c = ctx()
    i = next(i for i in range(1, PUZZLE_CLUES + 1)
             if relic_slot_for(i, c)[0] != "image"
             and angle_label(angle_for_unverifiable(i, c)) not in ("STRUCTURE", None))
    assigned = angle_label(angle_for_unverifiable(i, c))
    other = "RELATION" if assigned != "RELATION" else "CULTURAL USE"
    e = engine([{"clue": "patience is a coin nobody spends", "taunt": "", "angle": other},
                {"clue": "patience is a coin nobody spends", "taunt": ""},      # none declared
                "patience is a coin nobody spends"])
    d = e.next_clue(c, i, ["c"] * (i - 1))
    assert d.text == "patience is a coin nobody spends"
    fb1, fb2 = (x["messages"][0]["content"] for x in e._client.calls[1:3])
    assert f"ASSIGNED angle '{assigned}'" in fb1 and f"declared '{other}'" in fb1
    assert "declared None" in fb2


def test_two_art_pieces_cannot_share_an_aspect():
    from finding_memeland.target.clues import image_aspect_for
    from finding_memeland.content.relic_clues import relic_slot_for
    c = ctx()
    art = [i for i in range(1, PUZZLE_CLUES + 1) if relic_slot_for(i, c)[0] == "image"]
    first, second = art
    wrong = image_aspect_for(first, c)                    # the FIRST piece's aspect, again
    e = engine([{"clue": "phosphor bruises where the beam lingers", "taunt": "x",
                 "image_aspect": wrong, "claims": []},
                "a frame arranged around its own absence"])
    d = e.next_clue(c, second, ["c"] * (second - 1))
    assert d.text == "a frame arranged around its own absence"
    fb = e._client.calls[1]["messages"][0]["content"]
    assert "ASSIGNED aspect" in fb and image_aspect_for(second, c) in fb


def test_structure_piece_needs_a_verified_claim_and_false_claims_reject():
    from finding_memeland.target.clues import angle_label
    from finding_memeland.content.relic_clues import angle_for_unverifiable
    c = ctx()
    i = next(i for i in range(1, PUZZLE_CLUES + 1)
             if angle_label(angle_for_unverifiable(i, c)) == "STRUCTURE")
    e = engine([{"clue": "its tail is a vowel, its head is not", "taunt": "",
                 "angle": "STRUCTURE", "claims": []},                        # no claim
                {"clue": "its tail is a vowel, its head is not", "taunt": "",
                 "angle": "STRUCTURE",
                 "claims": [{"type": "ends", "word": 2, "value": "vowel"}]},   # harbor → FALSE
                {"clue": "its tail is not a vowel", "taunt": "", "angle": "STRUCTURE",
                 "claims": [{"type": "ends", "word": 2, "value": "consonant"}]}])
    d = e.next_clue(c, i, ["c"] * (i - 1))
    assert d.text == "its tail is not a vowel"
    fb1, fb2 = (x["messages"][0]["content"] for x in e._client.calls[1:3])
    assert "no claim, no piece" in fb1
    assert "claim ends (word 2, value 'vowel') is FALSE" in fb2


def test_synonym_list_in_a_name_piece_is_rejected():
    """Live test 09/09, clue 1: 'shares a zip code with speculation, sci-fi,
    and dread — but NOT nostalgia' → FUTURE by lookup in ten seconds."""
    e = engine(["it shares a zip code with speculation, sci-fi, and dread",
                "patience is a coin nobody spends"])
    d = e.next_clue(ctx(), 1, [])
    assert d.text == "patience is a coin nobody spends"
    assert "synonym list" in e._client.calls[1]["messages"][0]["content"]


def test_relation_angle_never_on_clue_one_and_the_ramp_is_the_hunt_9_one():
    """The Hunt #7 rule only. A "not before piece 4" tweak (09/09) was
    reverted by Pedro: the ramp that reached clue 7 in Hunts #8 and #9 is
    not changed on the strength of a simulation."""
    from finding_memeland.content.relic_clues import RELATION_EARLIEST, angle_for_unverifiable
    from finding_memeland.content.relic_clues import RelicClueContext
    assert RELATION_EARLIEST == 2
    for name in ("Salt Harbor", "Ancient Future", "Damp Hamlet", "Caesar Plop", "Uncle Pump"):
        c = RelicClueContext(display_name=name, image_description="", lore="", backstory="")
        assert not (angle_for_unverifiable(1, c) or "").startswith("RELATION"), name


def test_parse_target_clue_reads_the_declaration():
    from finding_memeland.target.clues import parse_target_clue
    d = parse_target_clue('noise {"clue": "x", "taunt": "t", "angle": "semantic field", '
                          '"image_aspect": null, "claims": [{"type": "starts", "word": 1, '
                          '"value": "a"}]} tail')
    assert d.text == "x" and d.taunt == "t" and d.angle == "semantic field"
    assert d.image_aspect is None and d.claims[0]["type"] == "starts"
    d = parse_target_clue('{"clue": "x"}')
    assert d.angle is None and d.claims == [] and d.taunt is None
    with pytest.raises(ValueError):
        parse_target_clue("no json here")


def test_structure_angle_text_asks_for_a_checkable_declared_fact():
    from finding_memeland.content.relic_clues import PUZZLE_ANGLES
    st = next(a for a in PUZZLE_ANGLES if a.startswith("STRUCTURE"))
    assert "DECLARED" in st and "NEVER guess" in st
    assert "a compound, a suffix that does work" not in st


# --------------------------------------------------------------------------- #
# The consistency judge — clue + answer must be TRUE (Opus, 09/09)            #
# --------------------------------------------------------------------------- #


def test_false_clue_is_rejected_by_the_judge_with_its_reason_and_regenerated():
    """Live test 09/09, clue 1: a definition of PRESENT for the word FUTURE —
    every other guard green (the blind solver REWARDS a clue that points the
    wrong way). The judge reads clue + answer and says no."""
    judge = FakeTruthJudge({"seam between them": (False, "that describes the present, the word is future")})
    e = engine(["sits between what was and what cannot be measured — the seam between them",
                "patience is a coin nobody spends"], judge=judge)
    d = e.next_clue(ctx(), 1, [])
    assert d.text == "patience is a coin nobody spends"
    fb = e._client.calls[1]["messages"][0]["content"]
    assert "CONSISTENCY JUDGE" in fb and "describes the present" in fb
    # it saw the answer, the word this piece is about, and the artwork
    clue, name, word, art = judge.seen[0]
    assert name == "Salt Harbor" and word in ("Salt", "Harbor") and art == "a lighthouse"


def test_judge_runs_in_the_reveal_phase_too_and_on_art_pieces():
    from finding_memeland.content.relic_clues import relic_slot_for
    judge = FakeTruthJudge()
    e = engine(["a clue", "b clue"], judge=judge)
    c = ctx()
    art = next(i for i in range(1, PUZZLE_CLUES + 1) if relic_slot_for(i, c)[0] == "image")
    e.next_clue(c, art, ["x"] * (art - 1))
    assert judge.seen[-1][2] is None                       # art piece: no word
    e.next_clue(c, PUZZLE_CLUES + 4, ["x"] * (PUZZLE_CLUES + 3))
    assert len(judge.seen) == 2                            # reveal phase: judged as well


def test_judge_unavailable_fails_closed_and_enters_the_guard_hold():
    from finding_memeland.target.clues import ClueGuardUnavailable, TruthJudgeUnavailable
    e = engine(["a clue"], judge=FakeTruthJudge(down=True))
    with pytest.raises(TruthJudgeUnavailable) as ex:
        e.next_clue(ctx(), 5, ["x"] * 4)
    assert "a clue" not in str(ex.value) and isinstance(ex.value, ClueGuardUnavailable)
    # integration: same hold ledger as the search guard
    from finding_memeland.target.dryrun import TargetWorld
    from finding_memeland.target.integration import HOLD_GUARD, clue_failed
    w = TargetWorld()
    hunt = w.launch()
    assert clue_failed(w.orch, hunt, TruthJudgeUnavailable("judge down")) is True
    assert any("HOLD" in m and HOLD_GUARD in m for m in w.notices())


def test_anthropic_truth_judge_parses_and_fails_to_none():
    from finding_memeland.target.clues import AnthropicTruthJudge

    class _C:
        def __init__(self, text):
            self.text = text
            self.calls = []

            class _M:
                def create(_s, **kw):
                    self.calls.append(kw)
                    if isinstance(self.text, Exception):
                        raise self.text
                    return _Resp(self.text)
            self.messages = _M()
    c = _C('{"consistent": false, "reason": "the word is future"}')
    v = AnthropicTruthJudge(c, "m").check("clue", name="Ancient Future", word="Future", artwork="art")
    assert v.consistent is False and "future" in v.reason
    user = c.calls[0]["messages"][0]["content"]
    assert "Ancient Future" in user and "'Future'" in user and "CLUE: clue" in user
    # transient trouble: retried, then fail-closed WITH the measured cause (R8)
    naps = []
    g = _C("garbage")
    v = AnthropicTruthJudge(g, "m", sleep=naps.append).check("c", name="n", word=None, artwork="")
    assert v.consistent is None and "no JSON in judge answer" in v.reason
    assert len(g.calls) == 3 and naps == [2.0, 4.0]
    v = AnthropicTruthJudge(_C(RuntimeError("503 overloaded")), "m", sleep=lambda s: None).check(
        "c", name="n", word=None, artwork="")
    assert v.consistent is None and "RuntimeError: 503 overloaded" in v.reason


def test_judge_recovers_on_the_second_try():
    from finding_memeland.target.clues import AnthropicTruthJudge

    class _Flaky:
        def __init__(self):
            self.n = 0

            class _M:
                def create(_s, **kw):
                    self.n += 1
                    if self.n == 1:
                        raise RuntimeError("529")
                    return _Resp('{"consistent": true, "reason": "fits"}')
            self.messages = _M()
    f = _Flaky()
    v = AnthropicTruthJudge(f, "m", sleep=lambda s: None).check("c", name="n", word=None, artwork="")
    assert v.consistent is True and f.n == 2


def test_writer_reasoning_out_loud_costs_one_attempt_not_the_round():
    """4th --real-clues: 'Looking at "Ancient" — I need a checkable structural
    claim…' — no JSON, ValueError out of the loop, round skipped. Now: one
    pointed retry inside generate(); a second failure still raises."""
    e = engine([(RAW, 'Looking at "Ancient" — let me verify facts about its letters: A-'),
                "patience is a coin nobody spends"])
    d = e.next_clue(ctx(), 5, ["x"] * 4)
    assert d.text == "patience is a coin nobody spends"
    assert len(e._client.calls) == 2
    assert "SILENTLY" in e._client.calls[1]["messages"][0]["content"]
    assert e._client.calls[0]["max_tokens"] == 1024
    e = engine([(RAW, "no json"), (RAW, "still no json")])
    with pytest.raises(ValueError):
        e.next_clue(ctx(), 5, ["x"] * 4)


def test_guard_pressure_is_tallied_per_clue_and_clue_one_gets_ten_attempts(caplog):
    """Pedro (09/09): 'a hunt must run, it cannot block' — guard pressure is
    measured per clue (which guard refused how often), so a guard that is
    too tight is removed with numbers, not opinion. Clue 1 tries 10 times."""
    import logging
    judge = FakeTruthJudge({"present": (False, "that is the present")})
    e = engine(["patience is a coin on base",                    # address words
                {"clue": "a coin nobody spends", "taunt": "", "angle": "NOPE"},   # declaration
                "the seam of the present",                       # judge
                "patience is a coin nobody spends"], judge=judge)
    with caplog.at_level(logging.WARNING):
        d = e.next_clue(ctx(), 5, ["x"] * 4)
    assert d.text == "patience is a coin nobody spends"
    assert e.last_attempt_report == (4, {"address words": 1, "declaration": 1, "judge": 1})
    assert any("published after 4 attempts" in r.message and "judge ×1" in r.message
               for r in caplog.records)
    # clue 1: ten attempts before refusing
    e = engine(["patience is a coin on base"] * 10 + ["never reached"])
    with pytest.raises(RuntimeError):
        e.next_clue(ctx(), 1, [])
    assert len(e._client.calls) == 10 and e._rejections == {"address words": 10}

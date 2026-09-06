"""Target clue engine — context without the address, prompt without the
chain, forbidden chain/platform words, search guard in the loop (fail-closed
when unverifiable), batched vision fetch."""

from __future__ import annotations

import json
import random

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
        fetch_bytes_generic=fetch, describe=lambda b: "fine",
        rng=random.Random(0)) == "fine"
    with pytest.raises(ImageUnavailable):
        describe_image_batched(
            target_image_url="ipfs://target", decoy_image_urls=["ipfs://d1"],
            fetch_bytes_generic=lambda u: None, describe=lambda b: "fine")
    with pytest.raises(ImageUnavailable):
        describe_image_batched(
            target_image_url="ipfs://target", decoy_image_urls=[],
            fetch_bytes_generic=lambda u: b"ok", describe=lambda b: "  ")


# --------------------------------------------------------------------------- #
# Engine loop with fakes                                                       #
# --------------------------------------------------------------------------- #


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class FakeAnthropic:
    """Scripted clue drafts, in order."""

    def __init__(self, drafts):
        self._drafts = list(drafts)
        self.calls = []

        class _Msgs:
            def create(_s, **kw):
                self.calls.append(kw)
                text = self._drafts.pop(0)
                return _Resp(json.dumps({"clue": text, "taunt": ""}))
        self.messages = _Msgs()


def ctx():
    return TargetClueContext.from_target(TARGET, image_description="a lighthouse")


def engine(drafts, *, guard=None, guard_hits=None):
    if guard is None:
        hits = guard_hits if guard_hits is not None else {"salt harbor": {ITEM_ID}}
        guard = ClueSearchGuard(search=FakeSearch(hits), retries=0, sleep_s=0.0)
    return TargetClueEngine(FakeAnthropic(drafts), "model", search_guard=guard,
                            solver=False)


def test_engine_requires_a_search_guard():
    with pytest.raises(ValueError):
        TargetClueEngine(FakeAnthropic([]), "m", search_guard=None, solver=False)


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

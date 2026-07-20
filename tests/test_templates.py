from finding_memeland.content import templates
from finding_memeland.content.templates import (
    WinnerData,
    clue_one,
    explainer_pending,
    winner_announcement,
)


def _data(persona_handle, winner_handle):
    return WinnerData(
        hunt_n=1, winner_handle=winner_handle, time_to_win="2h 10m",
        prize_amount="500,000", tx_link="0xtx", persona_handle=persona_handle,
        persona_user_id="100", claim_code="ABCDEFGH", salt="s",
    )


def test_handles_not_double_prefixed_when_already_at():
    out = winner_announcement(_data("@hidden_one", "@winner_one"))
    assert "@@" not in out
    assert "@hidden_one" in out
    assert "@winner_one" in out


def test_handles_prefixed_when_missing_at():
    out = winner_announcement(_data("hidden_two", "winner_two"))
    assert "@hidden_two" in out
    assert "@winner_two" in out
    assert "@@" not in out


def test_reveals_integrity_ingredients():
    out = winner_announcement(_data("@h", "@w"))
    assert "100" in out and "ABCDEFGH" in out and "salt" in out.lower()


# --- Pack 2, item 3 (post-mortem P1b): cold-traffic explainer -------------

def test_clue_one_opens_with_the_explainer(monkeypatch):
    monkeypatch.setattr(templates, "CLUE_ONE_EXPLAINER",
                        "an AI hid an account on X. find it, DM the code, win.")
    out = clue_one(hunt_n=2, clue_text="riddle here", prize="1,000,000",
                   integrity_hash="be481c8b")
    # Order: announcement first, explainer right after, then the clue.
    assert out.startswith("Hunt #2 is live.")
    assert out.index("Hunt #2 is live") < out.index("an AI hid an account on X.")
    assert out.index("an AI hid an account on X.") < out.index("1st clue:")
    assert "riddle here" in out
    assert "Reshare this post to enter" in out
    assert "integrity: be481c8b" in out
    assert "Check pinned for rules" in out


def test_explainer_pending_detects_placeholder(monkeypatch):
    monkeypatch.setattr(
        templates, "CLUE_ONE_EXPLAINER", templates._EXPLAINER_PLACEHOLDER_MARK
    )
    assert explainer_pending() is True
    monkeypatch.setattr(templates, "CLUE_ONE_EXPLAINER", "real text")
    assert explainer_pending() is False


def test_shipped_explainer_is_real_and_leads_the_post():
    """The launch gate is open: the SHIPPED explainer is real text, it opens
    clue_one, and it carries none of the phrases we ban (scam-pattern wallet
    ask, engagement bait, platform-manipulation vocabulary)."""
    assert explainer_pending() is False
    out = clue_one(hunt_n=2, clue_text="riddle", prize="1,000,000,000",
                   integrity_hash="abc")
    assert out.startswith("Hunt #2 is live.")
    assert "every hunt i invent someone who doesn't exist" in out
    assert out.index("is live.") < out.index("every hunt i invent")
    assert "DM me the code" in out
    low = out.lower()
    assert "wallet" not in low  # the wallet ask lives in the pinned rules, never in clue 1
    assert "fake" not in low
    for phrase in _BAIT_PHRASES:
        assert phrase not in low


# --- Backlog exception (post-mortem P3.1): the reveal must not lie ---------

def test_winner_announcement_does_not_claim_dormancy():
    # Production keeps the persona dressed forever (undress_on_retire=False);
    # "dormant in 1 hour" was false — in the same post that asks readers to
    # verify the integrity hash.
    out = winner_announcement(_data("@hidden_one", "@winner_one"))
    assert "dormant" not in out.lower()
    assert "stays up as a trophy" in out
    assert "played once, and never again" in out


# --- X engagement-bait triggers must never reach a published post ----------

# The operator account was flagged by X on 2026-07-15 for a post combining
# "don't miss out" + "follow @X and turn notifications on" + "will you be the
# one". Those phrases were then stripped from every hand-written post — but one
# survived inside a TEMPLATE, in the winner reveal: the single post carrying the
# tx link and the integrity proof, i.e. where a deboost costs the most.
_BAIT_PHRASES = (
    "turn notifications on",
    "don't miss out",
    "dont miss out",
    "will you be the one",
)


def test_winner_announcement_has_no_engagement_bait():
    out = winner_announcement(_data("@hidden_one", "@winner_one")).lower()
    for phrase in _BAIT_PHRASES:
        assert phrase not in out, f"engagement-bait phrase in the reveal: {phrase!r}"


def test_clue_one_has_no_engagement_bait(monkeypatch):
    monkeypatch.setattr(templates, "CLUE_ONE_EXPLAINER", "an AI hid an account. find it.")
    out = clue_one(
        hunt_n=2, clue_text="riddle", prize="1,000,000", integrity_hash="abc"
    ).lower()
    for phrase in _BAIT_PHRASES:
        assert phrase not in out, f"engagement-bait phrase in clue 1: {phrase!r}"

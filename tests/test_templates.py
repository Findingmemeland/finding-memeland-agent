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
    assert out.startswith("an AI hid an account on X.")
    # Everything the old post had is still there, after the explainer.
    assert "Hunt #2 is live" in out
    assert "riddle here" in out
    assert "Reshare this post to enter" in out
    assert "integrity: be481c8b" in out
    assert "Check pinned for rules" in out


def test_explainer_pending_detects_placeholder(monkeypatch):
    assert explainer_pending() is True  # ships as a placeholder on purpose
    monkeypatch.setattr(templates, "CLUE_ONE_EXPLAINER", "real text")
    assert explainer_pending() is False


# --- Backlog exception (post-mortem P3.1): the reveal must not lie ---------

def test_winner_announcement_does_not_claim_dormancy():
    # Production keeps the persona dressed forever (undress_on_retire=False);
    # "dormant in 1 hour" was false — in the same post that asks readers to
    # verify the integrity hash.
    out = winner_announcement(_data("@hidden_one", "@winner_one"))
    assert "dormant" not in out.lower()
    assert "stays up as a trophy" in out
    assert "played once, and never again" in out

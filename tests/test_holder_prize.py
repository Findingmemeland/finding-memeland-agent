"""Token-denominated prizes + holder reward split (Pedro, 2026-07-31).

/launch takes an exact $FIND amount ("500M", "1B") — no USD conversion, no
FMML_USD_PRICE needed. Holding is NO LONGER eliminatory: a non-holder winner
still wins, but is paid non_holder_prize_pct% of the pot (holders get 100%),
and the Winner Announcement says so in Pedro's words.
"""

import pytest

from finding_memeland.dm.validator import DMValidator, ParsedDM
from finding_memeland.orchestrator.simulation import build_simulation
from finding_memeland.orchestrator.state_machine import HuntState
from finding_memeland.runtime import fmt_tokens, parse_token_amount

WALLET = "0x" + "a" * 40


# ---------------------------------------------------------------------------
# /launch token parsing
# ---------------------------------------------------------------------------
def test_parse_token_amount_accepts_suffixes_and_plain():
    assert parse_token_amount("500M") == 500_000_000
    assert parse_token_amount("1B") == 1_000_000_000
    assert parse_token_amount("0.5b") == 500_000_000
    assert parse_token_amount("250k") == 250_000
    assert parse_token_amount("500000000") == 500_000_000
    assert parse_token_amount("500,000,000") == 500_000_000
    assert parse_token_amount("500_000_000") == 500_000_000


def test_parse_token_amount_rejects_garbage():
    for bad in ("", "abc", "-5", "0", "1x", "$200"):
        with pytest.raises(ValueError):
            parse_token_amount(bad)


def test_fmt_tokens_compact():
    assert fmt_tokens(100_000_000) == "100M"
    assert fmt_tokens(1_500_000_000) == "1.5B"
    assert fmt_tokens(250_000) == "250k"
    assert fmt_tokens(123) == "123"


# ---------------------------------------------------------------------------
# Validator: holding is a FLAG now, checked last — never eliminatory
# ---------------------------------------------------------------------------
class _Chain:
    def __init__(self, holds): self._h = holds
    def has_continuous_holding(self, **kw): return self._h


class _X:
    def __init__(self, reshared=True): self._r = reshared
    def has_reshared(self, **kw): return self._r


class _Hunt:
    claim_code = "AB2DEF7X"
    min_balance_fmml = 1000
    holding_hours = 24
    reshare_post_id = "111"


def _dm(code="AB2DEF7X"):
    return ParsedDM(dm_id="1", sender_x_id="9", wallet=WALLET,
                    claim_code=code, claim_candidates=(code,))


def _profile(_): return {"name": "Jane", "handle": "@jane", "bio": "gm"}


def test_non_holder_still_wins_with_flag_down():
    v = DMValidator(chain=_Chain(False), x_client=_X(), profile_lookup=_profile)
    res = v.validate(_dm(), _Hunt())
    assert res.won is True and res.outcome == "won"
    assert res.holder is False


def test_holder_wins_with_flag_up():
    v = DMValidator(chain=_Chain(True), x_client=_X(), profile_lookup=_profile)
    res = v.validate(_dm(), _Hunt())
    assert res.won is True and res.holder is True


def test_reshare_and_bot_are_still_eliminatory():
    v = DMValidator(chain=_Chain(True), x_client=_X(reshared=False),
                    profile_lookup=_profile)
    assert v.validate(_dm(), _Hunt()).outcome == "no_reshare"
    bot = DMValidator(chain=_Chain(True), x_client=_X(),
                      profile_lookup=lambda _: {"name": "Solver Bot",
                                                "handle": "@solver_bot", "bio": ""})
    assert bot.validate(_dm(), _Hunt()).outcome == "bot_disqualified"


# ---------------------------------------------------------------------------
# E2E: the pay split + the announcement line
# ---------------------------------------------------------------------------
class _NonHolderValidator:
    def validate(self, parsed, hunt):
        from finding_memeland.dm.validator import ValidationResult
        if not parsed.wallet or hunt.claim_code not in (
            parsed.claim_candidates or ()
        ):
            return ValidationResult(False, "bad_code")
        return ValidationResult(True, "won", holder=False)


def test_non_holder_winner_paid_pct_and_told_publicly():
    rig = build_simulation()
    rig.orchestrator._validator = _NonHolderValidator()
    hunt = rig.orchestrator.run_hunt(prize_fmml=1_000_000_000)
    assert hunt.state == HuntState.DONE
    assert hunt.prize_fmml == 1_000_000_000          # the pot is advertised in full
    assert rig.payout.sent[0]["amount"] == 100_000_000   # 10% paid
    reveal = next(p for p in rig.publisher.posts if "We have a winner" in p)
    assert "100,000,000 $FIND transferred" in reveal
    assert "heads up: this wallet isn't holding $FIND" in reveal
    assert "non-holders win 10% of the pot" in reveal
    assert "hold on to your tokens and the full bounty is yours next time" in reveal
    # Books record what was PAID, not the advertised pot.
    assert rig.repo.winners[0]["prize_fmml"] == 100_000_000
    assert any("NOT a holder" in m for m in rig.notifier.messages)


def test_clue_one_announces_the_holder_split():
    rig = build_simulation()
    rig.orchestrator.run_hunt(prize_fmml=1_000_000_000)
    clue1 = rig.publisher.posts[0]
    assert "The first to find me wins 1,000,000,000 $FIND." in clue1
    assert "hold $FIND to win the full prize — non-holders win 10%." in clue1
    # The split line sits directly under the prize line (Pedro's placement).
    assert clue1.index("wins 1,000,000,000 $FIND.") < clue1.index("non-holders win 10%")
    assert clue1.index("non-holders win 10%") < clue1.index("Reshare this post")


def test_holder_winner_paid_in_full_no_note():
    rig = build_simulation()
    hunt = rig.orchestrator.run_hunt(prize_fmml=1_000_000_000)
    assert hunt.state == HuntState.DONE
    assert rig.payout.sent[0]["amount"] == 1_000_000_000
    reveal = next(p for p in rig.publisher.posts if "We have a winner" in p)
    assert "heads up" not in reveal


def test_non_holder_pct_is_configurable():
    rig = build_simulation()
    rig.orchestrator._validator = _NonHolderValidator()
    rig.orchestrator._non_holder_pct = 25
    rig.orchestrator.run_hunt(prize_fmml=1_000_000_000)
    assert rig.payout.sent[0]["amount"] == 250_000_000
    reveal = next(p for p in rig.publisher.posts if "We have a winner" in p)
    assert "non-holders win 25% of the pot" in reveal


def test_token_prize_needs_no_price_feed():
    """run_hunt(prize_fmml=...) must never touch usd_to_fmml for the PRIZE —
    launching no longer depends on FMML_USD_PRICE. (The holding floor is a
    separate story: a USD floor without a price fail-closes — see below — so
    here the floor is token-denominated.)"""
    rig = build_simulation()

    class NoPrice:
        def usd_to_fmml(self, usd):
            raise RuntimeError("price must not be needed for a token launch")

    rig.orchestrator._price_feed = NoPrice()
    rig.orchestrator._holding_floor_fmml = 1000  # token floor: no price involved
    hunt = rig.orchestrator.run_hunt(prize_fmml=500_000_000)
    assert hunt.state == HuntState.DONE
    assert rig.payout.sent[0]["amount"] == 500_000_000


def test_legacy_usd_launch_still_works_for_live_test():
    rig = build_simulation()
    hunt = rig.orchestrator.run_hunt(prize_usd=200)
    assert hunt.state == HuntState.DONE
    assert rig.payout.sent[0]["amount"] == 200_000  # fake feed: usd x 1000


def test_parser_rejects_infinities_and_absurd_amounts():
    for bad in ("inf", "nan", "1e400", "1e18", "9999999999999999b"):
        with pytest.raises(ValueError):
            parse_token_amount(bad)


def test_usd_floor_without_price_refuses_to_launch_before_dressing():
    """Fail-CLOSED (review): a USD floor with no price must refuse the launch
    — never silently run with floor 0 — and must refuse BEFORE any persona is
    acquired or dressed."""
    rig = build_simulation()
    orch = rig.orchestrator

    class NoPrice:
        def usd_to_fmml(self, usd):
            raise RuntimeError("FMML_USD_PRICE not set")

    orch._price_feed = NoPrice()
    orch._holding_floor_fmml = 0
    orch._holding_floor_usd = 20.0
    with pytest.raises(RuntimeError, match="Refusing to launch"):
        orch.run_hunt(prize_fmml=500_000_000)
    assert rig.dresser.dressed is False, "must refuse before dressing anyone"
    assert rig.persona_source.retired == []


def test_zero_floor_launches_fine_without_price():
    rig = build_simulation()
    orch = rig.orchestrator

    class NoPrice:
        def usd_to_fmml(self, usd):
            raise RuntimeError("no price")

    orch._price_feed = NoPrice()
    orch._holding_floor_fmml = 0
    orch._holding_floor_usd = 0.0
    hunt = orch.run_hunt(prize_fmml=500_000_000)
    assert hunt.state == HuntState.DONE


def test_zero_floor_clue_one_omits_the_split_line():
    """Floor 0 = holding OFF (Hunt #4 bridge): Clue 1 must NOT advertise the
    holder split — never announce a rule that isn't being enforced."""
    rig = build_simulation()
    orch = rig.orchestrator
    orch._holding_floor_fmml = 0
    orch._holding_floor_usd = 0.0
    orch.run_hunt(prize_fmml=500_000_000)
    clue1 = rig.publisher.posts[0]
    assert "non-holders" not in clue1
    assert "hold $FIND to win the full prize" not in clue1
    # The rest of the post is intact around the omitted line.
    assert "The first to find me wins 500,000,000 $FIND." in clue1
    assert "Reshare this post to enter." in clue1

"""Hunt #2 P0(b) — SubmissionAssembler unit tests.

The approved rule: code and wallet may arrive in separate messages; the
submission is complete when the pair is known; arrival order = created_at of
the COMPLETING message.
"""

from datetime import datetime, timezone

from finding_memeland.dm.assembler import SubmissionAssembler
from finding_memeland.dm.validator import parse_dm

W1 = "0x" + "a" * 40
W2 = "0x" + "b" * 40


def _t(minute):
    return datetime(2026, 7, 23, 19, minute, tzinfo=timezone.utc)


def _msg(sender, body, dm_id="1"):
    return parse_dm(dm_id, sender, body, expected_code_len=8)


def test_wallet_then_code_completes_on_second_message():
    a = SubmissionAssembler()
    assert a.feed(_msg("u1", f"my wallet {W1}", "10"), _t(0)) is None
    assert a.missing("u1") == "code"
    out = a.feed(_msg("u1", "the code is J89FFUU4", "11"), _t(1))
    assert out is not None
    assert out.parsed.wallet == W1
    assert "J89FFUU4" in out.parsed.claim_candidates
    assert out.completing_dm_id == "11"
    assert out.completed_at == _t(1)  # order = when the pair completed


def test_code_then_wallet_completes_on_second_message():
    a = SubmissionAssembler()
    assert a.feed(_msg("u1", "J89FFUU4", "10"), _t(0)) is None
    assert a.missing("u1") == "wallet"
    out = a.feed(_msg("u1", f"{W1}", "11"), _t(1))
    assert out is not None and out.completed_at == _t(1)


def test_wrong_code_then_right_code_accumulates_candidates():
    # The Bashit419 case: bad code + wallet first, correct code alone later.
    a = SubmissionAssembler()
    first = a.feed(_msg("u1", f"misanthrope WRONGCOD {W1}", "10"), _t(0))
    assert first is not None  # pair complete (wrong code) -> validated as bad_code
    a.mark_validated("u1")
    out = a.feed(_msg("u1", "ok then: J89FFUU4", "11"), _t(3))
    assert out is not None  # new candidate -> revalidate
    assert set(out.parsed.claim_candidates) >= {"WRONGCOD", "J89FFUU4"}
    assert out.completed_at == _t(3)


def test_wallet_correction_uses_latest_wallet():
    a = SubmissionAssembler()
    a.feed(_msg("u1", f"J89FFUU4 {W1}", "10"), _t(0))
    a.mark_validated("u1")
    out = a.feed(_msg("u1", f"typo, use this one {W2}", "11"), _t(2))
    assert out is not None and out.parsed.wallet == W2


def test_identical_state_does_not_revalidate():
    a = SubmissionAssembler()
    assert a.feed(_msg("u1", f"J89FFUU4 {W1}", "10"), _t(0)) is not None
    a.mark_validated("u1")
    # Same content again (player double-sends): no new validation burn.
    assert a.feed(_msg("u1", f"J89FFUU4 {W1}", "11"), _t(1)) is None


def test_unvalidated_state_retriggers_after_crashy_validation():
    """feed() must NOT self-mark: if validation raised, the retry re-assembles."""
    a = SubmissionAssembler()
    assert a.feed(_msg("u1", f"J89FFUU4 {W1}", "10"), _t(0)) is not None
    # validator crashed -> mark_validated never called -> same message retries
    assert a.feed(_msg("u1", f"J89FFUU4 {W1}", "10"), _t(0)) is not None


def test_senders_are_isolated():
    a = SubmissionAssembler()
    a.feed(_msg("u1", "J89FFUU4", "10"), _t(0))
    out = a.feed(_msg("u2", f"{W1}", "11"), _t(1))
    assert out is None  # u2's wallet does not complete u1's code
    assert a.missing("u1") == "wallet"
    assert a.missing("u2") == "code"


def test_missing_both_for_unknown_sender_and_empty_message():
    a = SubmissionAssembler()
    assert a.missing("ghost") == "both"
    a.feed(_msg("u1", "hello there", "10"), _t(0))
    assert a.missing("u1") == "both"

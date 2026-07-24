"""SubmissionAssembler — joins claim code and wallet across a sender's messages.

Hunt #2 lesson (approved rule, 2026-07-24): the rules never required code and
wallet in the SAME message, and penalizing that loses legitimate winners
(@Koyfiesa). The assembler keeps per-sender state within the hunt window:

- code candidates ACCUMULATE across messages (a wrong code followed by the
  right one must not lock the player out);
- the latest parseable wallet wins (a player may correct a typo'd address);
- a submission is COMPLETE when both a wallet and >=1 code candidate are known;
- fairness rule: the submission's arrival order is the created_at of the
  message that COMPLETED the pair. Deterministic, publishable, auditable.

Messages from the prep window (before live_at) are NEVER fed here — windows
are watertight: a code sent before Clue 1 cannot complete a pair after T0.

Pure module: no I/O, fully unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .validator import ParsedDM


@dataclass
class _SenderState:
    codes: list[str] = field(default_factory=list)   # accumulated, upper, dedup
    wallet: str | None = None
    last_validated: tuple | None = None              # (codes tuple, wallet) fingerprint


@dataclass(frozen=True)
class Assembled:
    """A complete (wallet + >=1 code) submission, ready for validation."""
    parsed: ParsedDM               # dm_id = the COMPLETING message's dm_id
    completed_at: datetime         # created_at of the completing message
    completing_dm_id: str


class SubmissionAssembler:
    def __init__(self):
        self._senders: dict[str, _SenderState] = {}

    def feed(self, parsed: ParsedDM, created_at: datetime) -> Assembled | None:
        """Fold one message into the sender's state. Returns an Assembled
        submission when the pair is complete AND the state changed since the
        last SUCCESSFUL validation (so a repeated identical message doesn't
        burn paid validation calls), else None.

        IMPORTANT: feed() does NOT mark the state as validated — the caller
        calls mark_validated() only after the validator actually ran. If
        validation raises (X lookup down), the retry re-assembles the same
        pair instead of silently degrading to 'partial'."""
        st = self._senders.setdefault(parsed.sender_x_id, _SenderState())
        for c in parsed.claim_candidates:
            if c not in st.codes:
                st.codes.append(c)
        if parsed.wallet:
            st.wallet = parsed.wallet

        if not st.codes or not st.wallet:
            return None
        fingerprint = (tuple(st.codes), st.wallet)
        if fingerprint == st.last_validated:
            return None  # nothing new — don't revalidate identical state
        assembled = ParsedDM(
            dm_id=parsed.dm_id,
            sender_x_id=parsed.sender_x_id,
            wallet=st.wallet,
            claim_code=st.codes[0],
            claim_candidates=tuple(st.codes),
        )
        return Assembled(
            parsed=assembled, completed_at=created_at, completing_dm_id=parsed.dm_id
        )

    def mark_validated(self, sender_x_id: str) -> None:
        """Record that the sender's CURRENT assembled state went through the
        validator (whatever the outcome). Only then does an identical state
        stop re-triggering validation."""
        st = self._senders.get(sender_x_id)
        if st is not None and st.codes and st.wallet:
            st.last_validated = (tuple(st.codes), st.wallet)

    def missing(self, sender_x_id: str) -> str | None:
        """What this sender still owes: 'wallet', 'code', or None if complete.
        Drives the canned reply ('got the code — now the wallet address')."""
        st = self._senders.get(sender_x_id)
        if st is None or (not st.codes and not st.wallet):
            return "both"
        if not st.wallet:
            return "wallet"
        if not st.codes:
            return "code"
        return None

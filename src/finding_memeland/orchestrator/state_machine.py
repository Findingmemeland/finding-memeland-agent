"""Orchestrator — the hunt lifecycle state machine.

States (frozen, mirrors db hunt_state):

    idle -> preparing -> live -> resolving -> paying
         -> pending_cleanup (1h reveal) -> retiring -> done
    (any live phase -> voided on platform interruption)

Implemented as a plain, deterministic state machine (not LangGraph): the flow is
sequential with timers and external events, not LLM-routed, so a graph framework
would add complexity without benefit and hurt testability.

The Orchestrator is wired against ports.py interfaces, so the exact same flow
runs against real services OR in-memory fakes (simulation.py) for a full local
dry-run. The clue/DM phase is modelled as a discrete poll loop driven by an
injected Clock; the real DM cadence (20s polling, 1-3h between clues) is refined
when the live DM listener lands (step 26).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from ..content.clue_engine import PersonaContext, next_clue_due
from ..content.integrity import compute_integrity_hash, generate_claim_code, generate_salt
from ..content.templates import (
    DM_REPLY_BAD_CODE,
    DM_REPLY_LATE,
    DM_REPLY_NO_ADDRESS,
    DM_REPLY_NO_HOLDING,
    DM_REPLY_NO_RESHARE,
    WinnerData,
    clue_followup,
    clue_one,
    winner_announcement,
)
from ..dm.validator import parse_dm
from .ports import PayoutReceipt, ReadyPersona, Winner


class HuntState(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    LIVE = "live"
    RESOLVING = "resolving"
    PAYING = "paying"
    PENDING_CLEANUP = "pending_cleanup"
    RETIRING = "retiring"
    DONE = "done"
    VOIDED = "voided"


# Allowed transitions. Any move not listed here is a bug and the supervisor halts.
TRANSITIONS: dict[HuntState, set[HuntState]] = {
    HuntState.IDLE: {HuntState.PREPARING},
    HuntState.PREPARING: {HuntState.LIVE, HuntState.VOIDED},
    HuntState.LIVE: {HuntState.RESOLVING, HuntState.VOIDED},
    HuntState.RESOLVING: {HuntState.PAYING, HuntState.VOIDED},
    HuntState.PAYING: {HuntState.PENDING_CLEANUP, HuntState.VOIDED},
    HuntState.PENDING_CLEANUP: {HuntState.RETIRING},
    HuntState.RETIRING: {HuntState.DONE},
    HuntState.DONE: set(),
    HuntState.VOIDED: {HuntState.RETIRING},
}

CLEANUP_WINDOW_SECONDS = 60 * 60  # 1h reveal window before retiring the persona


def can_transition(src: HuntState, dst: HuntState) -> bool:
    return dst in TRANSITIONS.get(src, set())


_REPLY_BY_OUTCOME = {
    "malformed": DM_REPLY_NO_ADDRESS,
    "bad_code": DM_REPLY_BAD_CODE,
    "no_holding": DM_REPLY_NO_HOLDING,
    "no_reshare": DM_REPLY_NO_RESHARE,
    "late": DM_REPLY_LATE,
}


@dataclass
class PreparedHunt:
    id: int
    persona: ReadyPersona
    identity: object              # GeneratedPersona
    ctx: PersonaContext
    claim_code: str
    salt: str
    integrity_hash: str
    prize_usd: float
    prize_fmml: int
    min_balance_fmml: int
    holding_hours: int
    reshare_post_id: str | None = None
    clues: list[str] = field(default_factory=list)
    state: HuntState = HuntState.IDLE
    started_at: datetime | None = None


class Orchestrator:
    """Runs one hunt end to end. Collaborators are injected (see ports.py)."""

    def __init__(
        self,
        *,
        settings,
        clock,
        repo,
        persona_source,
        persona_generator,
        avatar_generator,
        dresser,
        publisher,
        clue_engine,
        dm_source,
        validator,
        payout,
        price_feed,
        notifier,
        hunt_number: int = 1,
        register: str = "medium",
        holding_floor_usd: float = 50.0,
        holding_hours: int = 24,
        poll_interval_s: int = 75,  # DM-read rate-limit safe (~15 req/15min); winner = DM arrival order, so slower polling never changes who wins
        max_rounds: int = 100_000,
        avatar_writer=None,
        clue_due_fn=None,
        cleanup_window_s: int = CLEANUP_WINDOW_SECONDS,
        undress_on_retire: bool = False,
        control=None,
        hunt_timeout_hours: float | None = 72,
    ):
        self._settings = settings
        self._clock = clock
        self._repo = repo
        self._persona_source = persona_source
        self._persona_generator = persona_generator
        self._avatar_generator = avatar_generator
        self._dresser = dresser
        self._publisher = publisher
        self._clue_engine = clue_engine
        self._dm_source = dm_source
        self._validator = validator
        self._payout = payout
        self._price_feed = price_feed
        self._notifier = notifier
        self._hunt_number = hunt_number
        self._register = register
        self._holding_floor_usd = holding_floor_usd
        self._holding_hours = holding_hours
        self._poll_interval_s = poll_interval_s
        self._max_rounds = max_rounds
        self._avatar_writer = avatar_writer  # callable(bytes) -> path, or None
        # Cadence hooks: defaults preserve production (1-3h between clues, 1h reveal).
        # The live-test harness injects short intervals so a rehearsal runs in minutes.
        self._clue_due_fn = clue_due_fn or next_clue_due
        self._cleanup_window_s = cleanup_window_s
        # DESIGN (Pedro, 2026-07-05): personas are single-use, so in REAL hunts
        # the profile is never undressed — it stays up as the hunt's historical
        # artifact (the claim code is public after the reveal anyway). Only the
        # live test (operator's own account) and voided-before-live hunts undress.
        self._undress_on_retire = undress_on_retire
        # Kill switch: an object with .paused() -> bool (see runtime.HuntControl).
        # While paused the loop idles: no clues, no DM processing, no paying.
        self._control = control
        # Unclaimed-hunt deadline: past it, the hunt is VOIDED with a public
        # notice instead of posting clues forever. None disables.
        self._hunt_timeout_h = hunt_timeout_hours

    # ------------------------------------------------------------------
    def run_hunt(self, prize_usd: float | None = None) -> PreparedHunt:
        self._settings.assert_ready_for_hunt()
        hunt = self._prepare(prize_usd if prize_usd is not None else self._settings.prize_usd_max)
        self._go_live(hunt)
        winner = self._clue_and_dm_loop(hunt)
        if winner is None:  # deadline passed unclaimed -> voided inside the loop
            return hunt
        receipt = self._pay(hunt, winner)
        self._reveal(hunt, winner, receipt)
        self._retire(hunt)
        return hunt

    # ------------------------------------------------------------------
    def _transition(self, hunt: PreparedHunt, dst: HuntState, **fields) -> None:
        if not can_transition(hunt.state, dst):
            raise RuntimeError(f"illegal transition {hunt.state} -> {dst}")
        hunt.state = dst
        self._repo.set_hunt_state(hunt.id, dst.value, **fields)

    def _notify(self, text: str) -> None:
        self._notifier.notify(text)

    # ------------------------------------------------------------------
    def _prepare(self, prize_usd: float) -> PreparedHunt:
        persona = self._persona_source.acquire_ready()
        identity = self._persona_generator.generate(register=self._register)
        claim_code = generate_claim_code()
        salt = generate_salt()
        integrity_hash = compute_integrity_hash(persona.x_user_id, claim_code, salt)

        prize_fmml = self._price_feed.usd_to_fmml(prize_usd)
        min_balance_fmml = self._price_feed.usd_to_fmml(self._holding_floor_usd)

        avatar_path = None
        png = self._avatar_generator.generate_png(identity.avatar_prompt)
        if png and self._avatar_writer is not None:
            avatar_path = self._avatar_writer(png)

        banner_path = None
        bpng = self._avatar_generator.generate_banner_png(identity.banner_prompt)
        if bpng and self._avatar_writer is not None:
            banner_path = self._avatar_writer(bpng)

        self._dresser.dress(
            access_token=persona.access_token,
            access_secret=persona.access_secret,
            identity=identity,
            claim_code=claim_code,
            avatar_path=avatar_path,
            banner_path=banner_path,
        )

        started_at = self._clock.now()
        base_fields = dict(
            persona_id=persona.id,
            persona_display_name=identity.display_name,
            persona_bio=identity.bio,
            claim_code=claim_code,
            integrity_salt=salt,
            integrity_hash=integrity_hash,
            prize_usd=prize_usd,
            prize_fmml=prize_fmml,
            min_balance_fmml=min_balance_fmml,
            holding_hours=self._holding_hours,
            started_at=started_at,
            state=HuntState.PREPARING.value,
        )
        try:
            # persona_identity (jsonb) is what makes a full crash-resume possible:
            # it lets a restarted agent rebuild the clue context. Column added in
            # schema.sql — if the live DB predates it, fall back gracefully.
            hunt_id = self._repo.create_hunt(
                **base_fields, persona_identity=asdict(identity)
            )
        except Exception:  # noqa: BLE001 — e.g. column missing on an older DB
            hunt_id = self._repo.create_hunt(**base_fields)
            self._notify(
                "hunts.persona_identity missing in the DB — this hunt can only be "
                "resumed in degraded mode after a restart. Run: alter table hunts "
                "add column if not exists persona_identity jsonb;"
            )
        hunt = PreparedHunt(
            id=hunt_id,
            persona=persona,
            identity=identity,
            ctx=PersonaContext.from_generated(identity, persona.handle),
            claim_code=claim_code,
            salt=salt,
            integrity_hash=integrity_hash,
            prize_usd=prize_usd,
            prize_fmml=prize_fmml,
            min_balance_fmml=min_balance_fmml,
            holding_hours=self._holding_hours,
            state=HuntState.PREPARING,
            started_at=started_at,
        )
        self._notify(f"hunt #{self._hunt_number}: persona {persona.handle} dressed, preparing")
        return hunt

    def _go_live(self, hunt: PreparedHunt) -> None:
        draft = self._clue_engine.next_clue(hunt.ctx, 1, [])
        post = clue_one(
            hunt_n=self._hunt_number,
            clue_text=draft.text,
            prize=f"{hunt.prize_fmml:,}",
            integrity_hash=hunt.integrity_hash,
        )
        tweet_id = self._publisher.post(post, long_post=True)
        hunt.reshare_post_id = tweet_id
        hunt.clues.append(draft.text)
        self._repo.record_clue(
            hunt_id=hunt.id, clue_index=1, clue_text=draft.text, tweet_id=tweet_id
        )
        # reshare_post_id persisted so a restarted agent keeps the SAME gate.
        self._transition(hunt, HuntState.LIVE, reshare_post_id=tweet_id)
        self._notify(f"hunt #{self._hunt_number} LIVE — clue 1 posted ({tweet_id})")

    # A submission whose processing keeps erroring (X lookup down, DB hiccup) is
    # retried this many times before being skipped, so one poisoned DM can never
    # stall the queue forever.
    _MAX_SUBMISSION_RETRIES = 3

    def _clue_and_dm_loop(
        self, hunt: PreparedHunt, *, since: str | None = None, clue_index: int | None = None
    ) -> Winner:
        """The live loop. DESIGN RULE: once a hunt is LIVE, people are playing —
        NOTHING transient (X 429/5xx, network, RPC, DB hiccup, bad LLM output)
        may kill this loop. Every phase is isolated: a failure is notified,
        backed off, and retried; only the winner path exits.

        `since`/`clue_index` allow a crash-resumed hunt to re-enter the loop
        exactly where it stopped (see resume_hunts)."""
        clue_index = clue_index if clue_index is not None else max(1, len(hunt.clues))
        next_due = self._clue_due_fn(self._clock.now())
        poll_failures = 0
        sub_retries: dict[str, int] = {}
        pause_notified = False

        for _ in range(self._max_rounds):
            # ---- Phase 0: kill switch (operator's /silence) ----
            if self._control is not None and self._control.paused():
                if not pause_notified:
                    self._notify("⏸ hunt PAUSED by operator — idling (/resume to continue)")
                    pause_notified = True
                self._clock.sleep(self._poll_interval_s)
                continue
            if pause_notified:
                self._notify("▶️ hunt RESUMED by operator")
                pause_notified = False

            # ---- Phase 0b: unclaimed-hunt deadline ----
            if (
                self._hunt_timeout_h is not None
                and hunt.started_at is not None
                and self._clock.now() >= hunt.started_at + timedelta(hours=self._hunt_timeout_h)
            ):
                self._void_unclaimed(hunt)
                return None

            # ---- Phase 1: read DMs (isolated — a failed poll is retried) ----
            try:
                batch = sorted(self._dm_source.poll(since), key=lambda s: s.created_at)
                poll_failures = 0
            except Exception as e:  # noqa: BLE001
                batch = []
                poll_failures += 1
                self._notify(
                    f"DM poll failed ({poll_failures}x, retrying): {e!r}"
                )
                # extra backoff on top of the normal sleep, capped at 5 min
                self._clock.sleep(min(300, self._poll_interval_s * min(poll_failures, 4)))

            # ---- Phase 2: process submissions (isolated per submission) ----
            for sub in batch:
                # Ignore DMs from BEFORE this hunt started (old conversations are
                # not submissions). Without this the agent would re-process every
                # historical DM each hunt — spamming past contacts with the canned
                # reply and burning API credits.
                if hunt.started_at is not None and sub.created_at < hunt.started_at:
                    since = sub.dm_id
                    continue
                try:
                    parsed = parse_dm(
                        sub.dm_id, sub.sender_x_id, sub.body,
                        expected_code_len=len(hunt.claim_code),
                    )
                    res = self._validator.validate(parsed, hunt)
                    self._repo.log_submission(
                        hunt_id=hunt.id, dm_id=sub.dm_id, sender_x_id=sub.sender_x_id,
                        wallet=parsed.wallet, outcome=res.outcome, x_created_at=sub.created_at,
                    )
                except Exception as e:  # noqa: BLE001
                    tries = sub_retries.get(sub.dm_id, 0) + 1
                    sub_retries[sub.dm_id] = tries
                    if tries < self._MAX_SUBMISSION_RETRIES:
                        # Do NOT advance the marker: this DM is re-read and
                        # retried on the next poll, preserving arrival order.
                        self._notify(
                            f"submission {sub.dm_id} failed (attempt {tries}, "
                            f"will retry): {e!r}"
                        )
                        break
                    since = sub.dm_id  # give up on this one; don't stall the queue
                    self._notify(
                        f"submission {sub.dm_id} SKIPPED after "
                        f"{tries} failed attempts: {e!r} — review it manually."
                    )
                    continue
                since = sub.dm_id
                if res.won:
                    self._transition(hunt, HuntState.RESOLVING)
                    self._notify(f"winner: @{sub.sender_handle}")
                    return Winner(submission=sub, wallet=parsed.wallet)
                reply = _REPLY_BY_OUTCOME.get(res.outcome)
                if reply:
                    # Courtesy loss-notice is best-effort: a failed reply (e.g. DM
                    # send restrictions) must NEVER abort the hunt. The winner is
                    # paid on-chain + announced publicly; no DM is required.
                    try:
                        self._publisher.reply_dm(sub.sender_x_id, reply)
                    except Exception as e:  # noqa: BLE001
                        self._notify(f"reply to @{sub.sender_handle} failed (non-fatal): {e!r}")

            # ---- Phase 3: post the next clue (isolated — a failed clue is a
            # skipped round, never a dead hunt) ----
            if self._clock.now() >= next_due:
                clue_index += 1
                try:
                    draft = self._clue_engine.next_clue(hunt.ctx, clue_index, hunt.clues)
                    tweet_id = self._publisher.post(
                        clue_followup(clue_index, draft.text, draft.taunt or "")
                    )
                except Exception as e:  # noqa: BLE001
                    # Guardrails exhausted, X post failed, LLM down — skip this
                    # round, alert the operator, try again next window.
                    clue_index -= 1
                    self._notify(f"clue generation failed (skipping this round): {e}")
                else:
                    # The clue IS on X now — bookkeeping failures must not make
                    # us repeat it. Record best-effort.
                    hunt.clues.append(draft.text)
                    try:
                        self._repo.record_clue(
                            hunt_id=hunt.id, clue_index=clue_index,
                            clue_text=draft.text, tweet_id=tweet_id,
                        )
                    except Exception as e:  # noqa: BLE001
                        self._notify(f"record_clue failed (non-fatal): {e!r}")
                next_due = self._clue_due_fn(self._clock.now())

            self._clock.sleep(self._poll_interval_s)

        raise RuntimeError("hunt loop exceeded max rounds without a winner")

    def _void_unclaimed(self, hunt: PreparedHunt) -> None:
        """Nobody won before the deadline: end the hunt publicly and cleanly.
        The persona IS undressed here (unlike a completed hunt) — leaving a live
        claim code in the bio of a dead hunt would mislead players."""
        hours = self._hunt_timeout_h
        self._notify(f"hunt #{self._hunt_number} expired unclaimed after {hours}h — voiding.")
        self._transition(hunt, HuntState.VOIDED)
        try:
            self._publisher.post(
                f"Hunt #{self._hunt_number} ends unclaimed. The persona keeps its "
                "secret and the prize returns to the treasury. Sharpen up — the "
                "next hunt won't wait for you. 🏴"
            )
        except Exception as e:  # noqa: BLE001
            self._notify(f"void notice post failed (non-fatal): {e!r}")
        self._transition(hunt, HuntState.RETIRING)
        try:
            self._dresser.retire(
                access_token=hunt.persona.access_token,
                access_secret=hunt.persona.access_secret,
            )
        except Exception as e:  # noqa: BLE001
            self._notify(f"undress of voided persona failed: {e!r} — reset it manually.")
        self._persona_source.mark_retired(hunt.persona.id)
        self._transition(hunt, HuntState.DONE)

    def _pay(self, hunt: PreparedHunt, winner: Winner):
        """Idempotent payout. Invariant: at most ONE transfer per hunt, ever.

        Order of operations is the whole point:
          1. any existing payout row for this hunt?
             - sent/confirmed with a tx_hash -> money is on-chain; REUSE it.
             - anything else (sending/unknown) -> a transfer may be in flight;
               ABORT loudly, human settles. Never guess with money.
          2. write the INTENT row (status='sending') BEFORE broadcasting
          3. transfer
          4. mark the row 'sent' (crash between 3 and 4 leaves 'sending',
             which step 1 then refuses to retry blindly)
        """
        self._transition(hunt, HuntState.PAYING)

        for row in self._repo.payouts_for_hunt(hunt.id):
            if row.get("status") in ("sent", "confirmed") and row.get("tx_hash"):
                self._notify(
                    f"payout for hunt #{hunt.id} already on-chain "
                    f"({row['tx_hash']}) — reusing it, NOT re-sending."
                )
                return PayoutReceipt(
                    tx_hash=row["tx_hash"],
                    amount_fmml=_as_int(row.get("amount_fmml") or hunt.prize_fmml),
                )
            raise RuntimeError(
                f"unresolved payout intent for hunt #{hunt.id} "
                f"(status {row.get('status')!r}) — a transfer may be in flight. "
                "Check the chain (hot wallet nonce/txs) and settle manually."
            )

        intent_id = self._repo.create_payout_intent(
            hunt_id=hunt.id, wallet=winner.wallet, amount_fmml=hunt.prize_fmml
        )
        try:
            receipt = self._payout.send_prize(
                hunt_id=hunt.id, to_wallet=winner.wallet, amount_fmml=hunt.prize_fmml
            )
        except Exception as e:
            # The tx MAY have been broadcast (e.g. receipt timeout). Mark it so
            # nothing ever auto-retries this hunt's payout.
            try:
                self._repo.set_payout_status(intent_id, "unknown", error=repr(e))
            except Exception as e2:  # noqa: BLE001
                self._notify(f"could not mark payout intent as unknown: {e2!r}")
            self._notify(
                f"🚨 payout for hunt #{hunt.id} errored MID-SEND: {e!r}. The tx "
                "may still mine — check the chain before ANY manual retry."
            )
            raise
        self._repo.set_payout_status(intent_id, "sent", tx_hash=receipt.tx_hash)
        self._repo.record_winner(
            hunt_id=hunt.id, winner_x_id=winner.submission.sender_x_id,
            wallet=winner.wallet, prize_fmml=hunt.prize_fmml,
        )
        self._notify(f"paid {hunt.prize_fmml:,} $FIND to {winner.wallet} ({receipt.tx_hash})")
        return receipt

    def _reveal(self, hunt: PreparedHunt, winner: Winner, receipt) -> None:
        now = self._clock.now()
        self._transition(
            hunt, HuntState.PENDING_CLEANUP,
            resolved_at=now, cleanup_due_at=now + timedelta(seconds=self._cleanup_window_s),
        )
        elapsed = self._clock.now() - hunt.started_at if hunt.started_at else None
        data = WinnerData(
            hunt_n=self._hunt_number,
            winner_handle=winner.submission.sender_handle,
            time_to_win=_fmt_duration(elapsed),
            prize_amount=f"{hunt.prize_fmml:,}",
            tx_link=receipt.tx_hash,
            persona_handle=hunt.persona.handle,
            persona_user_id=hunt.persona.x_user_id,
            claim_code=hunt.claim_code,
            salt=hunt.salt,
        )
        self._publisher.post(winner_announcement(data), long_post=True)
        self._clock.sleep(self._cleanup_window_s)  # reveal window (1h prod; short in test)

    def _retire(self, hunt: PreparedHunt) -> None:
        self._transition(hunt, HuntState.RETIRING)
        self._finish_retire(hunt)

    def _finish_retire(self, hunt: PreparedHunt) -> None:
        """The RETIRING -> DONE tail. Split out so a crash-resumed hunt that was
        already mid-retire can finish without an illegal re-transition.

        In REAL hunts the profile is NOT undressed (single-use personas; the
        dressed profile stays as the hunt's public artifact). When undressing IS
        enabled (live test), it's best-effort: at this point the winner is paid
        and announced — a flaky X profile endpoint (the known 500/131) must not
        crash the hunt."""
        if self._undress_on_retire:
            try:
                self._dresser.retire(
                    access_token=hunt.persona.access_token,
                    access_secret=hunt.persona.access_secret,
                )
            except Exception as e:  # noqa: BLE001
                self._notify(
                    f"⚠️ could not undress persona {hunt.persona.handle}: {e!r} — "
                    "reset the profile manually; the hunt itself is complete."
                )
        self._persona_source.mark_retired(hunt.persona.id)
        log = self._repo.submissions_for_hunt(hunt.id)
        self._publisher.post(
            f"Hunt #{self._hunt_number} closed. {len(log)} submissions logged for public audit."
        )
        self._transition(hunt, HuntState.DONE)
        self._notify(f"hunt #{self._hunt_number} done; persona {hunt.persona.handle} retired")

    # ------------------------------------------------------------------
    # Crash recovery — called once at boot (main.py). Finds hunts the previous
    # process left in a non-terminal state and picks each one up where it
    # stopped. Money-adjacent states (RESOLVING/PAYING) are NEVER auto-resumed:
    # without payout idempotency, a blind retry could double-pay.
    # ------------------------------------------------------------------
    def resume_hunts(self) -> int:
        try:
            rows = self._repo.active_hunts()
        except Exception as e:  # noqa: BLE001
            self._notify(f"resume check failed (continuing idle): {e!r}")
            return 0
        if not rows:
            return 0
        resumed = 0
        for row in rows:
            try:
                self._resume_one(row)
                resumed += 1
            except Exception as e:  # noqa: BLE001
                self._notify(
                    f"🚨 could not resume hunt #{row.get('id')} "
                    f"(state {row.get('state')}): {e!r} — intervene manually: the "
                    "persona may still be dressed with a live claim code."
                )
        return resumed

    def _resume_one(self, row: dict) -> None:
        state = HuntState(row["state"])
        hunt = self._rebuild_hunt(row, state)

        if state is HuntState.PREPARING:
            # Never went LIVE (no players yet). Cheapest safe move: void it and
            # undress the persona; a fresh /launch starts clean.
            self._notify(f"hunt #{hunt.id} was stuck in PREPARING — voiding it.")
            self._transition(hunt, HuntState.VOIDED)
            self._transition(hunt, HuntState.RETIRING)
            try:
                self._dresser.retire(
                    access_token=hunt.persona.access_token,
                    access_secret=hunt.persona.access_secret,
                )
            except Exception as e:  # noqa: BLE001
                self._notify(f"retire of voided persona failed: {e!r} — undress it manually.")
            self._persona_source.mark_retired(hunt.persona.id)
            self._transition(hunt, HuntState.DONE)
            return

        if state is HuntState.LIVE:
            won_rows = [
                s for s in self._repo.submissions_for_hunt(hunt.id)
                if s.get("outcome") == "won"
            ]
            if won_rows:
                # Winner was validated but the process died before RESOLVING.
                # Money territory — human eyes required.
                self._notify(
                    f"🚨 hunt #{hunt.id} already has a WON submission "
                    f"(dm {won_rows[0].get('dm_id')}) but died before paying. "
                    "NOT auto-paying — verify and settle manually."
                )
                return
            since = self._latest_dm_marker(hunt.id)
            if hunt.ctx is None:
                self._notify(
                    f"hunt #{hunt.id} resumed WITHOUT persona identity (old DB "
                    "schema): DMs and the winner flow work, but no further clues "
                    "can be generated."
                )
            self._notify(
                f"hunt #{hunt.id} RESUMED live after a restart — "
                f"{len(hunt.clues)} clues out, DM marker {since or 'start'}."
            )
            winner = self._clue_and_dm_loop(hunt, since=since)
            receipt = self._pay(hunt, winner)
            self._reveal(hunt, winner, receipt)
            self._retire(hunt)
            return

        if state in (HuntState.RESOLVING, HuntState.PAYING):
            self._notify(
                f"🚨 hunt #{hunt.id} died in {state.value.upper()} — a payout may "
                "or may not have gone out. NOT auto-resuming: check the payouts "
                "table and the chain, then settle manually."
            )
            return

        if state is HuntState.PENDING_CLEANUP:
            # Winner paid & announced; only the reveal window + retire remain.
            due = _as_dt(row.get("cleanup_due_at"))
            now = self._clock.now()
            if due is not None and due > now:
                self._clock.sleep((due - now).total_seconds())
            self._notify(f"hunt #{hunt.id} resumed at cleanup — retiring the persona.")
            self._retire(hunt)
            return

        if state is HuntState.RETIRING:
            self._notify(f"hunt #{hunt.id} resumed mid-retire — finishing.")
            self._finish_retire(hunt)
            return

    def _rebuild_hunt(self, row: dict, state: HuntState) -> PreparedHunt:
        persona = self._persona_source.acquire_by_id(row["persona_id"])

        identity = None
        ctx = None
        payload = row.get("persona_identity")
        if payload:
            from ..persona.generator import GeneratedPersona

            if isinstance(payload, str):
                payload = json.loads(payload)
            identity = GeneratedPersona(**payload)
            ctx = PersonaContext.from_generated(identity, persona.handle)

        clue_rows = self._repo.clues_for_hunt(row["id"])
        reshare = row.get("reshare_post_id") or next(
            (c.get("tweet_id") for c in clue_rows if c.get("clue_index") == 1), None
        )
        return PreparedHunt(
            id=row["id"],
            persona=persona,
            identity=identity,
            ctx=ctx,
            claim_code=row["claim_code"],
            salt=row["integrity_salt"],
            integrity_hash=row["integrity_hash"],
            prize_usd=float(row.get("prize_usd") or 0),
            prize_fmml=_as_int(row.get("prize_fmml")),
            min_balance_fmml=_as_int(row.get("min_balance_fmml")),
            holding_hours=int(row.get("holding_hours") or self._holding_hours),
            reshare_post_id=reshare,
            clues=[c["clue_text"] for c in clue_rows],
            state=state,
            started_at=_as_dt(row.get("started_at")),
        )

    def _latest_dm_marker(self, hunt_id: int) -> str | None:
        """Highest processed dm_id from the submission log = where to resume the
        DM stream. DMs read-but-not-logged before the crash are simply re-read."""
        ids = [
            int(s["dm_id"]) for s in self._repo.submissions_for_hunt(hunt_id)
            if s.get("dm_id") and str(s["dm_id"]).isdigit()
        ]
        return str(max(ids)) if ids else None


def _as_dt(v) -> datetime | None:
    """Rows from Supabase carry ISO strings; fakes carry datetimes."""
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _as_int(v) -> int:
    if v is None:
        return 0
    return int(float(v))


def _fmt_duration(delta) -> str:
    if delta is None:
        return "unknown"
    total = int(delta.total_seconds())
    h, rem = divmod(total, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m"

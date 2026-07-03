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

from dataclasses import dataclass, field
from datetime import datetime
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
from .ports import ReadyPersona, Winner


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

    # ------------------------------------------------------------------
    def run_hunt(self, prize_usd: float | None = None) -> PreparedHunt:
        self._settings.assert_ready_for_hunt()
        hunt = self._prepare(prize_usd if prize_usd is not None else self._settings.prize_usd_max)
        self._go_live(hunt)
        winner = self._clue_and_dm_loop(hunt)
        receipt = self._pay(hunt, winner)
        self._reveal(hunt, winner, receipt)
        self._retire(hunt)
        return hunt

    # ------------------------------------------------------------------
    def _transition(self, hunt: PreparedHunt, dst: HuntState) -> None:
        if not can_transition(hunt.state, dst):
            raise RuntimeError(f"illegal transition {hunt.state} -> {dst}")
        hunt.state = dst
        self._repo.set_hunt_state(hunt.id, dst.value)

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

        hunt_id = self._repo.create_hunt(
            persona_id=persona.id,
            claim_code=claim_code,
            integrity_salt=salt,
            integrity_hash=integrity_hash,
            prize_usd=prize_usd,
            prize_fmml=prize_fmml,
            min_balance_fmml=min_balance_fmml,
            holding_hours=self._holding_hours,
            state=HuntState.PREPARING.value,
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
            started_at=self._clock.now(),
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
        self._transition(hunt, HuntState.LIVE)
        self._notify(f"hunt #{self._hunt_number} LIVE — clue 1 posted ({tweet_id})")

    def _clue_and_dm_loop(self, hunt: PreparedHunt) -> Winner:
        since: str | None = None
        clue_index = 1
        next_due = self._clue_due_fn(self._clock.now())

        for _ in range(self._max_rounds):
            for sub in sorted(self._dm_source.poll(since), key=lambda s: s.created_at):
                since = sub.dm_id  # advance the marker even for skipped DMs
                # Ignore DMs from BEFORE this hunt started (old conversations are
                # not submissions). Without this the agent would re-process every
                # historical DM each hunt — spamming past contacts with the canned
                # reply and burning API credits.
                if hunt.started_at is not None and sub.created_at < hunt.started_at:
                    continue
                parsed = parse_dm(
                    sub.dm_id, sub.sender_x_id, sub.body,
                    expected_code_len=len(hunt.claim_code),
                )
                res = self._validator.validate(parsed, hunt)
                self._repo.log_submission(
                    hunt_id=hunt.id, dm_id=sub.dm_id, sender_x_id=sub.sender_x_id,
                    wallet=parsed.wallet, outcome=res.outcome, x_created_at=sub.created_at,
                )
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

            if self._clock.now() >= next_due:
                clue_index += 1
                draft = self._clue_engine.next_clue(hunt.ctx, clue_index, hunt.clues)
                tweet_id = self._publisher.post(
                    clue_followup(clue_index, draft.text, draft.taunt or "")
                )
                hunt.clues.append(draft.text)
                self._repo.record_clue(
                    hunt_id=hunt.id, clue_index=clue_index,
                    clue_text=draft.text, tweet_id=tweet_id,
                )
                next_due = self._clue_due_fn(self._clock.now())

            self._clock.sleep(self._poll_interval_s)

        raise RuntimeError("hunt loop exceeded max rounds without a winner")

    def _pay(self, hunt: PreparedHunt, winner: Winner):
        self._transition(hunt, HuntState.PAYING)
        receipt = self._payout.send_prize(
            hunt_id=hunt.id, to_wallet=winner.wallet, amount_fmml=hunt.prize_fmml
        )
        self._repo.record_winner(
            hunt_id=hunt.id, winner_x_id=winner.submission.sender_x_id,
            wallet=winner.wallet, prize_fmml=hunt.prize_fmml,
        )
        self._repo.record_payout(
            hunt_id=hunt.id, wallet=winner.wallet,
            amount_fmml=hunt.prize_fmml, tx_hash=receipt.tx_hash, status="sent",
        )
        self._notify(f"paid {hunt.prize_fmml:,} $FIND to {winner.wallet} ({receipt.tx_hash})")
        return receipt

    def _reveal(self, hunt: PreparedHunt, winner: Winner, receipt) -> None:
        self._transition(hunt, HuntState.PENDING_CLEANUP)
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
        self._dresser.retire(
            access_token=hunt.persona.access_token,
            access_secret=hunt.persona.access_secret,
        )
        self._persona_source.mark_retired(hunt.persona.id)
        log = self._repo.submissions_for_hunt(hunt.id)
        self._publisher.post(
            f"Hunt #{self._hunt_number} closed. {len(log)} submissions logged for public audit."
        )
        self._transition(hunt, HuntState.DONE)
        self._notify(f"hunt #{self._hunt_number} done; persona {hunt.persona.handle} retired")


def _fmt_duration(delta) -> str:
    if delta is None:
        return "unknown"
    total = int(delta.total_seconds())
    h, rem = divmod(total, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m"

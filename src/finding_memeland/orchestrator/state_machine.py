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
from ..claims.parser import code_like, extract_candidates, extract_wallet
from ..content.templates import (
    CLUE_FOLLOWUP_CLAIM_HINT,
    DM_REPLY_BAD_CODE,
    DM_REPLY_EARLY,
    DM_REPLY_LATE,
    DM_REPLY_NEED_CODE,
    DM_REPLY_NEED_WALLET,
    DM_REPLY_NO_ADDRESS,
    DM_REPLY_NO_HOLDING,
    DM_REPLY_NO_RESHARE,
    POST_REPLY_EARLY,
    POST_REPLY_INVALID_WALLET,
    POST_REPLY_LATE,
    POST_REPLY_MISSING_REPOST,
    POST_REPLY_NO_HOLDING,
    POST_REPLY_TIMED_OUT,
    POST_REPLY_WRONG_DOOR,
    WinnerData,
    clue_followup,
    clue_one,
    post_reply_win,
    winner_announcement,
)
from ..dm.assembler import SubmissionAssembler
from ..dm.validator import ParsedDM, parse_dm, screen_bot
from .ports import PayoutReceipt, ReadyPersona, Submission, Winner


class HuntState(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    PREPPED = "prepped"      # dressed + prep posts running; Clue 1 not out (P2)
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
    HuntState.PREPARING: {HuntState.PREPPED, HuntState.LIVE, HuntState.VOIDED},
    HuntState.PREPPED: {HuntState.LIVE, HuntState.VOIDED},
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
    # P2 prepare/go-live split: live_at = when Clue 1 actually posted. The DM
    # gate uses it: [0, started_at) = old noise; [started_at, live_at) = the
    # prep window ('early', rejected + logged); [live_at, ...) = the game.
    live_at: datetime | None = None
    # Public "Hunt #N" — DB-derived at prepare time, stored on the row, reread
    # on resume. ONE source of truth (P3.2: posts said #1 forever while resume
    # printed the DB id).
    number: int = 1


def _theme_line(row: dict) -> str:
    """One compact 'do not repeat' line per past hunt for avoid_recent: the
    display name + archetype + the literal answer terms — so the generator
    steers away from the THEME, not just the exact name."""
    name = str(row.get("persona_display_name") or "").strip()
    payload = row.get("persona_identity")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    payload = payload or {}
    archetype = str(payload.get("archetype") or "").strip()
    terms = ", ".join(str(t) for t in (payload.get("solution_terms") or []) if str(t).strip())
    bits = [b for b in (name, archetype, terms) if b]
    return " / ".join(bits)


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
        hunt_number: int = 1,  # FALLBACK only — the real number is DB-derived in _prepare
        register: str = "medium",
        holding_floor_usd: float = 20.0,
        holding_floor_fmml: int = 0,
        holding_hours: int = 24,
        poll_interval_s: int = 75,  # DM-read rate-limit safe (~15 req/15min); winner = DM arrival order, so slower polling never changes who wins
        max_rounds: int = 100_000,
        avatar_writer=None,
        clue_due_fn=None,
        cleanup_window_s: int = CLEANUP_WINDOW_SECONDS,
        undress_on_retire: bool = False,
        control=None,
        hunt_timeout_hours: float | None = 72,
        heartbeat=None,
        persona_post_engine=None,
        prep_window_h: float | None = None,
        prep_posts_n: int = 3,
        claim_source=None,
        taunt_engine=None,
        claim_guess_cap: int = 5,
        wallet_timeout_s: int = 600,
        claim_sweep_every_n: int = 5,
        non_holder_prize_pct: int = 10,
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
        self._holding_floor_fmml = holding_floor_fmml
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
        # Liveness sensor (runtime.PollHeartbeat): the live loop beat()s every
        # cycle; a supervisor thread screams on Telegram if beats stop while a
        # hunt is live. Post-mortem P0: a silently hung/dead loop looked
        # exactly like a healthy idle one.
        self._heartbeat = heartbeat
        # P2 prepare/go-live split. prep_window_h=None keeps the legacy direct
        # go-live (simulation/live-test default); production passes 24.
        self._persona_post_engine = persona_post_engine
        self._prep_window_h = prep_window_h
        self._prep_posts_n = prep_posts_n
        # Claim-by-post channel (2026-07-25). claim_source set => submissions
        # come from PUBLIC replies to the Clue 1 post (mentions timeline); the
        # legacy DM loop stays intact as the fallback (claim_source=None) until
        # the post channel has survived a production hunt — then it goes.
        self._claim_source = claim_source
        self._taunt_engine = taunt_engine
        self._claim_guess_cap = claim_guess_cap
        self._wallet_timeout_s = wallet_timeout_s
        self._claim_sweep_every_n = claim_sweep_every_n
        # Holder reward split (2026-07-31): holding no longer eliminates — a
        # non-holder winner is paid this % of the pot (holders get 100%).
        # Clamped 1-100: 0 would pay a winner nothing while the announcement
        # declares them a winner; >100 would exceed the preflight-checked pot.
        self._non_holder_pct = min(100, max(1, int(non_holder_prize_pct)))

    # ------------------------------------------------------------------
    def _submission_loop(self, hunt: PreparedHunt, **kw) -> Winner:
        """Channel selector: claim-by-post when a claim_source is wired,
        legacy DMs otherwise. One line, so run_hunt and every resume path
        agree forever on which channel a hunt uses."""
        if self._claim_source is not None:
            return self._claim_loop(hunt, **kw)
        return self._clue_and_dm_loop(hunt, **kw)

    # ------------------------------------------------------------------
    def run_hunt(
        self, prize_fmml: int | None = None, *, prize_usd: float | None = None
    ) -> PreparedHunt:
        """Token-denominated prizes (Pedro, 2026-07-31): production launches
        with an exact $FIND amount (/launch 500M) — no USD conversion, no
        FMML_USD_PRICE required. The prize_usd keyword remains for the
        live-test harness and simulations (converted via the price feed)."""
        self._settings.assert_ready_for_hunt()
        if prize_fmml is None:
            usd = prize_usd if prize_usd is not None else self._settings.prize_usd_max
            prize_fmml = self._price_feed.usd_to_fmml(usd)
        hunt = self._prepare(prize_fmml)
        if self._prep_window_h:
            if not self._prep_window(hunt):
                return hunt  # aborted by the operator during prep
        self._go_live(hunt)
        winner = self._submission_loop(hunt)
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
    def _prepare(self, prize_fmml: int) -> PreparedHunt:
        # Eligibility floor FIRST — before any account is acquired or dressed.
        # Fail-CLOSED (review 2026-07-31): a USD floor without a price must
        # refuse the launch, never silently become "no floor" (the public
        # anti-sniper rule would be unenforced while still advertised).
        min_balance_fmml = self._holding_floor_fmml
        if not min_balance_fmml and self._holding_floor_usd > 0:
            try:
                min_balance_fmml = self._price_feed.usd_to_fmml(self._holding_floor_usd)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    f"holding floor is ${self._holding_floor_usd:g} (USD fallback) but "
                    f"the price is unavailable ({e}) — set HOLDING_FLOOR_FMML (token "
                    "floor, no price needed) or FMML_USD_PRICE, or set the floor to 0 "
                    "explicitly. Refusing to launch with an unenforceable floor."
                ) from e

        persona = self._persona_source.acquire_ready()

        # Public hunt number from the DB (max+1). On failure, fall back to the
        # constructor default and say so — a numbering hiccup must not block a
        # launch, but it must never be silent either.
        try:
            number = self._repo.next_hunt_number()
        except Exception as e:  # noqa: BLE001
            number = self._hunt_number
            self._notify(f"hunt numbering query failed ({e!r}) — falling back to #{number}")

        # Anti-repetition (post-mortem P1a): both halves existed — the
        # generator's avoid_recent parameter and the stored persona_identity —
        # but nothing connected them, so every hunt got an identical prompt and
        # the model converged on the densest archetype (the Penelope repeat).
        avoid: list[str] = []
        try:
            for row in self._repo.recent_persona_identities():
                line = _theme_line(row)
                if line:
                    avoid.append(line)
        except Exception as e:  # noqa: BLE001
            self._notify(
                f"could not load recent personas for avoid_recent ({e!r}) — "
                "generating without the anti-repeat list; watch for a repeated theme."
            )
        identity = self._persona_generator.generate(
            register=self._register, avoid_recent=avoid
        )
        claim_code = generate_claim_code()
        salt = generate_salt()
        integrity_hash = compute_integrity_hash(persona.x_user_id, claim_code, salt)

        prize_fmml = int(prize_fmml)
        # Informational only: a USD figure for the DB row, when a price is set.
        prize_usd = None
        fmml_to_usd = getattr(self._price_feed, "fmml_to_usd", None)
        if fmml_to_usd is not None:
            try:
                prize_usd = fmml_to_usd(prize_fmml)
            except Exception:  # noqa: BLE001 — cosmetic, never blocks a launch
                prize_usd = None
        # Eligibility floor: prefer the PRE-ANNOUNCED fixed token amount — the
        # 24h holding window looks BACK in time, so players must have known the
        # exact number before they bought. Trigger-time USD conversion is only
        # the fallback (and can unfairly raise the bar if price fell overnight).
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
            # persona_identity (jsonb) enables full crash-resume; hunt_number is
            # the public numbering. Both are post-baseline columns — if the live
            # DB predates them, fall back gracefully.
            hunt_id = self._repo.create_hunt(
                **base_fields, persona_identity=asdict(identity), hunt_number=number
            )
        except Exception:  # noqa: BLE001 — e.g. column missing on an older DB
            hunt_id = self._repo.create_hunt(**base_fields)
            self._notify(
                "hunts.persona_identity/hunt_number missing in the DB — resume "
                "will be degraded and numbering may fall back to the DB id. Run: "
                "alter table hunts add column if not exists persona_identity jsonb; "
                "alter table hunts add column if not exists hunt_number integer;"
            )
        # P2: generate the persona's prep posts NOW (they anchor the clues) and
        # schedule them at random times inside the window. Publishing happens in
        # _prep_window; without a window this block is skipped entirely.
        prep_posts: list[str] = []
        if self._prep_window_h and self._persona_post_engine is not None:
            prep_posts = self._persona_post_engine.generate(
                identity, n=self._prep_posts_n
            )
            window_s = self._prep_window_h * 3600
            for i, text in enumerate(prep_posts):
                # Spread across the middle 80% of the window, ordered.
                frac = (i + 1) / (len(prep_posts) + 1)
                sched = started_at + timedelta(seconds=window_s * (0.1 + 0.8 * frac))
                try:
                    self._repo.create_persona_post(
                        hunt_id=hunt_id, text=text, scheduled_at=sched
                    )
                except Exception as e:  # noqa: BLE001 — table may predate migration
                    self._notify(
                        f"persona_posts table missing? ({e!r}) — prep posts will "
                        "not be published. Run the 2026-07-24 migration."
                    )
                    prep_posts = []
                    break

        ctx = PersonaContext.from_generated(identity, persona.handle)
        # Anchor posts: the clue engine points players at REAL searchable
        # phrases — the vector that provably works (Hunt #2).
        if prep_posts and hasattr(ctx, "anchor_posts"):
            ctx.anchor_posts = list(prep_posts)

        hunt = PreparedHunt(
            id=hunt_id,
            persona=persona,
            identity=identity,
            ctx=ctx,
            claim_code=claim_code,
            salt=salt,
            integrity_hash=integrity_hash,
            prize_usd=prize_usd or 0.0,
            prize_fmml=prize_fmml,
            min_balance_fmml=min_balance_fmml,
            holding_hours=self._holding_hours,
            state=HuntState.PREPARING,
            started_at=started_at,
            number=number,
        )
        self._notify(f"hunt #{hunt.number}: persona {persona.handle} dressed, preparing")
        return hunt

    # Poll cadence inside the prep window (checks abort/delay flags + due posts).
    _PREP_TICK_S = 60

    def _prep_window(self, hunt: PreparedHunt) -> bool:
        """T-24h → T0: persona is dressed and posting; Clue 1 is NOT out.

        The DB is the source of truth for the whole window (post-mortem
        doctrine): golive_due_at (which /delay_golive moves) and abort_prep
        (which /abort_prep sets) are re-read every tick, so operator commands
        and restarts always win. Returns True to proceed to go-live, False if
        the operator aborted (persona undressed + retired)."""
        if hunt.state is HuntState.PREPPED:
            # Crash-resume: window already scheduled; the DB row has the truth.
            row0 = {}
            try:
                row0 = self._repo.get_hunt(hunt.id) or {}
            except Exception:  # noqa: BLE001
                pass
            due = _as_dt(row0.get("golive_due_at")) or self._clock.now()
        else:
            due = self._clock.now() + timedelta(hours=self._prep_window_h)
            self._transition(hunt, HuntState.PREPPED, golive_due_at=due)
            self._notify(
                f"hunt #{hunt.number} PREPPED — persona dressed; prep window until "
                f"{due:%Y-%m-%d %H:%M} UTC. /abort_prep aborts, /delay_golive <h> delays."
            )
        while True:
            row = {}
            try:
                row = self._repo.get_hunt(hunt.id) or {}
            except Exception as e:  # noqa: BLE001 — DB hiccup: keep last known
                self._notify(f"prep window: DB read failed (retrying): {e!r}")

            if row.get("abort_prep"):
                self._abort_prep(hunt)
                return False

            if self._control is not None and self._control.paused():
                self._clock.sleep(self._PREP_TICK_S)
                continue

            self._publish_due_prep_posts(hunt)

            due = _as_dt(row.get("golive_due_at")) or due
            if self._clock.now() >= due:
                return True
            self._clock.sleep(self._PREP_TICK_S)

    def _publish_due_prep_posts(self, hunt: PreparedHunt) -> None:
        """Publish any scheduled persona post whose time has come. Best-effort:
        a failed post is retried next tick; a failed bookkeeping write must not
        re-post (mark first? no — the post matters more than the row; we mark
        after posting and notify if the mark fails)."""
        try:
            rows = self._repo.persona_posts_for_hunt(hunt.id)
        except Exception:  # noqa: BLE001
            return
        now = self._clock.now()
        for r in rows:
            if r.get("posted_at"):
                continue
            sched = _as_dt(r.get("scheduled_at"))
            if sched is None or sched > now:
                continue
            try:
                tweet_id = self._dresser.publish_post(
                    access_token=hunt.persona.access_token,
                    access_secret=hunt.persona.access_secret,
                    text=r["text"],
                )
            except Exception as e:  # noqa: BLE001
                self._notify(f"prep post failed (will retry next tick): {e!r}")
                continue
            try:
                self._repo.set_persona_post(r["id"], posted_at=now, tweet_id=tweet_id)
            except Exception as e:  # noqa: BLE001
                self._notify(
                    f"prep post {tweet_id} published but NOT marked in the DB "
                    f"({e!r}) — it may be re-posted after a restart; check."
                )

    def _abort_prep(self, hunt: PreparedHunt) -> None:
        """Operator's /abort_prep: something broke during the window (persona
        suspended, post flagged). Undress, retire, void — Clue 1 never fires."""
        self._notify(f"hunt #{hunt.number} prep ABORTED by operator — voiding.")
        self._transition(hunt, HuntState.VOIDED)
        self._transition(hunt, HuntState.RETIRING)
        try:
            self._dresser.retire(
                access_token=hunt.persona.access_token,
                access_secret=hunt.persona.access_secret,
            )
        except Exception as e:  # noqa: BLE001
            self._notify(f"undress of aborted persona failed: {e!r} — reset it manually.")
        self._persona_source.mark_retired(hunt.persona.id)
        self._transition(hunt, HuntState.DONE)

    def _go_live(self, hunt: PreparedHunt) -> None:
        draft = self._clue_engine.next_clue(hunt.ctx, 1, [])
        post = clue_one(
            hunt_n=hunt.number,
            clue_text=draft.text,
            prize=f"{hunt.prize_fmml:,}",
            integrity_hash=hunt.integrity_hash,
            non_holder_pct=self._non_holder_pct,
        )
        tweet_id = self._publisher.post(post, long_post=True)
        hunt.reshare_post_id = tweet_id
        hunt.clues.append(draft.text)
        self._repo.record_clue(
            hunt_id=hunt.id, clue_index=1, clue_text=draft.text, tweet_id=tweet_id
        )
        # reshare_post_id persisted so a restarted agent keeps the SAME gate;
        # live_at is the prep-window boundary for the DM gate (P2).
        hunt.live_at = self._clock.now()
        self._transition(
            hunt, HuntState.LIVE, reshare_post_id=tweet_id, live_at=hunt.live_at
        )
        self._notify(f"hunt #{hunt.number} LIVE — clue 1 posted ({tweet_id})")

    # A submission whose processing keeps erroring (X lookup down, DB hiccup) is
    # retried this many times before being skipped, so one poisoned DM can never
    # stall the queue forever.
    _MAX_SUBMISSION_RETRIES = 3
    # Round-robin cap on per-conversation reads (rate budget: ONE read per
    # cycle in the conversation endpoint's own 15/15min bucket).
    _MAX_ACTIVE_CONVS = 15

    def _touch_conv(self, active: list, markers: dict, sub) -> None:
        """Record activity: sender moves to the front of the rotation (recent
        talkers are re-read soonest) and its per-conversation marker advances."""
        sender = sub.sender_x_id
        if sender in active:
            active.remove(sender)
        active.insert(0, sender)
        del active[self._MAX_ACTIVE_CONVS:]
        prev = markers.get(sender)
        if str(sub.dm_id).isdigit() and (prev is None or int(sub.dm_id) > int(prev)):
            markers[sender] = sub.dm_id

    def _clue_and_dm_loop(
        self, hunt: PreparedHunt, *, since: str | None = None, clue_index: int | None = None
    ) -> Winner:
        """Heartbeat bracket around the live loop: mark_live(True) while inside,
        ALWAYS mark_live(False) on the way out (winner, void, or crash) so the
        watchdog never keeps screaming over a loop that already exited."""
        if self._heartbeat is None:
            return self._dm_loop_body(hunt, since=since, clue_index=clue_index)
        self._heartbeat.mark_live(True)
        try:
            return self._dm_loop_body(hunt, since=since, clue_index=clue_index)
        finally:
            self._heartbeat.mark_live(False)

    def _dm_loop_body(
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

        # ---- DM ingestion state (Hunt #2 P0 fix) ----
        # processed: dedupe between the discovery stream and per-conversation
        # reads (prepopulated from the log so a resume never double-processes).
        # conv_marker/active_convs: per-conversation cursors + rotation.
        # assembler: joins code+wallet across a sender's messages; replaying the
        # log rebuilds partial state after a restart AND seeds the validation
        # fingerprints so old complete pairs aren't re-validated.
        assembler = SubmissionAssembler()
        processed: set[str] = set()
        conv_marker: dict[str, str] = {}
        active_convs: list[str] = []
        try:
            from ..dm.validator import ParsedDM as _P

            for row in sorted(
                self._repo.submissions_for_hunt(hunt.id),
                key=lambda r: str(r.get("x_created_at") or ""),
            ):
                dmid = str(row.get("dm_id") or "")
                sender = str(row.get("sender_x_id") or "")
                if dmid:
                    processed.add(dmid)
                if not sender:
                    continue
                if sender not in active_convs:
                    active_convs.append(sender)
                if dmid.isdigit():
                    prev = conv_marker.get(sender)
                    if prev is None or int(dmid) > int(prev):
                        conv_marker[sender] = dmid
                if row.get("outcome") == "early":
                    continue  # prep-window messages never feed the assembler
                code = str(row.get("submitted_claim_code") or "").upper() or None
                replayed = assembler.feed(
                    _P(dm_id=dmid, sender_x_id=sender,
                       wallet=row.get("wallet") or None, claim_code=code,
                       claim_candidates=(code,) if code else ()),
                    _as_dt(row.get("x_created_at")) or self._clock.now(),
                )
                if replayed is not None:
                    # This pair was already validated before the restart (its
                    # outcome is in the log) — don't re-validate it now.
                    assembler.mark_validated(sender)
        except Exception as e:  # noqa: BLE001 — a bad log row must not block the hunt
            self._notify(f"could not rebuild DM ingestion state ({e!r}) — starting fresh.")
        rr_index = 0
        # Settlement sweep (fairness): the winner is decided by the created_at
        # of the message that COMPLETED the pair — but round-robin reads can
        # PROCESS a later clean thread before an earlier follow-up is read
        # (exactly the Hunt #2 injustice, recreated by read latency). So the
        # first valid win only opens a candidacy: every active conversation is
        # re-read once more, an earlier completion replaces the candidate, and
        # only then does the hunt resolve. Worst case adds ~cap×cycle (~19 min)
        # before payment; ordering is never decided by our polling schedule.
        win_best: tuple | None = None          # (completed_at, Winner)
        settle_pending: set[str] = set()
        settle_deadline = None

        for _ in range(self._max_rounds):
            # Liveness: one beat per cycle, whatever the cycle does (idle,
            # pause, failure backoff). No beats => hung or dead => watchdog.
            if self._heartbeat is not None:
                self._heartbeat.beat()

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
                win_best is None
                and self._hunt_timeout_h is not None
                and hunt.started_at is not None
                and self._clock.now() >= hunt.started_at + timedelta(hours=self._hunt_timeout_h)
            ):
                self._void_unclaimed(hunt)
                return None

            # ---- Phase 1: read DMs (isolated — a failed poll is retried) ----
            # Two streams (Hunt #2 P0): the account-level endpoint SUPPRESSES
            # inbound events of conversations we replied in (proven in prod +
            # devcommunity /t/254508), so it is DISCOVERY-only; follow-ups come
            # from per-conversation reads, ONE per cycle by rate budget (each
            # endpoint has its own 15/15min per-user bucket; at 75s/cycle both
            # streams run at 12/15min — never read two conversations per cycle).
            raw_batch: list = []
            discovery_ids: set[str] = set()
            try:
                found = list(self._dm_source.poll(since))
                discovery_ids = {s.dm_id for s in found}
                raw_batch += found
                poll_failures = 0
            except Exception as e:  # noqa: BLE001
                poll_failures += 1
                self._notify(
                    f"DM poll failed ({poll_failures}x, retrying): {e!r}"
                )
                # extra backoff on top of the normal sleep, capped at 5 min
                self._clock.sleep(min(300, self._poll_interval_s * min(poll_failures, 4)))

            poll_conv = getattr(self._dm_source, "poll_conversation", None)
            if poll_conv is not None and active_convs:
                # During settlement, prioritize conversations not yet re-read.
                pool = [c for c in active_convs if c in settle_pending] or active_convs
                conv = pool[rr_index % len(pool)]
                rr_index += 1
                try:
                    raw_batch += list(poll_conv(conv, conv_marker.get(conv)))
                    settle_pending.discard(conv)
                except Exception as e:  # noqa: BLE001
                    self._notify(f"conversation poll {conv} failed (next cycle): {e!r}")
            elif poll_conv is None:
                settle_pending.clear()  # single-stream source: nothing to sweep

            merged: dict[str, object] = {}
            for sub in raw_batch:
                if sub.dm_id not in processed and sub.dm_id not in merged:
                    merged[sub.dm_id] = sub
            batch = sorted(merged.values(), key=lambda s: s.created_at)

            # ---- Phase 2: process submissions (isolated per submission) ----
            tag = f"[hunt#{hunt.number}/db{hunt.id}]"
            if batch:
                print(f"{tag} processing {len(batch)} dm(s), marker={since or 'start'}")
            for sub in batch:
                live_boundary = hunt.live_at or hunt.started_at

                # Window 1 — before the hunt row existed: old conversations,
                # not submissions. Skipped, but never silently (post-mortem P0).
                if hunt.started_at is not None and sub.created_at < hunt.started_at:
                    print(
                        f"{tag} dm {sub.dm_id} skipped: pre-hunt "
                        f"({sub.created_at} < {hunt.started_at}); marker advanced"
                    )
                    if sub.dm_id in discovery_ids:
                        since = sub.dm_id
                    processed.add(sub.dm_id)
                    continue

                # Window 2 — the prep window (persona dressed, Clue 1 NOT out):
                # rejected + LOGGED + replied, never able to win, never fed to
                # the assembler (windows are watertight). Hunt #2 P2 gate.
                if live_boundary is not None and sub.created_at < live_boundary:
                    try:
                        self._repo.log_submission(
                            hunt_id=hunt.id, dm_id=sub.dm_id,
                            sender_x_id=sub.sender_x_id, wallet=None,
                            outcome="early", x_created_at=sub.created_at,
                        )
                    except Exception as e:  # noqa: BLE001
                        self._notify(f"early dm {sub.dm_id} could not be logged: {e!r}")
                    print(f"{tag} dm {sub.dm_id} rejected: early (prep window)")
                    if sub.dm_id in discovery_ids:
                        since = sub.dm_id
                    processed.add(sub.dm_id)
                    self._touch_conv(active_convs, conv_marker, sub)
                    try:
                        self._publisher.reply_dm(sub.sender_x_id, DM_REPLY_EARLY)
                    except Exception as e:  # noqa: BLE001
                        self._notify(f"early reply to @{sub.sender_handle} failed (non-fatal): {e!r}")
                    continue

                # Window 3 — the game.
                try:
                    parsed = parse_dm(
                        sub.dm_id, sub.sender_x_id, sub.body,
                        expected_code_len=len(hunt.claim_code),
                    )
                    # Hunt #2 rule: code and wallet may arrive in separate
                    # messages; arrival order = the message that COMPLETED the
                    # pair. Each raw message is still logged for the audit.
                    assembled = assembler.feed(parsed, sub.created_at)
                    if assembled is not None:
                        res = self._validator.validate(assembled.parsed, hunt)
                        assembler.mark_validated(sub.sender_x_id)
                        outcome = res.outcome
                        wallet_for_log = assembled.parsed.wallet
                    else:
                        res = None
                        missing = assembler.missing(sub.sender_x_id)
                        outcome = "malformed" if missing == "both" else "partial"
                        wallet_for_log = parsed.wallet
                    row_id = self._repo.log_submission(
                        hunt_id=hunt.id, dm_id=sub.dm_id, sender_x_id=sub.sender_x_id,
                        wallet=wallet_for_log, submitted_claim_code=parsed.claim_code,
                        outcome=outcome, x_created_at=sub.created_at,
                    )
                except Exception as e:  # noqa: BLE001
                    tries = sub_retries.get(sub.dm_id, 0) + 1
                    sub_retries[sub.dm_id] = tries
                    if tries < self._MAX_SUBMISSION_RETRIES:
                        # Do NOT advance markers: this DM is re-read and
                        # retried on the next poll, preserving arrival order.
                        self._notify(
                            f"submission {sub.dm_id} failed (attempt {tries}, "
                            f"will retry): {e!r}"
                        )
                        break
                    if sub.dm_id in discovery_ids:
                        since = sub.dm_id  # give up on this one; don't stall the queue
                    processed.add(sub.dm_id)
                    self._notify(
                        f"submission {sub.dm_id} SKIPPED after "
                        f"{tries} failed attempts: {e!r} — review it manually."
                    )
                    continue
                if sub.dm_id in discovery_ids:
                    since = sub.dm_id
                processed.add(sub.dm_id)
                self._touch_conv(active_convs, conv_marker, sub)
                if res is not None and res.won:
                    cand = Winner(
                        submission=sub, wallet=assembled.parsed.wallet,
                        submission_row_id=row_id if isinstance(row_id, int) else None,
                        holder=getattr(res, "holder", True),
                    )
                    if win_best is None:
                        win_best = (sub.created_at, cand)
                        settle_pending = {
                            c for c in active_convs if c != sub.sender_x_id
                        }
                        settle_deadline = self._clock.now() + timedelta(
                            seconds=max(600, (self._MAX_ACTIVE_CONVS + 2) * self._poll_interval_s)
                        )
                        self._notify(
                            f"win candidate: @{sub.sender_handle} (completed "
                            f"{sub.created_at:%H:%M:%S}) — settlement sweep over "
                            f"{len(settle_pending)} conversation(s) before paying."
                        )
                    elif sub.created_at < win_best[0]:
                        self._notify(
                            f"settlement: EARLIER completion by @{sub.sender_handle} "
                            f"({sub.created_at:%H:%M:%S}) replaces the candidate."
                        )
                        win_best = (sub.created_at, cand)
                    else:
                        # Valid but later than the candidate: they lost the race.
                        try:
                            self._publisher.reply_dm(sub.sender_x_id, DM_REPLY_LATE)
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                if res is not None:
                    reply = _REPLY_BY_OUTCOME.get(res.outcome)
                else:
                    reply = {
                        "wallet": DM_REPLY_NEED_WALLET,
                        "code": DM_REPLY_NEED_CODE,
                        "both": DM_REPLY_NO_ADDRESS,
                    }.get(assembler.missing(sub.sender_x_id))
                if reply:
                    # Courtesy replies are best-effort: a failed reply (e.g. DM
                    # send restrictions) must NEVER abort the hunt. The winner is
                    # paid on-chain + announced publicly; no DM is required.
                    try:
                        self._publisher.reply_dm(sub.sender_x_id, reply)
                    except Exception as e:  # noqa: BLE001
                        self._notify(f"reply to @{sub.sender_handle} failed (non-fatal): {e!r}")

            # ---- Phase 2b: settlement finalization ----
            if win_best is not None:
                deadline_hit = settle_deadline is not None and self._clock.now() >= settle_deadline
                if not settle_pending or deadline_hit:
                    if deadline_hit and settle_pending:
                        self._notify(
                            f"settlement deadline hit with {len(settle_pending)} "
                            "conversation(s) unread — finalizing with the best known."
                        )
                    winner = win_best[1]
                    self._transition(hunt, HuntState.RESOLVING)
                    self._notify(f"winner: @{winner.submission.sender_handle}")
                    return winner
                self._clock.sleep(self._poll_interval_s)
                continue  # sweeping: no clues while the hunt is decided

            # ---- Phase 3: post the next clue (isolated — a failed clue is a
            # skipped round, never a dead hunt) ----
            clue_index, next_due = self._maybe_post_clue(hunt, clue_index, next_due)

            self._clock.sleep(self._poll_interval_s)

        raise RuntimeError("hunt loop exceeded max rounds without a winner")

    def _maybe_post_clue(
        self, hunt: PreparedHunt, clue_index: int, next_due, claim_hint: str | None = None
    ) -> tuple[int, object]:
        """Post the next clue if it's due. Shared by both submission loops.
        A failed clue is a skipped round, never a dead hunt."""
        if self._clock.now() < next_due:
            return clue_index, next_due
        clue_index += 1
        try:
            draft = self._clue_engine.next_clue(hunt.ctx, clue_index, hunt.clues)
            tweet_id = self._publisher.post(
                clue_followup(clue_index, draft.text, draft.taunt or "", claim_hint)
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
        return clue_index, self._clue_due_fn(self._clock.now())

    # ==================================================================
    # Claim-by-post (2026-07-25) — the public submission channel.
    # Ruleset (Pedro): claims ONLY as replies to the Clue 1 post; order =
    # tweet created_at (snowflake id tiebreak); reshare is eliminatory and
    # checked at processing time; the wallet is asked publicly and accepted
    # ONLY from the same author_id; 10-minute timeout from OUR ask reply;
    # on timeout the hunt reopens; wrong codes get capped oracle taunts;
    # the reply engine never has clue content (architecture, not promise).
    # ==================================================================

    # Row outcomes that count as a code attempt for the per-account guess cap.
    _GUESS_OUTCOMES = frozenset(
        {"bad_code", "no_reshare", "pending", "won", "timed_out", "spam_capped",
         "late", "bot_disqualified"}
    )
    # A candidate whose public ask keeps failing (e.g. winning post deleted)
    # is dropped after this many attempts — a wedged ask must never freeze the
    # hunt forever (adversarial review #3).
    _MAX_ASK_ATTEMPTS = 5
    # Global taunt budget per hunt (cost control): pool replies are $0.015
    # each; past this the oracle goes silent, claims still process normally.
    _MAX_TAUNTS_PER_HUNT = 50

    def _claim_loop(
        self, hunt: PreparedHunt, *, since: str | None = None, clue_index: int | None = None
    ) -> Winner | None:
        """Heartbeat bracket — same contract as the DM loop."""
        if self._heartbeat is None:
            return self._claim_loop_body(hunt, since=since, clue_index=clue_index)
        self._heartbeat.mark_live(True)
        try:
            return self._claim_loop_body(hunt, since=since, clue_index=clue_index)
        finally:
            self._heartbeat.mark_live(False)

    def _rebuild_claim_state(self, hunt: PreparedHunt):
        """Restart safety: everything the loop promised publicly (caps, 'one
        reply per profile') is rebuilt from the submissions log; the WAIT_WALLET
        sub-state is rebuilt from the hunt row (DB doctrine)."""
        from ..claims.parser import ClaimPost

        processed: set[str] = set()
        guesses: dict[str, int] = {}
        counted: set[str] = set()      # tweet ids already counted as guesses
        taunted: set[str] = set()
        sys_sent: dict[str, set[str]] = {}
        queue: list[tuple] = []        # rebuilt open claims ('pending' rows)
        for row in self._repo.submissions_for_hunt(hunt.id):
            tid = str(row.get("dm_id") or "")
            author = str(row.get("sender_x_id") or "")
            outcome = row.get("outcome")
            if tid:
                processed.add(tid)
            if not author:
                continue
            if outcome in self._GUESS_OUTCOMES:
                guesses[author] = guesses.get(author, 0) + 1
                counted.add(tid)
            if outcome in ("bad_code", "taunted"):
                taunted.add(author)
            if outcome == "pending":
                # An open claim from before the restart: the candidate/queue
                # must survive (adversarial review #2 — otherwise the marker
                # skips it forever and a LATER claimant would win).
                created = _as_dt(row.get("x_created_at")) or self._clock.now()
                pseudo = ClaimPost(
                    tweet_id=tid, author_id=author,
                    author_handle=str(row.get("sender_handle") or ""),
                    text="", created_at=created,
                    replied_to_id=hunt.reshare_post_id,
                )
                queue.append(
                    (created, int(tid) if tid.isdigit() else 0, pseudo, row.get("id"))
                )
            kind = {
                "no_reshare": "missing_repost", "wrong_door": "wrong_door",
                "early": "early", "late": "late",
            }.get(outcome)
            if kind:
                sys_sent.setdefault(kind, set()).add(author)
        queue.sort(key=lambda e: (e[0], e[1]))

        pending = None
        try:
            row = self._repo.get_hunt(hunt.id) or {}
        except Exception:  # noqa: BLE001
            row = {}
        if row.get("pending_winner_x_id") and row.get("pending_ask_tweet_id"):
            claim_tid = str(row.get("pending_claim_tweet_id") or "")
            claim_row = next(
                (s for s in self._repo.submissions_for_hunt(hunt.id)
                 if str(s.get("dm_id") or "") == claim_tid),
                {},
            )
            pending = {
                "author_id": str(row["pending_winner_x_id"]),
                "author_handle": str(row.get("pending_winner_handle") or ""),
                "claim_tweet_id": claim_tid,
                "ask_tweet_id": str(row["pending_ask_tweet_id"]),
                "due_at": _as_dt(row.get("wallet_due_at")) or self._clock.now(),
                "row_id": claim_row.get("id"),
                "claim_created_at": _as_dt(claim_row.get("x_created_at"))
                or self._clock.now(),
                "corrected": False,
            }
            # The active pending claim is not "queued behind itself".
            queue = [e for e in queue if e[2].tweet_id != claim_tid]
        return processed, guesses, counted, taunted, sys_sent, pending, queue

    def _sys_reply(
        self, kind: str, post, text: str, sys_sent: dict[str, set[str]]
    ) -> str | None:
        """One system reply of each TYPE per profile, best-effort (a failed
        reply never stalls the hunt). Returns the reply tweet id, if sent."""
        sent = sys_sent.setdefault(kind, set())
        if post.author_id in sent:
            return None
        sent.add(post.author_id)
        try:
            return self._publisher.reply_post(text, in_reply_to=post.tweet_id)
        except Exception as e:  # noqa: BLE001
            self._notify(
                f"public reply ({kind}) to @{post.author_handle} failed (non-fatal): {e!r}"
            )
            return None

    def _banned_reply_terms(self, hunt: PreparedHunt) -> tuple[str, ...]:
        """The hard-validation list for outgoing taunts: solution terms, the
        persona's name tokens + handle, and the claim code. The taunt engine
        receives ONLY this list — never the clues or the identity."""
        terms: list[str] = [hunt.claim_code]
        identity = hunt.identity
        if identity is not None:
            terms += [str(t) for t in getattr(identity, "solution_terms", [])]
            terms += str(getattr(identity, "display_name", "") or "").split()
        terms.append(hunt.persona.handle.lstrip("@"))
        return tuple(t for t in terms if t and len(t) >= 3)

    def _set_pending(self, hunt: PreparedHunt, pending: dict | None) -> None:
        """Persist / clear the WAIT_WALLET sub-state on the hunt row. Critical
        state never lives only in process memory (post-mortem doctrine)."""
        fields = (
            dict(
                pending_winner_x_id=pending["author_id"],
                pending_winner_handle=pending["author_handle"],
                pending_claim_tweet_id=pending["claim_tweet_id"],
                pending_ask_tweet_id=pending["ask_tweet_id"],
                wallet_due_at=pending["due_at"],
            )
            if pending
            else dict(
                pending_winner_x_id=None, pending_winner_handle=None,
                pending_claim_tweet_id=None, pending_ask_tweet_id=None,
                wallet_due_at=None,
            )
        )
        try:
            self._repo.update_hunt(hunt.id, **fields)
        except Exception as e:  # noqa: BLE001
            self._notify(
                f"🚨 could not persist WAIT_WALLET state ({e!r}) — a restart "
                "during this claim window would forget the pending winner."
            )

    def _ask_wallet(
        self, hunt: PreparedHunt, cand: tuple, sys_sent: dict[str, set[str]]
    ) -> dict | None:
        """Public congrats + wallet ask, replying to the winning code post.
        The 10-minute clock starts at OUR reply (the winner only learns they
        won when we answer). Returns the pending dict, or None if the reply
        could not be posted (retried next cycle)."""
        created_at, _tid, post, row_id = cand
        minutes = max(1, int(self._wallet_timeout_s // 60))
        try:
            ask_id = self._publisher.reply_post(
                post_reply_win(minutes), in_reply_to=post.tweet_id
            )
        except Exception as e:  # noqa: BLE001
            self._notify(f"wallet ask reply failed (retrying next cycle): {e!r}")
            return None
        pending = {
            "author_id": post.author_id,
            "author_handle": post.author_handle,
            "claim_tweet_id": post.tweet_id,
            "ask_tweet_id": ask_id,
            "due_at": self._clock.now() + timedelta(seconds=self._wallet_timeout_s),
            "row_id": row_id,
            "claim_created_at": created_at,
            "corrected": False,
        }
        self._set_pending(hunt, pending)
        self._notify(
            f"claim candidate @{post.author_handle} (post {post.tweet_id}, "
            f"{created_at:%H:%M:%S}) — wallet asked publicly, {minutes} min window."
        )
        return pending

    def _claim_loop_body(  # noqa: C901 — the live loop is long by nature, like its DM twin
        self, hunt: PreparedHunt, *, since: str | None = None, clue_index: int | None = None
    ) -> Winner | None:
        """The claim-by-post live loop. Same DESIGN RULE as the DM loop: once
        LIVE, nothing transient may kill it — every phase is isolated, failures
        notify + retry. Only the winner path (or void deadline) exits."""
        clue_index = clue_index if clue_index is not None else max(1, len(hunt.clues))
        next_due = self._clue_due_fn(self._clock.now())
        poll_failures = 0
        pause_notified = False
        cycle_n = 0

        (processed, guesses, counted, taunted, sys_sent, pending, rebuilt_queue) = (
            self._rebuild_claim_state(hunt)
        )
        if pending is not None:
            self._notify(
                f"resumed with a PENDING winner @{pending['author_handle']} "
                f"(wallet due {pending['due_at']:%H:%M:%S}) — watching the thread."
            )
        banned = self._banned_reply_terms(hunt)
        judged: set[str] = set()               # authors whose chatter was LLM-judged
        taunt_budget = {"used": len(taunted)}  # global per-hunt cost cap
        ask_attempts: dict[str, int] = {}      # per-claim failed public asks
        # Settlement: the first valid claim opens a short candidacy window (two
        # extra cycles) so an earlier post still in flight through the mentions
        # pipeline can displace it. created_at is authoritative and public.
        win_cand: tuple | None = None          # (created_at, tweet_int, post, row_id)
        settle_until = None
        wait_queue: list[tuple] = []           # valid claims queued behind pending
        if rebuilt_queue:
            # Open claims recovered from the log after a restart: earliest one
            # (re)opens the candidacy, the rest queue behind it.
            if pending is None:
                win_cand = rebuilt_queue.pop(0)
                settle_until = self._clock.now() + timedelta(
                    seconds=2 * self._poll_interval_s
                )
                self._notify(
                    f"recovered open claim by @{win_cand[2].author_handle} "
                    f"({win_cand[0]:%H:%M:%S}) from the log — resuming settlement."
                )
            wait_queue = rebuilt_queue

        for _ in range(self._max_rounds):
            if self._heartbeat is not None:
                self._heartbeat.beat()
            cycle_n += 1

            # ---- Phase 0: kill switch ----
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
                win_cand is None and pending is None and not wait_queue
                and self._hunt_timeout_h is not None
                and hunt.started_at is not None
                and self._clock.now() >= hunt.started_at + timedelta(hours=self._hunt_timeout_h)
            ):
                self._void_unclaimed(hunt)
                return None

            # ---- Phase 1: read the mentions stream (+ periodic thread sweep) ----
            raw_batch: list = []
            mention_ids: set[str] = set()
            try:
                found = list(self._claim_source.poll(since))
                mention_ids = {p.tweet_id for p in found}
                raw_batch += found
                poll_failures = 0
            except Exception as e:  # noqa: BLE001
                poll_failures += 1
                self._notify(f"claim poll failed ({poll_failures}x, retrying): {e!r}")
                backoff = min(300, self._poll_interval_s * min(poll_failures, 4))
                if pending is not None:
                    # Blind cycles must not eat the winner's 10 minutes — their
                    # reply may already be up, unread. Extend and persist.
                    pending["due_at"] += timedelta(seconds=backoff)
                    self._set_pending(hunt, pending)
                self._clock.sleep(backoff)

            sweep_due = (
                self._claim_sweep_every_n
                and cycle_n % self._claim_sweep_every_n == 0
            )
            # Settlement re-read: while a candidacy is open, sweep EVERY cycle.
            # An earlier post delivered late has a SMALLER snowflake id than the
            # since marker — the mentions poll can never see it again; only a
            # full markerless thread read can (the claim-channel analog of the
            # DM settlement's "re-read all active conversations"). No since_id
            # on sweeps, ever: the 24h per-resource dedup makes re-reads
            # near-free, and `processed` dedupes the work.
            if hunt.reshare_post_id and (sweep_due or win_cand is not None):
                try:
                    raw_batch += list(
                        self._claim_source.sweep(hunt.reshare_post_id, None)
                    )
                except Exception as e:  # noqa: BLE001
                    self._notify(f"thread sweep failed (next round): {e!r}")

            merged: dict[str, object] = {}
            for p in raw_batch:
                if p.tweet_id not in processed and p.tweet_id not in merged:
                    merged[p.tweet_id] = p
            batch = sorted(merged.values(), key=lambda p: p.sort_key())

            # ---- Phase 2: process posts, in public arrival order ----
            tag = f"[hunt#{hunt.number}/db{hunt.id}]"
            if batch:
                print(f"{tag} processing {len(batch)} post(s), marker={since or 'start'}")
            code_len = len(hunt.claim_code)

            def _done(post) -> None:
                """Mark a post fully processed: dedupe + advance the mentions
                marker (only for posts from the mentions stream — swept thread
                strays must never skip unread mentions)."""
                nonlocal since
                if (
                    post.tweet_id in mention_ids
                    and post.tweet_id.isdigit()
                    and (since is None or int(post.tweet_id) > int(since))
                ):
                    since = post.tweet_id
                processed.add(post.tweet_id)

            for post in batch:
                live_boundary = hunt.live_at or hunt.started_at

                # Window 1 — pre-hunt noise: skipped, never silently.
                if hunt.started_at is not None and post.created_at < hunt.started_at:
                    print(f"{tag} post {post.tweet_id} skipped: pre-hunt")
                    _done(post)
                    continue

                # Window 2 — prep window: a code-like post is logged 'early' +
                # answered once; chatter is just skipped. Never able to win.
                if live_boundary is not None and post.created_at < live_boundary:
                    if code_like(post.text, code_len):
                        try:
                            self._repo.log_submission(
                                hunt_id=hunt.id, dm_id=post.tweet_id,
                                sender_x_id=post.author_id, wallet=None,
                                sender_handle=post.author_handle,
                                outcome="early", x_created_at=post.created_at,
                            )
                        except Exception as e:  # noqa: BLE001
                            self._notify(f"early post {post.tweet_id} not logged: {e!r}")
                        self._sys_reply("early", post, POST_REPLY_EARLY, sys_sent)
                        print(f"{tag} post {post.tweet_id} rejected: early (prep window)")
                    _done(post)
                    continue

                # ---- WAIT_WALLET: replies to the ask tweet ----
                if pending is not None and post.replied_to_id == pending["ask_tweet_id"]:
                    if post.author_id != pending["author_id"]:
                        _done(post)  # anyone else's wallet is NOT the winner's — ignore
                        continue
                    if post.created_at > pending["due_at"]:
                        _done(post)  # after the window: Phase 2b will lapse it
                        continue
                    result = self._process_wallet_reply(hunt, pending, post, sys_sent)
                    if result == "retry":
                        # Validation infrastructure down (RPC, X): the reply is
                        # NOT consumed — it is re-read and retried next cycle,
                        # and the outage must not eat the winner's window
                        # (adversarial review #1).
                        pending["due_at"] = max(
                            pending["due_at"],
                            self._clock.now()
                            + timedelta(seconds=2 * self._poll_interval_s),
                        )
                        self._set_pending(hunt, pending)
                        break
                    _done(post)
                    if isinstance(result, Winner):
                        self._mark_queue_late(wait_queue)
                        return result
                    if result == "lapsed":
                        pending = self._promote_from_queue(
                            hunt, wait_queue, sys_sent, ask_attempts
                        )
                    continue

                is_claim_location = (
                    hunt.reshare_post_id is not None
                    and post.replied_to_id == hunt.reshare_post_id
                )
                looks_like_code = code_like(post.text, code_len)

                # Wrong door — a code-like post anywhere but the Clue 1 thread.
                if looks_like_code and not is_claim_location:
                    try:
                        self._repo.log_submission(
                            hunt_id=hunt.id, dm_id=post.tweet_id,
                            sender_x_id=post.author_id, wallet=None,
                            sender_handle=post.author_handle,
                            outcome="wrong_door", x_created_at=post.created_at,
                        )
                    except Exception as e:  # noqa: BLE001
                        self._notify(f"wrong_door post {post.tweet_id} not logged: {e!r}")
                    self._sys_reply("wrong_door", post, POST_REPLY_WRONG_DOOR, sys_sent)
                    _done(post)
                    continue

                if not is_claim_location:
                    # A random mention outside the Clue 1 thread: silence.
                    # (Taunts live in the claim thread only — Pedro's rule is
                    # about REPLIES; also keeps the LLM judge off the mentions
                    # firehose.)
                    _done(post)
                    continue

                # ---- Replies to Clue 1 (the claim window) ----
                if not looks_like_code:
                    self._maybe_taunt_chatter(
                        hunt, post, taunted, judged, taunt_budget, banned
                    )
                    _done(post)
                    continue

                # Guess cap (anti-brute-force): first N code-like posts count;
                # past the cap the account is ignored — logged, no reply.
                # Counted once per TWEET (a transient retry of the same post
                # must not burn extra guesses — adversarial review #4).
                if post.tweet_id not in counted:
                    counted.add(post.tweet_id)
                    guesses[post.author_id] = guesses.get(post.author_id, 0) + 1
                if guesses[post.author_id] > self._claim_guess_cap:
                    try:
                        self._repo.log_submission(
                            hunt_id=hunt.id, dm_id=post.tweet_id,
                            sender_x_id=post.author_id, wallet=None,
                            sender_handle=post.author_handle,
                            outcome="spam_capped", x_created_at=post.created_at,
                        )
                    except Exception as e:  # noqa: BLE001
                        self._notify(f"spam_capped post {post.tweet_id} not logged: {e!r}")
                    _done(post)
                    continue

                candidates = extract_candidates(post.text, code_len)
                if hunt.claim_code not in candidates:
                    # Wrong code — the oracle jeers (once per profile).
                    try:
                        self._repo.log_submission(
                            hunt_id=hunt.id, dm_id=post.tweet_id,
                            sender_x_id=post.author_id, wallet=None,
                            sender_handle=post.author_handle,
                            submitted_claim_code=(candidates[0] if candidates else None),
                            outcome="bad_code", x_created_at=post.created_at,
                        )
                    except Exception as e:  # noqa: BLE001
                        self._notify(f"bad_code post {post.tweet_id} not logged: {e!r}")
                    if (
                        post.author_id not in taunted
                        and self._taunt_engine is not None
                        and taunt_budget["used"] < self._MAX_TAUNTS_PER_HUNT
                    ):
                        taunted.add(post.author_id)
                        taunt_budget["used"] += 1
                        try:
                            jeer = self._taunt_engine.taunt(post.text, banned)
                            self._publisher.reply_post(jeer, in_reply_to=post.tweet_id)
                        except Exception as e:  # noqa: BLE001
                            self._notify(f"taunt reply failed (non-fatal): {e!r}")
                    _done(post)
                    continue

                # Correct code. Reshare is ELIMINATORY, checked now (the API
                # exposes no repost timestamp, so "exists at processing time"
                # is the enforceable rule — accepted slack of one poll cycle).
                outcome_row_id = None
                try:
                    reshared = self._claim_source.has_reshared(
                        post.author_id, hunt.reshare_post_id
                    )
                except Exception as e:  # noqa: BLE001
                    # Can't verify -> do NOT reject a possibly-valid claim and
                    # do NOT accept an unverified one; retry next cycle.
                    self._notify(f"reshare check failed for {post.tweet_id} (retrying): {e!r}")
                    break
                if not reshared:
                    try:
                        self._repo.log_submission(
                            hunt_id=hunt.id, dm_id=post.tweet_id,
                            sender_x_id=post.author_id, wallet=None,
                            sender_handle=post.author_handle,
                            submitted_claim_code=hunt.claim_code,
                            outcome="no_reshare", x_created_at=post.created_at,
                        )
                    except Exception as e:  # noqa: BLE001
                        self._notify(f"no_reshare post {post.tweet_id} not logged: {e!r}")
                    self._sys_reply(
                        "missing_repost", post, POST_REPLY_MISSING_REPOST, sys_sent
                    )
                    _done(post)
                    continue

                # Bot screen (bright-line, public self-identification only).
                try:
                    profile = self._claim_source.lookup_profile(post.author_id) or {}
                except Exception:  # noqa: BLE001
                    profile = {}
                bot_ok, reason = screen_bot(
                    display_name=profile.get("name", ""),
                    handle=profile.get("handle", post.author_handle),
                    bio=profile.get("bio", ""),
                    automated_label=bool(profile.get("automated", False)),
                    own_handles=("FindingMemeland",),
                )
                if not bot_ok:
                    try:
                        self._repo.log_submission(
                            hunt_id=hunt.id, dm_id=post.tweet_id,
                            sender_x_id=post.author_id, wallet=None,
                            sender_handle=post.author_handle,
                            submitted_claim_code=hunt.claim_code,
                            outcome="bot_disqualified", x_created_at=post.created_at,
                        )
                    except Exception as e:  # noqa: BLE001
                        self._notify(f"bot post {post.tweet_id} not logged: {e!r}")
                    self._notify(
                        f"claim by @{post.author_handle} disqualified (bot screen: "
                        f"{reason}) — no public reply."
                    )
                    _done(post)
                    continue

                # A VALID claim.
                try:
                    outcome_row_id = self._repo.log_submission(
                        hunt_id=hunt.id, dm_id=post.tweet_id,
                        sender_x_id=post.author_id, wallet=None,
                        sender_handle=post.author_handle,
                        submitted_claim_code=hunt.claim_code,
                        outcome="pending", x_created_at=post.created_at,
                    )
                except Exception as e:  # noqa: BLE001
                    self._notify(f"🚨 valid claim {post.tweet_id} could not be logged: {e!r}")
                tweet_int = int(post.tweet_id) if post.tweet_id.isdigit() else 0
                entry = (post.created_at, tweet_int, post, outcome_row_id)
                # Rows stay 'pending' while an entry is candidate OR queued —
                # the queue is rebuilt from 'pending' rows after a restart
                # (adversarial review #2); terminal 'late' is set only when
                # the hunt actually resolves with someone else.
                if pending is not None:
                    # Someone is already being asked for the wallet: queue this
                    # claim (earliest-first) for a possible timeout promotion.
                    wait_queue.append(entry)
                    wait_queue.sort(key=lambda e: (e[0], e[1]))
                    self._sys_reply("late", post, POST_REPLY_LATE, sys_sent)
                elif win_cand is None:
                    win_cand = entry
                    settle_until = self._clock.now() + timedelta(
                        seconds=2 * self._poll_interval_s
                    )
                    self._notify(
                        f"win candidate: @{post.author_handle} (post "
                        f"{post.tweet_id}, {post.created_at:%H:%M:%S}) — settling "
                        "for two cycles before the public ask."
                    )
                elif (post.created_at, tweet_int) < (win_cand[0], win_cand[1]):
                    displaced = win_cand
                    win_cand = entry
                    self._notify(
                        f"settlement: EARLIER claim by @{post.author_handle} "
                        f"({post.created_at:%H:%M:%S}) replaces the candidate."
                    )
                    wait_queue.append(displaced)
                    wait_queue.sort(key=lambda e: (e[0], e[1]))
                    self._sys_reply("late", displaced[2], POST_REPLY_LATE, sys_sent)
                else:
                    wait_queue.append(entry)
                    wait_queue.sort(key=lambda e: (e[0], e[1]))
                    self._sys_reply("late", post, POST_REPLY_LATE, sys_sent)
                _done(post)

            # ---- Phase 2b: wallet timeout ----
            if pending is not None and self._clock.now() >= pending["due_at"]:
                self._notify(
                    f"wallet window expired for @{pending['author_handle']} — "
                    "claim lapses; the hunt reopens."
                )
                try:
                    self._publisher.reply_post(
                        POST_REPLY_TIMED_OUT, in_reply_to=pending["ask_tweet_id"]
                    )
                except Exception as e:  # noqa: BLE001
                    self._notify(f"timeout reply failed (non-fatal): {e!r}")
                if pending.get("row_id") is not None:
                    try:
                        self._repo.set_submission_outcome(pending["row_id"], "timed_out")
                    except Exception as e:  # noqa: BLE001
                        self._notify(f"timed_out outcome not recorded: {e!r}")
                self._set_pending(hunt, None)
                pending = self._promote_from_queue(hunt, wait_queue, sys_sent, ask_attempts)

            # ---- Phase 2b2: a stalled promotion (failed ask) is retried ----
            if pending is None and win_cand is None and wait_queue:
                pending = self._promote_from_queue(hunt, wait_queue, sys_sent, ask_attempts)

            # ---- Phase 2c: settlement -> the public ask ----
            if win_cand is not None and pending is None:
                if self._clock.now() >= settle_until:
                    pending = self._ask_wallet(hunt, win_cand, sys_sent)
                    if pending is not None:
                        win_cand = None
                        settle_until = None
                    elif self._drop_unaskable(hunt, win_cand, ask_attempts):
                        # e.g. the winning post was deleted: the ask 400s
                        # forever — drop the claim instead of wedging the hunt.
                        win_cand = None
                        settle_until = None
                        pending = self._promote_from_queue(
                            hunt, wait_queue, sys_sent, ask_attempts
                        )
                self._clock.sleep(self._poll_interval_s)
                continue  # no clues while a claim is being decided

            if pending is not None:
                self._clock.sleep(self._poll_interval_s)
                continue  # no clues while waiting for the winner's wallet

            # ---- Phase 3: next clue ----
            clue_index, next_due = self._maybe_post_clue(
                hunt, clue_index, next_due, CLUE_FOLLOWUP_CLAIM_HINT
            )

            self._clock.sleep(self._poll_interval_s)

        raise RuntimeError("claim loop exceeded max rounds without a winner")

    def _promote_from_queue(
        self,
        hunt: PreparedHunt,
        wait_queue: list[tuple],
        sys_sent: dict[str, set[str]],
        ask_attempts: dict[str, int],
    ) -> dict | None:
        """After a lapse: the earliest queued valid claim gets the wallet ask.
        Empty queue => the window simply reopens (next valid code post wins —
        possibly the original claimant posting again)."""
        if not wait_queue:
            return None
        entry = wait_queue[0]
        pending = self._ask_wallet(hunt, entry, sys_sent)
        if pending is None:
            # Ask failed. Transient X hiccup -> entry stays queued and is
            # retried next cycle; permanently unaskable (deleted post) ->
            # drop it so the queue never wedges.
            if self._drop_unaskable(hunt, entry, ask_attempts):
                wait_queue.pop(0)
            return None
        wait_queue.pop(0)
        return pending

    def _drop_unaskable(
        self, hunt: PreparedHunt, entry: tuple, ask_attempts: dict[str, int]
    ) -> bool:
        """Count a failed public ask for this claim; past the cap, retire the
        claim (row -> 'timed_out') and tell the operator. Returns True when
        the claim was dropped."""
        tid = entry[2].tweet_id
        ask_attempts[tid] = ask_attempts.get(tid, 0) + 1
        if ask_attempts[tid] < self._MAX_ASK_ATTEMPTS:
            return False
        self._notify(
            f"🚨 could not post the wallet ask for claim {tid} "
            f"({self._MAX_ASK_ATTEMPTS} attempts — post deleted?) — dropping "
            "the claim; the hunt continues."
        )
        if entry[3] is not None:
            try:
                self._repo.set_submission_outcome(entry[3], "timed_out")
            except Exception:  # noqa: BLE001
                pass
        return True

    def _mark_queue_late(self, wait_queue: list[tuple]) -> None:
        """The hunt resolved: every still-queued claim is terminally 'late'
        (their rows were 'pending' so a restart could rebuild the queue)."""
        for entry in wait_queue:
            if entry[3] is not None:
                try:
                    self._repo.set_submission_outcome(entry[3], "late")
                except Exception:  # noqa: BLE001
                    pass

    def _maybe_taunt_chatter(
        self,
        hunt: PreparedHunt,
        post,
        taunted: set[str],
        judged: set[str],
        taunt_budget: dict,
        banned: tuple[str, ...],
    ) -> None:
        """Non-code chatter: one jeer per profile, ONLY if the LLM judge finds
        it game-funny (fail-closed — 'Good morning' gets silence). The reply is
        logged ('taunted') so the once-per-profile promise survives restarts.
        Each author's chatter is judged at most ONCE per process (LLM cost:
        one flood of 'gm' posts must not become one LLM call each), and the
        global per-hunt taunt budget applies."""
        if self._taunt_engine is None or post.author_id in taunted:
            return
        if post.author_id in judged:
            return
        judged.add(post.author_id)
        if taunt_budget["used"] >= self._MAX_TAUNTS_PER_HUNT:
            return
        try:
            if not self._taunt_engine.should_taunt_chatter(post.text):
                return
            jeer = self._taunt_engine.taunt(post.text, banned)
            self._publisher.reply_post(jeer, in_reply_to=post.tweet_id)
        except Exception as e:  # noqa: BLE001
            self._notify(f"chatter taunt failed (non-fatal): {e!r}")
            return
        taunted.add(post.author_id)
        taunt_budget["used"] += 1
        try:
            self._repo.log_submission(
                hunt_id=hunt.id, dm_id=post.tweet_id, sender_x_id=post.author_id,
                sender_handle=post.author_handle,
                wallet=None, outcome="taunted", x_created_at=post.created_at,
            )
        except Exception as e:  # noqa: BLE001
            self._notify(f"taunted post {post.tweet_id} not logged: {e!r}")

    def _process_wallet_reply(
        self, hunt: PreparedHunt, pending: dict, post, sys_sent: dict[str, set[str]]
    ):
        """A reply from the pending winner to our ask tweet. Returns a Winner,
        'lapsed' (claim failed terminally — hunt reopens), or None (waiting)."""
        wallet = extract_wallet(post.text)
        if not wallet:
            # No parseable/checksum-valid address: ONE public correction ask
            # per claim window; the clock keeps ticking.
            if not pending["corrected"]:
                pending["corrected"] = True
                try:
                    self._publisher.reply_post(
                        POST_REPLY_INVALID_WALLET, in_reply_to=post.tweet_id
                    )
                except Exception as e:  # noqa: BLE001
                    self._notify(f"invalid-wallet reply failed (non-fatal): {e!r}")
            return None

        # Final validation — the SAME production validator as the DM channel
        # (code trivially passes; holding, reshare re-check, bot screen).
        parsed = ParsedDM(
            dm_id=post.tweet_id, sender_x_id=post.author_id, wallet=wallet,
            claim_code=hunt.claim_code, claim_candidates=(hunt.claim_code,),
        )
        try:
            res = self._validator.validate(parsed, hunt)
        except Exception as e:  # noqa: BLE001
            # Infrastructure down, not a bad wallet: the caller keeps the reply
            # unconsumed and extends the window (adversarial review #1).
            self._notify(f"final validation failed (will retry, reply kept): {e!r}")
            return "retry"

        if res.won:
            if pending.get("row_id") is not None:
                try:
                    self._repo.set_submission_outcome(
                        pending["row_id"], "won", wallet=wallet
                    )
                except Exception as e:  # noqa: BLE001
                    self._notify(f"won outcome not recorded ({e!r}) — fix the row manually.")
            self._set_pending(hunt, None)
            winner = Winner(
                submission=Submission(
                    dm_id=pending["claim_tweet_id"],
                    sender_x_id=post.author_id,
                    sender_handle=pending["author_handle"] or post.author_handle,
                    body=post.text,
                    created_at=pending["claim_created_at"],
                ),
                wallet=wallet,
                submission_row_id=pending.get("row_id"),
                holder=getattr(res, "holder", True),
            )
            self._transition(hunt, HuntState.RESOLVING)
            self._notify(f"winner: @{winner.submission.sender_handle}")
            return winner

        # Terminal failures reopen the hunt (Pedro's ruleset: the claim lapses).
        outcome = res.outcome
        if pending.get("row_id") is not None:
            try:
                self._repo.set_submission_outcome(pending["row_id"], outcome)
            except Exception:  # noqa: BLE001
                pass
        reply = {
            "no_holding": POST_REPLY_NO_HOLDING,
            "no_reshare": POST_REPLY_MISSING_REPOST,
        }.get(outcome)
        if reply:
            try:
                self._publisher.reply_post(reply, in_reply_to=post.tweet_id)
            except Exception as e:  # noqa: BLE001
                self._notify(f"{outcome} reply failed (non-fatal): {e!r}")
        self._notify(
            f"pending claim by @{pending['author_handle']} failed final "
            f"validation ({outcome}) — the hunt reopens."
        )
        self._set_pending(hunt, None)
        return "lapsed"

    def _void_unclaimed(self, hunt: PreparedHunt) -> None:
        """Nobody won before the deadline: end the hunt publicly and cleanly.
        The persona IS undressed here (unlike a completed hunt) — leaving a live
        claim code in the bio of a dead hunt would mislead players."""
        hours = self._hunt_timeout_h
        self._notify(f"hunt #{hunt.number} expired unclaimed after {hours}h — voiding.")
        self._transition(hunt, HuntState.VOIDED)
        try:
            self._publisher.post(
                f"Hunt #{hunt.number} ends unclaimed. The persona keeps its "
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
                    f"payout for hunt #{hunt.number} (db #{hunt.id}) already "
                    f"on-chain ({row['tx_hash']}) — reusing it, NOT re-sending."
                )
                return PayoutReceipt(
                    tx_hash=row["tx_hash"],
                    amount_fmml=_as_int(row.get("amount_fmml") or hunt.prize_fmml),
                )
            raise RuntimeError(
                f"unresolved payout intent for hunt #{hunt.number} (db #{hunt.id}) "
                f"(status {row.get('status')!r}) — a transfer may be in flight. "
                "Check the chain (hot wallet nonce/txs) and settle manually."
            )

        # Holder reward split (2026-07-31): the pot is advertised in full; a
        # winner whose wallet fails the holding rule is paid this reduced share.
        paid_fmml = (
            hunt.prize_fmml
            if winner.holder
            else max(1, hunt.prize_fmml * self._non_holder_pct // 100)
        )
        if not winner.holder:
            self._notify(
                f"winner @{winner.submission.sender_handle} is NOT a holder — "
                f"paying {self._non_holder_pct}% of the pot: {paid_fmml:,} $FIND."
            )
        intent_id = self._repo.create_payout_intent(
            hunt_id=hunt.id, wallet=winner.wallet, amount_fmml=paid_fmml
        )
        try:
            receipt = self._payout.send_prize(
                hunt_id=hunt.id, to_wallet=winner.wallet, amount_fmml=paid_fmml
            )
        except Exception as e:
            # The tx MAY have been broadcast (e.g. receipt timeout). Mark it so
            # nothing ever auto-retries this hunt's payout.
            try:
                self._repo.set_payout_status(intent_id, "unknown", error=repr(e))
            except Exception as e2:  # noqa: BLE001
                self._notify(f"could not mark payout intent as unknown: {e2!r}")
            self._notify(
                f"🚨 payout for hunt #{hunt.number} (db #{hunt.id}) errored MID-SEND: {e!r}. The tx "
                "may still mine — check the chain before ANY manual retry."
            )
            raise
        # ---- THE MONEY IS OUT. From here on, NOTHING may kill the hunt ----
        # (Hunt #2 P1: record_winner crashed on a NOT NULL column and the hunt
        # died AFTER paying, without the Winner Announcement. Bookkeeping
        # failures notify loudly and the flow continues to the reveal.)
        try:
            self._repo.set_payout_status(intent_id, "sent", tx_hash=receipt.tx_hash)
        except Exception as e:  # noqa: BLE001
            self._notify(
                f"🚨 payout SENT ({receipt.tx_hash}) but could not be marked "
                f"'sent' in the DB: {e!r} — fix the payouts row manually."
            )
        try:
            self._repo.record_winner(
                hunt_id=hunt.id, winner_x_id=winner.submission.sender_x_id,
                wallet=winner.wallet, prize_fmml=paid_fmml,
                submission_id=winner.submission_row_id,
            )
        except Exception as e:  # noqa: BLE001
            self._notify(
                f"🚨 winner paid ({receipt.tx_hash}) but record_winner failed: "
                f"{e!r} — insert the winners row manually. The reveal continues."
            )
        self._notify(f"paid {paid_fmml:,} $FIND to {winner.wallet} ({receipt.tx_hash})")
        return receipt

    def _reveal(self, hunt: PreparedHunt, winner: Winner, receipt) -> None:
        """Invariant (Hunt #2 P1): once the money is out, the announcement
        ALWAYS goes out. DB transitions are best-effort here; the post retries."""
        now = self._clock.now()
        try:
            self._transition(
                hunt, HuntState.PENDING_CLEANUP,
                resolved_at=now, cleanup_due_at=now + timedelta(seconds=self._cleanup_window_s),
            )
        except Exception as e:  # noqa: BLE001
            hunt.state = HuntState.PENDING_CLEANUP  # keep the flow moving
            self._notify(f"🚨 transition to PENDING_CLEANUP failed in the DB: {e!r} — fix the row manually.")
        # Time-to-win measured from Clue 1 (live_at), not from the prep dress.
        start = hunt.live_at or hunt.started_at
        elapsed = self._clock.now() - start if start else None
        data = WinnerData(
            hunt_n=hunt.number,
            winner_handle=winner.submission.sender_handle,
            time_to_win=_fmt_duration(elapsed),
            # The amount actually TRANSFERRED (a non-holder gets the reduced
            # share) — the reveal must never overstate the payout.
            prize_amount=f"{_as_int(receipt.amount_fmml) or hunt.prize_fmml:,}",
            tx_link=receipt.tx_hash,
            persona_handle=hunt.persona.handle,
            persona_user_id=hunt.persona.x_user_id,
            claim_code=hunt.claim_code,
            salt=hunt.salt,
            holder=winner.holder,
            non_holder_pct=self._non_holder_pct,
        )
        text = winner_announcement(data)
        for attempt in range(3):
            try:
                self._publisher.post(text, long_post=True)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    self._notify(
                        f"🚨 Winner Announcement FAILED 3x: {e!r} — winner is paid "
                        f"({receipt.tx_hash}); POST THE REVEAL MANUALLY."
                    )
                else:
                    self._notify(f"winner announcement failed (retrying): {e!r}")
                    self._clock.sleep(30)
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
            f"Hunt #{hunt.number} closed. {len(log)} submissions logged for public audit."
        )
        self._transition(hunt, HuntState.DONE)
        self._notify(f"hunt #{hunt.number} done; persona {hunt.persona.handle} retired")

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
            self._notify(f"hunt #{hunt.number} (db #{hunt.id}) was stuck in PREPARING — voiding it.")
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

        if state is HuntState.PREPPED:
            # Persona dressed, prep posts possibly mid-schedule, Clue 1 not out.
            # Re-enter the prep window: golive_due_at/abort_prep live in the DB,
            # so the operator's commands survive the restart for free.
            self._notify(
                f"hunt #{hunt.number} resumed in PREP window — continuing to "
                "publish prep posts until go-live."
            )
            if not self._prep_window(hunt):
                return
            self._go_live(hunt)
            winner = self._submission_loop(hunt)
            if winner is None:
                return
            receipt = self._pay(hunt, winner)
            self._reveal(hunt, winner, receipt)
            self._retire(hunt)
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
                    f"🚨 hunt #{hunt.number} (db #{hunt.id}) already has a WON submission "
                    f"(dm {won_rows[0].get('dm_id')}) but died before paying. "
                    "NOT auto-paying — verify and settle manually."
                )
                return
            since = (
                self._latest_claim_marker(hunt.id)
                if self._claim_source is not None
                else self._latest_dm_marker(hunt.id)
            )
            if hunt.ctx is None:
                self._notify(
                    f"hunt #{hunt.number} resumed WITHOUT persona identity (old DB "
                    "schema): DMs and the winner flow work, but no further clues "
                    "can be generated."
                )
            self._notify(
                f"hunt #{hunt.number} RESUMED live after a restart — "
                f"{len(hunt.clues)} clues out, marker {since or 'start'}."
            )
            winner = self._submission_loop(hunt, since=since)
            if winner is None:
                return
            receipt = self._pay(hunt, winner)
            self._reveal(hunt, winner, receipt)
            self._retire(hunt)
            return

        if state in (HuntState.RESOLVING, HuntState.PAYING):
            self._notify(
                f"🚨 hunt #{hunt.number} (db #{hunt.id}) died in {state.value.upper()} — a payout may "
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
            self._notify(f"hunt #{hunt.number} resumed at cleanup — retiring the persona.")
            self._retire(hunt)
            return

        if state is HuntState.RETIRING:
            self._notify(f"hunt #{hunt.number} resumed mid-retire — finishing.")
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
            live_at=_as_dt(row.get("live_at")),
            # Pre-migration rows have no hunt_number; the DB id is a better
            # fallback than a hardcoded 1 (at least it's unique and traceable).
            number=_as_int(row.get("hunt_number")) or int(row["id"]),
        )

    def _latest_claim_marker(self, hunt_id: int) -> str | None:
        """Resume marker for the claim channel. Sweep-discovered posts
        (wrong_door/taunted can come from the markerless thread sweep) are
        EXCLUDED: their ids may be ahead of what the mentions stream actually
        delivered, and a poisoned marker would permanently skip unread
        mentions (adversarial review #6). Re-reading a few already-processed
        posts is free (processed-set dedupe + 24h billing dedup)."""
        ids = [
            int(s["dm_id"]) for s in self._repo.submissions_for_hunt(hunt_id)
            if s.get("dm_id") and str(s["dm_id"]).isdigit()
            and s.get("outcome") not in ("wrong_door", "taunted")
        ]
        return str(max(ids)) if ids else None

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

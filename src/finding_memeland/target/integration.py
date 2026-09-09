"""Target ↔ Orchestrator integration (Option A) — the relic_integration seam,
reused.

Same deliberate design as persona/relic_integration.py: almost all target
logic lives HERE; state_machine.py gets small `hunt.target is not None`
branches at the same eight delegation points it already has for relics, so
the file that runs live hunts and moves money changes as little as possible
and the target behaviour is auditable in one place.

What a target hunt carries on the hunt row (all set at prepare time):
  · `target_sealed`     — the SealedTarget (target + salt + commitment +
                          decoys), encrypted with TARGET_POOL_KEY
  · `target_ctx_sealed` — the artwork description (vision output), also
                          encrypted: it describes the answer's picture
  · `target_epoch`      — the curation epoch id
  · `integrity_hash`    — the v2 COMMITMENT (published in Clue 1);
                          `integrity_salt` the salt; `claim_code` is EMPTY —
                          a target hunt has no code
  · `target_void_id`    — set when a hunt is voided over the target's
                          mutation/burn: the next prepare EXCLUDES it

Everything effectful comes through `TargetPorts` (main.py builds it from
adapters); the wiring logic tests offline with fakes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from ..content.relic_clues import PUZZLE_CLUES
from ..orchestrator.ports import ReadyPersona
from .claim import ClaimJudge
from .clues import TargetClueContext
from .hunt import (
    ACT_CONTINUE,
    ACT_HOLD,
    ACT_PAY_NOTED,
    ACT_RELAUNCH,
    LIVE_BURNED,
    LIVE_MUTATED,
    LIVE_UNAVAILABLE,
    PHASE_CLAIM,
    PHASE_PUZZLE,
    PHASE_REVEAL,
    LIVE_HASH_UNAVAILABLE,
    HoldLedger,
    LiveCheck,
    LiveHash,
    LiveVerdict,
    SealedTarget,
    SealedTargetCipher,
    SprayDetector,
    TargetHuntPreparer,
    live_policy,
    void_reveal_ingredients,
)
from .selector import CurationEpoch
from .templates import (
    POST_REPLY_FORMAT,
    POST_REPLY_ONE_TOKEN,
    POST_REPLY_UNRESOLVED_LINK,
    TargetWinnerData,
    VoidRevealData,
    target_winner_announcement,
    void_reveal,
)


@dataclass
class TargetPorts:
    """Everything the target branch needs, injected once into the
    Orchestrator (`target=TargetPorts(...)`, with `target_launch=True`)."""

    epoch: CurationEpoch
    preparer: TargetHuntPreparer
    cipher: SealedTargetCipher
    clue_engine: object                       # TargetClueEngine (or a fake)
    describe_image: Callable[[SealedTarget], str]   # vision, batched (clues.describe_image_batched)
    live_check: LiveCheck | None = None       # None = no live checks (sim only)
    resolve_link: Callable[[str], object] | None = None
    spray: SprayDetector | None = None
    # The live METADATA HASH for a void/pay-noted post — resolved ONCE, at
    # that moment, through any gateway (the hunt is over or decided; the
    # repeated live check never touches a gateway — hunt.LiveCheck). None
    # = unresolvable, printed as such. Optional: sims and dry-runs omit it.
    live_hash: Callable[[SealedTarget], LiveHash] | None = None
    # How many redraws the content guard may force before /launch refuses.
    max_content_redraws: int = 3
    # HOLD ceiling and cadence (Opus, 06/09, P1-B): a hold without a ceiling
    # and without a second notice is a void in slow motion with nobody
    # watching. Re-notify every `hold_renotify_s`; past `max_hold_s` the
    # operator is CALLED to decide — nothing is decided automatically.
    hold_renotify_s: float = 3600.0
    max_hold_s: float = 6 * 3600.0            # one EPISODE
    # The accumulated ceiling (Opus, 06/09, P1): an RPC that oscillates —
    # five hours down, ten minutes up, five hours down — never trips the
    # episode ceiling while the deadline has been frozen for a day. The
    # system must detect DEGRADATION, not only death.
    max_total_hold_s: float = 12 * 3600.0


def target_label(number: int) -> str:
    """Neutral operator label — never the target's name (blind mode)."""
    return f"target:hunt{number}"


# --------------------------------------------------------------------------- #
# Prepare                                                                      #
# --------------------------------------------------------------------------- #


def recent_void_ids(orch) -> frozenset[str]:
    """Target ids voided over mutation/burn — excluded from the next draw.
    Read through the repo when it offers `recent_target_voids`; an older repo
    simply excludes nothing (the pool is 10^5; a repeat is unlikely, not
    unsafe)."""
    fn = getattr(orch._repo, "recent_target_voids", None)
    if fn is None:
        return frozenset()
    try:
        return frozenset(str(v) for v in fn())
    except Exception:  # noqa: BLE001
        return frozenset()


def prepare_target_hunt(orch, prize_fmml: int, min_balance_fmml: int, *,
                        ladder_exempt: bool = False):
    """The target twin of prepare_relic_hunt. Returns a PreparedHunt.

    Order: 1. gate + draw + judge + seal (TargetHuntPreparer — refuses
    loudly unless the gate is GREEN); 2. the artwork description through
    the batched vision path (refuses if the art can't be fetched); 3. the
    hunt row with the sealed payloads; 4. the in-memory hunt."""
    from ..orchestrator.state_machine import HuntState, PreparedHunt

    ports: TargetPorts = orch._target
    # The immediate relaunch after a void is the case that matters (Opus,
    # 06/09): the id voided in THIS process is excluded even when the repo
    # has no `recent_target_voids` yet.
    exclude = recent_void_ids(orch) | frozenset(
        [x for x in [getattr(orch, "_last_target_void_id", None)] if x])
    # The content guard lives on the image pass (Opus, 06/09): a refused
    # artwork excludes its id and the draw runs again — bounded, so a pool
    # full of refusals refuses the launch instead of grinding.
    from .clues import ContentRefused
    from .hunt import LaunchRefused
    refused = 0
    while True:
        sealed = ports.preparer.prepare(ports.epoch, exclude=exclude)
        try:
            image_description = ports.describe_image(sealed)
            break
        except ContentRefused as e:
            refused += 1
            exclude = exclude | frozenset({e.target_id})
            orch._notify(f"content guard refused a drawn artwork ({refused}) "
                         "— redrawing (id excluded, never named)")
            if refused >= ports.max_content_redraws:
                # `from None` (P2-1): the chained ContentRefused carries a
                # target id — a refused one, not the hunt's, but a traceback
                # is not the place for any id
                raise LaunchRefused(
                    f"content guard refused {refused} draws in a row — "
                    "launch refused; review the judge/pool before retrying"
                ) from None
    ctx = TargetClueContext.from_target(sealed.target,
                                        image_description=image_description)

    number = orch._next_number()
    prize_fmml = int(prize_fmml)
    prize_usd = orch._prize_usd_of(prize_fmml)
    started_at = orch._clock.now()

    persona = ReadyPersona(
        id=f"target-{number}", handle=target_label(number),
        x_user_id="",                    # NEVER the target id (blind + no leak)
        access_token="", access_secret="",
    )
    hunt_id = orch._repo.create_hunt(
        persona_id=None, persona_display_name=None, persona_bio=None,
        claim_code="",                   # no code in Option A
        integrity_salt=sealed.salt,
        integrity_hash=sealed.commitment,
        prize_usd=prize_usd, prize_fmml=prize_fmml,
        min_balance_fmml=min_balance_fmml,
        holding_hours=orch._holding_hours,
        started_at=started_at,
        state=HuntState.PREPARING.value,
        hunt_number=number,
        ladder_exempt=bool(ladder_exempt),
        target_sealed=ports.cipher.seal(sealed),
        target_ctx_sealed=ports.cipher._cipher.encrypt(  # noqa: SLF001 — same key
            json.dumps({"image_description": image_description}, ensure_ascii=False)),
        target_epoch=ports.epoch.epoch_id,
    )
    hunt = PreparedHunt(
        id=hunt_id, persona=persona, identity=None, ctx=ctx,
        claim_code="", salt=sealed.salt, integrity_hash=sealed.commitment,
        prize_usd=prize_usd or 0.0, prize_fmml=prize_fmml,
        min_balance_fmml=min_balance_fmml, holding_hours=orch._holding_hours,
        state=HuntState.PREPARING, started_at=started_at, number=number,
        predressed=True,                 # no prep window: nothing to index
        target=sealed,
    )
    # P2-3: the uniqueness check's counters (never names) — `crowded` says
    # how often a full page hid the candidate and a draw was spent on it.
    stats = getattr(getattr(ports.preparer, "_name_is_unique", None), "stats", None)
    stats_line = (" | uniqueness " + ", ".join(f"{k}={v}" for k, v in stats.items())
                  if isinstance(stats, dict) else "")
    orch._notify(
        f"hunt #{number}: target (blind) sorteado do snapshot da época "
        f"{ports.epoch.epoch_id!r} — gate GREEN, juiz ✓, arte descrita ✓, "
        f"{len(sealed.decoys)} decoys selados{stats_line}. A lançar."
    )
    return hunt


# --------------------------------------------------------------------------- #
# Clues                                                                        #
# --------------------------------------------------------------------------- #


def claim_matcher_for(orch, hunt):
    from ..claims.matcher import CodeClaimMatcher, TargetClaimMatcher
    if getattr(hunt, "target", None) is not None:
        return TargetClaimMatcher(
            judge=ClaimJudge(target_id=hunt.target.id()),
            resolve_link=orch._target.resolve_link,
            format_reply=POST_REPLY_FORMAT,
            unresolved_reply=POST_REPLY_UNRESOLVED_LINK,
            one_token_reply=POST_REPLY_ONE_TOKEN,
        )
    return CodeClaimMatcher(hunt.claim_code)


def phase_for(clue_index: int) -> str:
    return PHASE_PUZZLE if clue_index <= PUZZLE_CLUES else PHASE_REVEAL


PRE_CLUE_POST = "post"
PRE_CLUE_SKIP = "skip"        # hold: no clue this round, deadline frozen
PRE_CLUE_ENDED = "ended"      # voided (relaunch or void-reveal): stop the loop


def _persist_hold(orch, hunt) -> None:
    """held_seconds → the hunt row on every hold transition/notice, so a
    crash during an outage keeps the accumulated time (Opus, 06/09)."""
    try:
        now = orch._clock.now().timestamp()
        orch._repo.update_hunt(hunt.id,
                               target_hold_s=int(hunt.target_hold.held_seconds(now)))
    except Exception as e:  # noqa: BLE001 — bookkeeping; the ledger stays in memory
        orch._notify(f"hold seconds not persisted: {e!r}")


def manage_hold(orch, hunt, *, holding: bool, reason: str) -> None:
    """ONE ledger for every hold cause (live check unavailable, search guard
    unverifiable, live check at claim time): start/stop it, re-notify on a
    cadence, and past MAX_HOLD call the operator to DECIDE — the code never
    resolves a hold on its own."""
    ports: TargetPorts = orch._target
    ledger: HoldLedger = hunt.target_hold
    now = orch._clock.now().timestamp()
    if holding:
        if not ledger.is_holding():
            ledger.start(now, reason=reason)
            orch._notify(f"⏸ HOLD ({reason}) — no clue this round, void deadline "
                         "frozen. Re-notifying hourly.")
            _persist_hold(orch, hunt)
            return
        # keep the row's held seconds fresh while holding (throttled): a
        # crash mid-outage must find the time already on the row
        if now - getattr(ledger, "last_persist", 0.0) >= 60.0:
            ledger.last_persist = now
            _persist_hold(orch, hunt)
        if now - (ledger.last_notice or now) >= ports.hold_renotify_s:
            ledger.last_notice = now
            cur = ledger.current_hold_seconds(now)
            total = ledger.held_seconds(now)
            if cur >= ports.max_hold_s or total >= ports.max_total_hold_s:
                which = ("episode" if cur >= ports.max_hold_s
                         else "ACCUMULATED across the hunt")
                orch._notify(
                    f"🚨 HOLD past MAX ({which}: episode {cur/3600:.1f}h, total "
                    f"{total/3600:.1f}h, {reason}) — the hunt is frozen and "
                    "nothing will be decided automatically. DECIDE: /resume once "
                    "the service is back, or void by hand.")
            else:
                orch._notify(f"⏸ still on HOLD ({reason}) for {cur/3600:.1f}h — "
                             "deadline frozen.")
            _persist_hold(orch, hunt)
        return
    if ledger.is_holding():
        total = ledger.held_seconds(now)
        ledger.stop(now)
        orch._notify(f"▶️ hold released — deadline extended by {total:.0f}s total.")
        _persist_hold(orch, hunt)


def pre_clue_live_check(orch, hunt, clue_index: int) -> str:
    """Decision 4: BEFORE every clue, read the target live (inside its sealed
    decoy batch) and act proportionally. Returns what the loop should do."""
    ports: TargetPorts = orch._target
    if ports.live_check is None:
        return PRE_CLUE_POST
    try:
        verdict = ports.live_check.check(hunt.target)
    except Exception as e:  # noqa: BLE001 — the checker itself broke: hold
        verdict = LiveVerdict(LIVE_UNAVAILABLE, None, 0)
        orch._notify(f"live check errored ({type(e).__name__}) — treating as unavailable")
    action = live_policy(verdict.status, phase=phase_for(clue_index))
    if action == ACT_CONTINUE:
        manage_hold(orch, hunt, holding=False, reason="")
        return PRE_CLUE_POST
    if action == ACT_HOLD:
        manage_hold(orch, hunt, holding=True, reason="live check unavailable")
        return PRE_CLUE_SKIP
    # mutated / burned
    relaunching = action == ACT_RELAUNCH
    void_target(orch, hunt, cause=verdict.status, live=verdict,
                relaunching=relaunching)
    return PRE_CLUE_ENDED


def clue_failed(orch, hunt, exc: BaseException) -> bool:
    """Called by _maybe_post_clue when clue generation raised. A search guard
    that could not verify (SearchGuardUnavailable) is an OUTAGE OF OURS: it
    enters the SAME hold ledger as the live check (Opus, 06/09, (b)) — the
    ramp stops AND the deadline freezes, never one without the other.
    Returns True when the failure was turned into a hold."""
    from .clues import SearchGuardUnavailable
    if isinstance(exc, SearchGuardUnavailable):
        manage_hold(orch, hunt, holding=True, reason="search guard unverifiable")
        return True
    return False


def clue_posted(orch, hunt) -> None:
    """A clue went out: whatever hold was open is over."""
    manage_hold(orch, hunt, holding=False, reason="")


def claim_time_live_check(orch, hunt) -> str:
    """At a VALID claim: 'ok' (pay), 'pay_noted' (mutated/burned after the
    claim — pay, note it in the reveal), or 'retry' (unavailable: do not
    decide, re-check next cycle; the winner's window is extended by the
    caller like the reshare-check outage)."""
    ports: TargetPorts = orch._target
    if ports.live_check is None:
        return "ok"
    try:
        verdict = ports.live_check.check(hunt.target)
    except Exception:  # noqa: BLE001
        verdict = LiveVerdict(LIVE_UNAVAILABLE, None, 0)
    action = live_policy(verdict.status, phase=PHASE_CLAIM, valid_claim=True)
    if action == ACT_HOLD:
        manage_hold(orch, hunt, holding=True,
                    reason="live check unavailable at claim (winner waiting)")
        return "retry"
    manage_hold(orch, hunt, holding=False, reason="")
    if action == ACT_PAY_NOTED:
        hunt.target_pay_noted = True
        lh = _live_hash(orch, hunt.target)
        hunt.target_live_hash = lh.sha256
        hunt.target_live_hash_status = lh.status
        hunt.target_live_token_uri = verdict.live_token_uri
        orch._notify("⚠️ target mutated/burned AFTER the winning claim — paying "
                     "anyway (identity binds, not ownership); noted in the reveal.")
        return "pay_noted"
    return "ok"


def _live_hash(orch, sealed: SealedTarget) -> LiveHash:
    """Tri-state (R8): a port that is missing or that raised MEASURED
    NOTHING — that is "unavailable", never "unresolvable"."""
    ports: TargetPorts = orch._target
    if ports.live_hash is None:
        return LiveHash.unavailable()
    try:
        out = ports.live_hash(sealed)
    except Exception:  # noqa: BLE001 — our side failed: unavailable
        return LiveHash.unavailable()
    return out if isinstance(out, LiveHash) else LiveHash.unavailable()


def void_target(orch, hunt, *, cause: str, live: LiveVerdict | None,
                relaunching: bool) -> None:
    """Void-reveal (mutation/burn/unclaimed): publish EVERY ingredient, mark
    the target id excluded, transition to DONE. Prize back to the vault."""
    from ..orchestrator.state_machine import HuntState

    sealed: SealedTarget = hunt.target
    ing = (void_reveal_ingredients(sealed, live) if live is not None
           else {"live_token_uri": None})
    # the live hash: once, now, through our gateway — the hunt is over.
    # Tri-state (R8): the post never says "reverts" over OUR outage.
    lh = (_live_hash(orch, sealed)
          if cause in (LIVE_MUTATED, LIVE_BURNED) else LiveHash.unavailable())
    text = void_reveal(VoidRevealData(
        hunt_n=hunt.number, cause=cause,
        target_name_onchain=sealed.target.name_onchain,
        target_id=sealed.id(), metadata_sha256=sealed.target.metadata_sha256,
        salt=sealed.salt, live_metadata_sha256=lh.sha256,
        live_hash_status=lh.status,
        token_uri=sealed.target.token_uri,
        live_token_uri=ing.get("live_token_uri"),
        relaunching=relaunching,
    ))
    orch._notify(f"hunt #{hunt.number} VOID ({cause})"
                 + (" — relaunch with /launch (the voided target is excluded)."
                    if relaunching else " — prize back to the vault."))
    if hunt.state is not HuntState.VOIDED:
        orch._transition(hunt, HuntState.VOIDED)
    try:
        orch._publisher.post(text, long_post=True)
    except Exception as e:  # noqa: BLE001
        orch._notify(f"🚨 void-reveal post FAILED: {e!r} — POST IT MANUALLY "
                     "(the ingredients are on the hunt row).")
    try:
        orch._repo.update_hunt(hunt.id, target_void_id=sealed.id(),
                               target_void_cause=cause)
    except Exception as e:  # noqa: BLE001
        orch._notify(f"target_void_id not recorded: {e!r} — the next draw may "
                     "repeat this target; note it manually.")
    orch._last_target_void_id = sealed.id()
    orch._transition(hunt, HuntState.RETIRING)
    orch._transition(hunt, HuntState.DONE)


# --------------------------------------------------------------------------- #
# Anti-spray                                                                   #
# --------------------------------------------------------------------------- #


# KNOWN LIMIT (Opus audit 09/09, resume note): the spray log and state live
# in memory. After a crash-resume the detector restarts from zero for that
# hunt and may not fire again. Consequence-free for the prize (spray never
# voids, only pauses for review) and low impact, so the log is not
# persisted — the detector covers hunts without a crash. If that ever
# matters, persist the (author, ref.id) pairs on the row.
def spray_check(orch, hunt, clue_index: int, log: list[tuple[str, str]],
                state: dict) -> None:
    """Puzzle phase only. `log` is the (author, label) list of wrong guesses
    the loop keeps; `state` remembers whether we already paused so the
    detector fires ONCE per hunt. Pause + notify — never a void."""
    ports: TargetPorts = orch._target
    if ports.spray is None or clue_index > PUZZLE_CLUES or state.get("fired"):
        return
    v = ports.spray.evaluate(log)
    if not v.triggered:
        return
    state["fired"] = True
    paused = False
    pause = getattr(orch._control, "pause", None)
    if pause is not None:
        try:
            pause()
            paused = True
        except Exception as e:  # noqa: BLE001
            orch._notify(f"spray: pause() failed ({e!r}) — pause manually.")
    orch._notify("🛑 " + v.render()
                 + (" — hunt PAUSED for review (/resume to continue)." if paused
                    else " — review now (pause was not available)."))


# --------------------------------------------------------------------------- #
# Reveal / resume                                                              #
# --------------------------------------------------------------------------- #


def reveal_text(orch, hunt, winner, receipt, *, time_to_win: str,
                prize_amount: str) -> str:
    sealed: SealedTarget = hunt.target
    return target_winner_announcement(TargetWinnerData(
        hunt_n=hunt.number, winner_handle=winner.submission.sender_handle,
        time_to_win=time_to_win, prize_amount=prize_amount,
        tx_link=receipt.tx_hash,
        target_name_onchain=sealed.target.name_onchain,
        target_id=sealed.id(), metadata_sha256=sealed.target.metadata_sha256,
        salt=sealed.salt, holder=winner.holder,
        non_holder_pct=orch._non_holder_pct,
        live_metadata_sha256=getattr(hunt, "target_live_hash", None),
        live_hash_status=getattr(hunt, "target_live_hash_status",
                                 LIVE_HASH_UNAVAILABLE),
        mutated_after_claim=bool(getattr(hunt, "target_pay_noted", False)),
        token_uri=sealed.target.token_uri,
        live_token_uri=getattr(hunt, "target_live_token_uri", None),
    ))


def resume_target_hunt(orch, row: dict, hunt):
    """Rehydrate the target side of a crash-resumed hunt from the sealed
    payloads. A row that cannot be unsealed is a hunt we do not serve."""
    ports: TargetPorts = orch._target
    sealed = ports.cipher.unseal(str(row["target_sealed"]))
    image_description = ""
    blob = row.get("target_ctx_sealed")
    if blob:
        try:
            doc = json.loads(ports.cipher._cipher.decrypt(str(blob)))  # noqa: SLF001
            image_description = str(doc.get("image_description") or "")
        except Exception:  # noqa: BLE001 — clues degrade (no art pieces), hunt survives
            orch._notify(f"hunt #{row.get('id')}: artwork description could not "
                         "be unsealed — art clues unavailable until fixed.")
    hunt.target = sealed
    hunt.ctx = TargetClueContext.from_target(sealed.target,
                                            image_description=image_description)
    hunt.persona = ReadyPersona(
        id=f"target-{hunt.number}", handle=target_label(hunt.number),
        x_user_id="", access_token="", access_secret="",
    )
    hunt.target_hold = HoldLedger(held=float(row.get("target_hold_s") or 0))
    # R8 applied to a defence (Opus, 09/09): the spray detector's log lives in
    # memory and restarts from zero on resume — say so, so the degradation is
    # visible to the operator instead of known only to whoever read the audit.
    if ports.spray is not None:
        orch._notify(
            f"hunt #{hunt.number}: resumed — the anti-spray detector restarted "
            "from zero (its log is in-memory); duplication before the crash is "
            "not counted. Watch the claim thread by hand for the rest of this hunt."
        )
    return hunt

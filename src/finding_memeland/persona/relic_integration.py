"""Relic ↔ Orchestrator integration.

Deliberate design: **almost all the new logic lives HERE, not in
state_machine.py**. The state machine gets a handful of tiny hunks (see
PATCH_state_machine.md) that delegate to these functions — so the 2478-line file
that runs live hunts and moves money changes as little as possible, and the relic
behaviour is auditable in one place.

The key trick that keeps the frozen protocol untouched (design 2026-08-22):
a relic hunt's synthetic `ReadyPersona.x_user_id` is the relic's **canonical id**
(`base:contract:tokenId`). So `compute_integrity_hash(x_user_id, code, salt)` —
unchanged, frozen — reproduces exactly the commitment burned in at mint, and the
reveal already publishes that field, which is precisely what the public needs to
verify WHICH relic was the target. No change to the hash, the reveal template, or
public verification.

Claim validation is NOT touched (Pedro, 2026-08-22): the code alone wins, exactly
as today — to know the code you must have found the relic.
"""

from __future__ import annotations

from ..orchestrator.ports import ReadyPersona

# A relic hunt has no X account. The handle is a NEUTRAL label used in operator
# notifications and internal logs; it must never carry the relic's name (blind
# mode). The reveal uses `relic_name` explicitly instead.
def relic_label(relic_id: str) -> str:
    return f"relic:{str(relic_id)[:8]}"


def prepare_relic_hunt(
    orch,
    prize_fmml: int,
    min_balance_fmml: int,
    *,
    ladder_exempt: bool = False,
):
    """The relic twin of `_prepare_predressed`. Returns a PreparedHunt.

    Order (mirrors the persona path's discipline):
      1. pick the oldest launchable relic + run the FAIL-CLOSED findability gate
         (refuses the launch if the canonical surface doesn't index it);
      2. build the commitment inputs from the identity (already committed at
         mint — never regenerated: the relic's description carries that code);
      3. create the hunt row (with relic_id + ladder_exempt);
      4. only then mark the relic in_play.
    """
    from ..orchestrator.state_machine import HuntState, PreparedHunt
    from ..content.relic_clues import RelicClueContext
    from ..persona.relic import RelicState
    from ..telegram.relic_launch import stage_relic_launch

    summary, _prompt, relic, identity = stage_relic_launch(
        pool=orch._relic_pool,
        prize_fmml=prize_fmml,
        ladder_exempt=ladder_exempt,
        canonical_findability=orch._relic_findability,
        secondary_findability=getattr(orch, "_relic_findability_secondary", ()),
    )

    canonical_id = relic.canonical_id()
    if not canonical_id or not relic.commitment:
        raise RuntimeError(
            f"relic {relic.id} is not properly minted (no canonical id/commitment) "
            "— refusing to launch"
        )

    # The commitment was computed AT MINT over (canonical_id + code + salt).
    # We reuse it verbatim: recomputing here would be a chance to diverge.
    claim_code = identity.claim_code
    salt = identity.salt
    integrity_hash = relic.commitment

    # Synthetic persona: the canonical id in x_user_id is what makes the frozen
    # integrity protocol produce/verify the relic commitment unchanged.
    persona = ReadyPersona(
        id=relic.id,
        handle=relic_label(relic.id),   # NEUTRAL — never the relic's name
        x_user_id=canonical_id,
        access_token="",                # a relic has no X account
        access_secret="",
    )

    ctx = RelicClueContext.from_identity(identity, backstory=identity.description)

    number = orch._next_number()
    prize_fmml = int(prize_fmml)
    prize_usd = orch._prize_usd_of(prize_fmml)
    started_at = orch._clock.now()

    base_fields = dict(
        persona_id=None,                 # no personas row for a relic hunt
        persona_display_name=None,       # BLIND: the name is not stored in the clear
        persona_bio=None,
        claim_code=claim_code,
        integrity_salt=salt,
        integrity_hash=integrity_hash,
        prize_usd=prize_usd,
        prize_fmml=prize_fmml,
        min_balance_fmml=min_balance_fmml,
        holding_hours=orch._holding_hours,
        started_at=started_at,
        state=HuntState.PREPARING.value,
    )
    hunt_id = orch._repo.create_hunt(
        **base_fields, hunt_number=number, relic_id=relic.id,
        ladder_exempt=bool(ladder_exempt),
    )

    # minted -> in_play only AFTER the row exists (same ordering rule as personas:
    # never consume the pool entry for a hunt that failed to be recorded).
    try:
        orch._relic_pool.set_state(relic.id, RelicState.IN_PLAY)
    except Exception as e:  # noqa: BLE001
        orch._notify(
            f"🚨 hunt row #{hunt_id} criada mas o relic {relic.id} NÃO ficou "
            f"in_play ({e!r}) — launch RECUSADO. Vê a BD e relança."
        )
        raise

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
        holding_hours=orch._holding_hours,
        state=HuntState.PREPARING,
        started_at=started_at,
        number=number,
        predressed=True,          # no prep window: the relic is already indexed
        relic=relic,
        relic_name=identity.name,  # in memory only — never in an operator message
    )

    age = summary.age_days()
    # BLIND: the operator sees structure, never the name.
    orch._notify(
        f"hunt #{number}: relic {relic.id} (blind) selecionado do pool"
        + (f", mintado há {age}d" if age is not None else "")
        + f", findability ✓ ({summary.findability_surface}). A lançar."
    )
    return hunt


def engine_for(orch, hunt):
    """The clue engine this hunt needs: the relic engine for relic hunts, the
    normal one otherwise. One helper so the two call sites stay one-liners."""
    if getattr(hunt, "relic", None) is not None and getattr(orch, "_relic_clue_engine", None):
        return orch._relic_clue_engine
    return orch._clue_engine


def deliver_trophy(orch, hunt, winner) -> None:
    """Send the relic to the winner AFTER the prize is paid. Never raises: the
    money is already out, so a trophy problem is a follow-up task, not a hunt
    failure (the transfer itself alerts with manual-send details)."""
    from ..chain.relic_trophy import transfer_trophy

    relic = getattr(hunt, "relic", None)
    port = getattr(orch, "_trophy_port", None)
    if relic is None or port is None:
        return
    try:
        res = transfer_trophy(
            relic=relic, to_wallet=winner.wallet, transfer_port=port,
            notifier=orch._notifier if hasattr(orch, "_notifier") else None,
        )
        if res.delivered:
            orch._notify(
                f"🏆 troféu entregue a {winner.submission.sender_handle}"
                + (f" (tx {res.tx_hash})" if res.tx_hash else " (já era dono)")
            )
    except Exception as e:  # noqa: BLE001 — already alerted inside; never break the flow
        orch._notify(f"⚠️ troféu não entregue (prémio JÁ pago): {e!r}")


def retire_relic(orch, hunt) -> None:
    """Close out a relic hunt: the relic becomes 'revealed' (its identity is now
    public). Nothing is undressed or destroyed — the relic IS the artifact."""
    from ..persona.relic import RelicState

    relic = getattr(hunt, "relic", None)
    if relic is None:
        return
    try:
        orch._relic_pool.set_state(relic.id, RelicState.REVEALED)
    except Exception as e:  # noqa: BLE001 — bookkeeping, never fatal at this point
        orch._notify(f"⚠️ relic {relic.id} não marcado como revealed: {e!r}")


def reveal_extra_line(hunt) -> str:
    """The on-chain proof line appended to the winner announcement (Pedro chose
    name + on-chain link): anyone can open the relic and recompute the
    commitment from the published user id (= canonical id), code and salt."""
    relic = getattr(hunt, "relic", None)
    name = getattr(hunt, "relic_name", None)
    if relic is None or not name:
        return ""
    return (
        f"\nthe relic: {name} — basescan.org/token/{relic.contract}"
        f"?a={relic.token_id}"
    )


def resume_relic_hunt(orch, row, hunt):
    """Rehydrate the relic side of a crash-resumed hunt: reload the relic and its
    identity from the pool by the row's relic_id. Returns the hunt (mutated) or
    the hunt untouched when the row isn't a relic hunt."""
    relic_id = row.get("relic_id")
    if not relic_id or getattr(orch, "_relic_pool", None) is None:
        return hunt
    relic = orch._relic_pool._repo.get_relic(str(relic_id))
    if relic is None:
        orch._notify(
            f"🚨 hunt #{row.get('id')} refere o relic {relic_id} mas ele não está "
            "na pool — resume sem contexto de relic; intervém."
        )
        return hunt
    identity = orch._relic_pool.reveal_identity(str(relic_id))
    from ..content.relic_clues import RelicClueContext

    hunt.relic = relic
    hunt.relic_name = identity.name
    hunt.identity = identity
    hunt.ctx = RelicClueContext.from_identity(identity, backstory=identity.description)
    hunt.persona = ReadyPersona(
        id=relic.id, handle=relic_label(relic.id),
        x_user_id=relic.canonical_id() or "", access_token="", access_secret="",
    )
    return hunt

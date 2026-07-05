from finding_memeland.content.integrity import verify_integrity_hash
from finding_memeland.orchestrator.simulation import FakeClueEngine, build_simulation
from finding_memeland.orchestrator.state_machine import HuntState


def _run():
    rig = build_simulation()
    hunt = rig.orchestrator.run_hunt()
    return rig, hunt


def test_full_hunt_reaches_done():
    rig, hunt = _run()
    assert hunt.state == HuntState.DONE


def test_winner_paid_once():
    rig, hunt = _run()
    assert len(rig.payout.sent) == 1
    assert rig.payout.sent[0]["amount"] == hunt.prize_fmml
    assert len(rig.repo.winners) == 1
    assert rig.repo.payouts[0]["tx_hash"].startswith("0xtx")


def test_persona_dressed_and_retired():
    rig, hunt = _run()
    assert rig.dresser.dressed
    assert rig.dresser.retired
    assert hunt.persona.id in rig.persona_source.retired


def test_integrity_hash_is_verifiable():
    rig, hunt = _run()
    # Anyone can recompute from the revealed ingredients.
    assert verify_integrity_hash(
        hunt.persona.x_user_id, hunt.claim_code, hunt.salt, hunt.integrity_hash
    )


def test_clue_one_and_winner_announcement_posted():
    rig, hunt = _run()
    blob = "\n".join(rig.publisher.posts).lower()
    assert "hunt #1 is live" in blob
    assert "winner" in blob
    assert hunt.integrity_hash in "\n".join(rig.publisher.posts)


def test_losing_submission_gets_canned_reply():
    rig, hunt = _run()
    # The scripted loser (wrong code) should have received a reply.
    assert any("code" in reply.lower() for _, reply in rig.publisher.dm_replies)
    # And every submission (loser + winner) is logged for the public audit.
    assert len(rig.repo.submissions) >= 2


def test_solution_terms_never_appear_in_posts():
    rig, hunt = _run()
    blob = "\n".join(rig.publisher.posts)
    for term in hunt.identity.solution_terms:
        assert term not in blob


class _GuardrailStuckClueEngine(FakeClueEngine):
    """Clue 1 works; every follow-up clue fails guardrails (live-test crash of
    2026-07-05: the model kept writing a forbidden name word)."""

    def next_clue(self, ctx, clue_index, prior_clues):
        if clue_index >= 2:
            raise RuntimeError(f"clue #{clue_index} failed guardrails after 4 attempts")
        return super().next_clue(ctx, clue_index, prior_clues)


def test_clue_guardrail_failure_skips_round_instead_of_killing_hunt():
    rig = build_simulation(clue_engine=_GuardrailStuckClueEngine())
    rig.orchestrator._clue_due_fn = lambda now: now  # a clue is due every round
    hunt = rig.orchestrator.run_hunt()
    # The hunt must survive the failed clues, keep reading DMs and finish.
    assert hunt.state == HuntState.DONE
    assert any("clue generation failed" in m for m in rig.notifier.messages)
    assert len(rig.payout.sent) == 1


class _FlakyOnceDMSource:
    """Raises on the first poll (X hiccup), then delegates to the real source."""

    def __init__(self, inner):
        self._inner = inner
        self._raised = False

    def poll(self, since):
        if not self._raised:
            self._raised = True
            raise ConnectionError("simulated X outage")
        return self._inner.poll(since)


def test_dm_poll_failure_does_not_kill_hunt():
    rig = build_simulation()
    rig.orchestrator._dm_source = _FlakyOnceDMSource(rig.orchestrator._dm_source)
    hunt = rig.orchestrator.run_hunt()
    assert hunt.state == HuntState.DONE
    assert any("DM poll failed" in m for m in rig.notifier.messages)
    assert len(rig.payout.sent) == 1


def test_resume_live_hunt_after_crash():
    # Process 1: hunt goes LIVE (persona dressed, clue 1 out)... then "crashes".
    rig1 = build_simulation()
    orch1 = rig1.orchestrator
    hunt1 = orch1._prepare(200)
    orch1._go_live(hunt1)
    assert rig1.repo.hunts[hunt1.id]["state"] == "live"

    # Process 2: fresh agent over the SAME database picks the hunt back up.
    rig2 = build_simulation(repo=rig1.repo)
    resumed = rig2.orchestrator.resume_hunts()
    assert resumed == 1
    assert any("RESUMED" in m for m in rig2.notifier.messages)
    # Same hunt (same id, same claim code) ran to completion and paid once.
    assert rig1.repo.hunts[hunt1.id]["state"] == "done"
    assert len(rig2.payout.sent) == 1
    assert rig1.repo.hunts[hunt1.id]["claim_code"] == hunt1.claim_code


def test_resume_never_touches_money_states():
    rig1 = build_simulation()
    hid = rig1.repo.create_hunt(
        persona_id="persona-1", claim_code="AB2CD3EF", integrity_salt="s",
        integrity_hash="h", prize_fmml=1000, min_balance_fmml=10,
        holding_hours=24, state="paying",
    )
    rig2 = build_simulation(repo=rig1.repo)
    rig2.orchestrator.resume_hunts()
    # Not resumed, not paid, loud alert instead.
    assert rig1.repo.hunts[hid]["state"] == "paying"
    assert len(rig2.payout.sent) == 0
    assert any("NOT auto-resuming" in m for m in rig2.notifier.messages)


def test_resume_pending_cleanup_just_retires():
    rig1 = build_simulation()
    hid = rig1.repo.create_hunt(
        persona_id="persona-1", claim_code="AB2CD3EF", integrity_salt="s",
        integrity_hash="h", prize_fmml=1000, min_balance_fmml=10,
        holding_hours=24, state="pending_cleanup",
    )
    rig2 = build_simulation(repo=rig1.repo)
    rig2.orchestrator.resume_hunts()
    assert rig1.repo.hunts[hid]["state"] == "done"
    assert rig2.dresser.retired
    assert len(rig2.payout.sent) == 0  # already paid before the crash


def test_resume_voids_stuck_preparing():
    rig1 = build_simulation()
    hid = rig1.repo.create_hunt(
        persona_id="persona-1", claim_code="AB2CD3EF", integrity_salt="s",
        integrity_hash="h", prize_fmml=1000, min_balance_fmml=10,
        holding_hours=24, state="preparing",
    )
    rig2 = build_simulation(repo=rig1.repo)
    rig2.orchestrator.resume_hunts()
    assert rig1.repo.hunts[hid]["state"] == "done"
    assert rig2.dresser.retired  # persona undressed
    assert len(rig2.payout.sent) == 0
    assert any("voiding" in m.lower() for m in rig2.notifier.messages)


def test_poisoned_submission_is_retried_then_skipped():
    from finding_memeland.orchestrator.ports import Submission

    rig = build_simulation()
    repo, clock = rig.repo, rig.clock

    class _ReplaySource:
        """Honours the since marker, so a not-yet-processed DM is re-served."""

        def poll(self, since):
            subs = [
                Submission(dm_id="1", sender_x_id="6660", sender_handle="poisoned",
                           body="code WRONGCOD wallet " + "0x" + "b" * 40,
                           created_at=clock.now()),
                Submission(dm_id="2", sender_x_id="9001", sender_handle="sharp_anon",
                           body=f"code {repo.latest_claim_code()} wallet " + "0x" + "a" * 40,
                           created_at=clock.now()),
            ]
            return [s for s in subs if since is None or int(s.dm_id) > int(since)]

    class _PoisonValidator:
        def validate(self, parsed, hunt):
            if parsed.sender_x_id == "6660":
                raise TimeoutError("simulated lookup failure")
            from finding_memeland.orchestrator.simulation import FakeValidator

            return FakeValidator().validate(parsed, hunt)

    rig.orchestrator._dm_source = _ReplaySource()
    rig.orchestrator._validator = _PoisonValidator()
    hunt = rig.orchestrator.run_hunt()
    # Poisoned DM was retried, then skipped; the winner behind it still won.
    assert hunt.state == HuntState.DONE
    assert any("SKIPPED after" in m for m in rig.notifier.messages)
    assert len(rig.payout.sent) == 1

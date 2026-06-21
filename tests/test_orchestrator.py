from finding_memeland.content.integrity import verify_integrity_hash
from finding_memeland.orchestrator.simulation import build_simulation
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

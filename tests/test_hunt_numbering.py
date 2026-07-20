"""Pack 2, item 1b (post-mortem P3.2) — one source of truth for "Hunt #N".

Before: templates said Hunt #1 forever (constructor default never overridden)
while resume notifications printed the DB id ("#2"). Now the number is
DB-derived at prepare (max+1), stored on the row, and reread on resume.
"""

from finding_memeland.orchestrator.simulation import build_simulation


def test_first_hunt_is_number_one():
    rig = build_simulation()
    hunt = rig.orchestrator.run_hunt()
    assert hunt.number == 1
    assert any("Hunt #1 is live" in p for p in rig.publisher.posts)


def test_second_hunt_increments_publicly():
    rig1 = build_simulation()
    rig1.orchestrator.run_hunt()

    rig2 = build_simulation(repo=rig1.repo)
    hunt2 = rig2.orchestrator.run_hunt()
    assert hunt2.number == 2
    posts = "\n".join(rig2.publisher.posts)
    assert "Hunt #2 is live" in posts
    assert "Hunt #2 closed" in posts
    # And the notifications agree — no second numbering scheme.
    assert any("hunt #2 LIVE" in m for m in rig2.notifier.messages)


def test_number_is_stored_on_the_row():
    rig = build_simulation()
    hunt = rig.orchestrator._prepare(200)
    assert rig.repo.hunts[hunt.id]["hunt_number"] == hunt.number


def test_resume_uses_the_stored_number_not_the_db_id():
    # Simulate the Genesis reality: db id != public number.
    rig1 = build_simulation()
    hunt1 = rig1.orchestrator._prepare(200)
    rig1.orchestrator._go_live(hunt1)
    rig1.repo.hunts[hunt1.id]["hunt_number"] = 7  # public number diverges from id

    rig2 = build_simulation(repo=rig1.repo)
    rig2.orchestrator.resume_hunts()
    assert any("hunt #7 RESUMED" in m for m in rig2.notifier.messages)
    # Winner announcement carries the same public number.
    assert any("Hunt #7 is halted" in p for p in rig2.publisher.posts)


def test_resume_falls_back_to_db_id_on_premigration_rows():
    rig1 = build_simulation()
    hid = rig1.repo.create_hunt(
        persona_id="persona-1", claim_code="AB2CD3EF", integrity_salt="s",
        integrity_hash="h", prize_fmml=1000, min_balance_fmml=10,
        holding_hours=24, state="pending_cleanup",  # no hunt_number column value
    )
    rig2 = build_simulation(repo=rig1.repo)
    rig2.orchestrator.resume_hunts()
    assert any(f"hunt #{hid} resumed at cleanup" in m for m in rig2.notifier.messages)


def test_numbering_query_failure_falls_back_and_notifies():
    rig = build_simulation()
    rig.repo.next_hunt_number = lambda: (_ for _ in ()).throw(TimeoutError("db"))
    hunt = rig.orchestrator._prepare(200)
    assert hunt.number == 1  # constructor fallback
    assert any("numbering query failed" in m for m in rig.notifier.messages)


def test_voided_hunt_consumes_its_number():
    rig1 = build_simulation(win_after_polls=10_000)
    rig1.orchestrator._hunt_timeout_h = 1
    hunt1 = rig1.orchestrator.run_hunt()
    assert hunt1.state.value == "done"  # voided path ends in done
    assert any("Hunt #1 ends unclaimed" in p for p in rig1.publisher.posts)

    rig2 = build_simulation(repo=rig1.repo)
    hunt2 = rig2.orchestrator.run_hunt()
    assert hunt2.number == 2  # the public already saw #1; never reuse it

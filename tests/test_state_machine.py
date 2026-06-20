from finding_memeland.orchestrator.state_machine import HuntState, can_transition


def test_happy_path_transitions():
    path = [
        HuntState.IDLE, HuntState.PREPARING, HuntState.LIVE, HuntState.RESOLVING,
        HuntState.PAYING, HuntState.PENDING_CLEANUP, HuntState.RETIRING, HuntState.DONE,
    ]
    for src, dst in zip(path, path[1:]):
        assert can_transition(src, dst), f"{src}->{dst}"


def test_illegal_transition_blocked():
    assert not can_transition(HuntState.IDLE, HuntState.PAYING)
    assert not can_transition(HuntState.LIVE, HuntState.DONE)


def test_void_path_exists():
    assert can_transition(HuntState.LIVE, HuntState.VOIDED)
    assert can_transition(HuntState.VOIDED, HuntState.RETIRING)

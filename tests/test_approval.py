from finding_memeland.telegram.approval_queue import ApprovalQueue, route_command


# --- command routing + auth (actions take the arg string) ---
def _actions(log):
    return {
        "launch": lambda arg="": log.append(("launch", arg)) or f"launching {arg}",
        "silence": lambda arg="": log.append(("silence", arg)) or "paused",
        "resume": lambda arg="": "resumed",
        "status": lambda arg="": "idle",
    }


def test_non_admin_blocked():
    log = []
    assert route_command("/launch 200", is_admin=False, actions=_actions(log)) == "unauthorized"
    assert log == []


def test_admin_launch_passes_argument():
    log = []
    out = route_command("/launch 250", is_admin=True, actions=_actions(log))
    assert out == "launching 250"
    assert log == [("launch", "250")]


def test_command_without_arg_gives_empty_arg():
    log = []
    route_command("/silence", is_admin=True, actions=_actions(log))
    assert log == [("silence", "")]


def test_unknown_command():
    out = route_command("/frobnicate", is_admin=True, actions=_actions([]))
    assert "unknown command" in out


# --- approval queue ---
class _Repo:
    def __init__(self):
        self.rows = {}
        self._n = 0
        self.status = {}

    def create_approval(self, *, kind, draft_text, telegram_msg_id=None):
        self._n += 1
        self.rows[self._n] = {"id": self._n, "kind": kind, "draft_text": draft_text}
        return self._n

    def get_approval(self, approval_id):
        return self.rows.get(approval_id)

    def set_approval_status(self, approval_id, status):
        self.status[approval_id] = status


class _Pub:
    def __init__(self):
        self.posts = []

    def post(self, text, **kw):
        self.posts.append(text)
        return "t1"


def test_game_post_cannot_be_queued():
    q = ApprovalQueue(repo=_Repo(), publisher=_Pub())
    for kind in ("clue_one", "clue", "winner_announcement"):
        try:
            q.submit_for_approval(kind=kind, draft_text="x")
        except ValueError:
            continue
        raise AssertionError(f"{kind} should be rejected from the queue")


def test_non_game_post_queues():
    repo = _Repo()
    q = ApprovalQueue(repo=repo, publisher=_Pub())
    qid = q.submit_for_approval(kind="filler", draft_text="gm frens")
    assert repo.rows[qid]["draft_text"] == "gm frens"


def test_approve_publishes():
    repo, pub = _Repo(), _Pub()
    q = ApprovalQueue(repo=repo, publisher=pub)
    qid = q.submit_for_approval(kind="filler", draft_text="hello")
    assert q.decide(qid, "approve") == "published"
    assert pub.posts == ["hello"]
    assert repo.status[qid] == "approved"


def test_approve_with_edit_publishes_edited():
    repo, pub = _Repo(), _Pub()
    q = ApprovalQueue(repo=repo, publisher=pub)
    qid = q.submit_for_approval(kind="comms", draft_text="orig")
    q.decide(qid, "approve", edited_text="edited!")
    assert pub.posts == ["edited!"]


def test_reject_does_not_publish():
    repo, pub = _Repo(), _Pub()
    q = ApprovalQueue(repo=repo, publisher=pub)
    qid = q.submit_for_approval(kind="filler", draft_text="nope")
    assert q.decide(qid, "reject") == "rejected"
    assert pub.posts == []
    assert repo.status[qid] == "rejected"

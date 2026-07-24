"""Hunt #2 P0 — follow-up ingestion via per-conversation reads.

Production proof ([dm-poll] logs, 23/07): the account-level dm_events endpoint
STOPS delivering inbound events of a conversation after the main account
replies in it (raw=50 frozen; returned=0 through the whole correction window).
These tests run the REAL loop + REAL XClient/XDMSource against a fake tweepy
client that reproduces exactly that suppression — and prove the two-stream
design (discovery + per-conversation round-robin) reads everything anyway.
"""

from datetime import timedelta
from types import SimpleNamespace

from finding_memeland.dm.listener import XDMSource
from finding_memeland.orchestrator.simulation import build_simulation
from finding_memeland.social.x_client import XClient

MAIN_ID = "999"
WALLET_A = "0x" + "a" * 40
WALLET_B = "0x" + "b" * 40


class SuppressedStream:
    """The X side: account-level view suppresses inbound events of replied
    conversations; the per-conversation endpoint always sees everything."""

    def __init__(self, t0):
        self.t0 = t0
        self.events = []
        self._next_id = 1000

    def add_in(self, sender, text, minute):
        self._next_id += 10
        self.events.append(SimpleNamespace(
            id=self._next_id, sender_id=sender, text=text,
            created_at=self.t0 + timedelta(minutes=minute),
            event_type="MessageCreate", conv=sender))

    def add_out(self, recipient, text, minute):
        self._next_id += 10
        self.events.append(SimpleNamespace(
            id=self._next_id, sender_id=MAIN_ID, text=text,
            created_at=self.t0 + timedelta(minutes=minute),
            event_type="MessageCreate", conv=recipient))

    def account_view(self):
        out = []
        for e in self.events:
            if str(e.sender_id) != MAIN_ID and any(
                r.conv == e.conv and str(r.sender_id) == MAIN_ID and r.id < e.id
                for r in self.events
            ):
                continue  # SUPPRESSED: inbound after our reply
            out.append(e)
        return sorted(out, key=lambda e: e.id, reverse=True)

    def conversation_view(self, participant_id):
        evs = [e for e in self.events if e.conv == participant_id]
        return sorted(evs, key=lambda e: e.id, reverse=True)


class FakeV2:
    def __init__(self, stream):
        self._s = stream
        self.conv_calls: list[str] = []

    def get_me(self, **_):
        return SimpleNamespace(data=SimpleNamespace(id=int(MAIN_ID)))

    def get_direct_message_events(self, **kw):
        pid = kw.get("participant_id")
        if pid is not None:
            self.conv_calls.append(str(pid))
            evs = self._s.conversation_view(str(pid))
        else:
            evs = self._s.account_view()
        evs = evs[: kw.get("max_results", 50)]
        users = {e.sender_id: SimpleNamespace(id=e.sender_id, username=f"u{e.sender_id}")
                 for e in evs}
        return SimpleNamespace(data=evs, includes={"users": list(users.values())}, meta={})


def _rig_with_stream(schedule):
    """Real orchestrator + real XClient/XDMSource over the suppressed fake API.
    `schedule` maps poll-cycle number -> list of (sender, text, minute)."""
    rig = build_simulation(poll_interval_s=60)
    orch = rig.orchestrator
    hunt = orch._prepare(200)
    orch._go_live(hunt)
    stream = SuppressedStream(hunt.started_at)
    fake = FakeV2(stream)
    xc = XClient(api_key="k", api_secret="s")
    xc._client = fake
    inner = XDMSource(xc)

    class Replying:
        def __init__(self, p): self._p = p
        def post(self, text, **kw): return self._p.post(text, **kw)
        def reply_dm(self, rid, text):
            self._p.reply_dm(rid, text)
            stream.add_out(rid, text, (rig.clock.now() - stream.t0).total_seconds() / 60)
    orch._publisher = Replying(rig.publisher)

    calls = {"n": 0, "conv_per_cycle": []}

    class Source:
        def poll(self, since):
            calls["n"] += 1
            calls["conv_per_cycle"].append(0)
            for sender, text, minute in schedule.get(calls["n"], []):
                stream.add_in(sender, text, minute)
            return inner.poll(since)

        def poll_conversation(self, pid, since):
            calls["conv_per_cycle"][-1] += 1
            return inner.poll_conversation(pid, since)

    orch._dm_source = Source()
    orch._max_rounds = 60
    return rig, orch, hunt, fake, calls


def _code(rig):
    return rig.repo.latest_claim_code()


def test_followup_after_reply_is_read_and_wins():
    """The Bashit419 case: wrong code+wallet, agent replies (conversation now
    suppressed account-level), correct code follows — and WINS via the
    per-conversation read + assembler."""
    schedule = {
        1: [("2038", f"misanthrope comme decepticon WRONGCOD {WALLET_B}", 1)],
        4: [("2038", "PLACEHOLDER", 4)],
    }
    rig, orch, hunt, fake, calls = _rig_with_stream(schedule)
    schedule[4] = [("2038", f"ok then: {_code(rig)}", 4)]
    winner = orch._clue_and_dm_loop(hunt)
    assert winner is not None
    assert winner.submission.sender_x_id == "2038"
    outcomes = [(s["sender_x_id"], s["outcome"]) for s in rig.repo.submissions]
    assert ("2038", "bad_code") in outcomes
    assert ("2038", "won") in outcomes
    assert fake.conv_calls, "the win must come through the per-conversation endpoint"


def test_koyfiesa_case_wallet_and_code_split_across_messages():
    """Wallet in one message, code in the next (after the agent's reply):
    the pair completes and wins."""
    schedule = {
        1: [("7001", f"found you! my wallet is {WALLET_A}", 1)],
        4: [("7001", "PLACEHOLDER", 4)],
    }
    rig, orch, hunt, fake, calls = _rig_with_stream(schedule)
    schedule[4] = [("7001", f"and the code: {_code(rig)}", 4)]
    winner = orch._clue_and_dm_loop(hunt)
    assert winner is not None
    assert winner.submission.sender_x_id == "7001"
    assert winner.wallet == WALLET_A
    # The wallet-only message was logged (audit) and answered with "need code".
    outcomes = [s["outcome"] for s in rig.repo.submissions if s["sender_x_id"] == "7001"]
    assert "partial" in outcomes
    replies = [t for _, t in rig.publisher.dm_replies]
    assert any("claim code" in t for t in replies)


def test_settlement_earlier_completion_beats_later_clean_thread():
    """THE Hunt #2 injustice, fixed: a clean single message (processed first)
    must NOT beat a follow-up completion with an earlier created_at."""
    schedule = {
        1: [("2038", f"try WRONGCOD {WALLET_B}", 1)],
        # cycle 3: the follow-up ARRIVES (minute 3) but account-level is
        # suppressed — only the conversation round-robin will see it...
        3: [("2038", "PLACEHOLDER", 3)],
        # ...while a clean thread arrives LATER (minute 5) and is discovered
        # immediately at cycle 4.
        4: [("1656", "PLACEHOLDER2", 5)],
    }
    rig, orch, hunt, fake, calls = _rig_with_stream(schedule)
    schedule[3] = [("2038", f"ok: {_code(rig)}", 3)]
    schedule[4] = [("1656", f"code {_code(rig)} wallet {WALLET_A}", 5)]
    winner = orch._clue_and_dm_loop(hunt)
    assert winner is not None
    assert winner.submission.sender_x_id == "2038", (
        "completion at minute 3 must beat the clean thread of minute 5, "
        "whatever the processing order"
    )
    assert any("settlement" in m or "win candidate" in m for m in rig.notifier.messages)


def test_rate_budget_one_conversation_read_per_cycle():
    """Never more than ONE per-conversation request per poll cycle — the
    endpoint's 15/15min bucket is shared across all participant_ids."""
    schedule = {
        1: [("a1", "hello WRONGCOD", 1), ("a2", "hey there", 1),
            ("a3", "sup", 1), ("a4", "yo", 1)],
        6: [("a9", "PLACEHOLDER", 6)],
    }
    rig, orch, hunt, fake, calls = _rig_with_stream(schedule)
    schedule[6] = [("a9", f"code {_code(rig)} wallet {WALLET_A}", 6)]
    orch._clue_and_dm_loop(hunt)
    assert calls["conv_per_cycle"], "loop ran"
    assert max(calls["conv_per_cycle"]) <= 1


def test_streams_are_deduped_one_row_per_message():
    """A message visible to BOTH streams (first message of a new conversation)
    is processed exactly once."""
    schedule = {1: [("5005", "just one message WRONGCOD no wallet", 1)]}
    rig, orch, hunt, fake, calls = _rig_with_stream(schedule)
    orch._max_rounds = 6
    try:
        orch._clue_and_dm_loop(hunt)
    except RuntimeError:
        pass  # max rounds, no winner — fine
    rows = [s for s in rig.repo.submissions if s["sender_x_id"] == "5005"]
    assert len(rows) == 1


def test_resume_rebuilds_conversation_markers_and_assembler():
    """After a restart, the log rebuilds per-conversation markers AND the
    assembler's partial state: a pre-crash wallet + post-crash code still win."""
    schedule = {1: [("7001", f"wallet first {WALLET_A}", 1)]}
    rig, orch, hunt, fake, calls = _rig_with_stream(schedule)
    orch._max_rounds = 3
    try:
        orch._clue_and_dm_loop(hunt)
    except RuntimeError:
        pass
    assert any(s["outcome"] == "partial" for s in rig.repo.submissions)

    # "Restart": fresh loop over the same repo/stream (the schedule counter
    # keeps advancing — cycle 6 lands inside the second run); the code arrives
    # only after the "reboot".
    schedule[6] = [("7001", f"the code! {_code(rig)}", 8)]
    orch._max_rounds = 60
    winner = orch._clue_and_dm_loop(hunt, since=None)
    assert winner is not None
    assert winner.submission.sender_x_id == "7001"
    # The pre-crash partial row was NOT re-processed (dedupe from the log).
    partials = [s for s in rig.repo.submissions if s["outcome"] == "partial"]
    assert len(partials) == 1

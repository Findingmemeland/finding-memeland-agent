"""Claim-by-post — Pedro's ruleset (2026-07-25), encoded as tests.

The DM API only reads virgin conversations, so submissions moved to PUBLIC
replies on the Clue 1 post. These tests run the REAL claim loop against a fake
claim source and prove every rule: single claim window, timestamp ordering,
eliminatory reshare, wallet-from-same-account-only, the 10-minute lapse that
reopens the hunt, the anti-spam caps, and clue-free replies.
"""

from datetime import timedelta

from finding_memeland.claims.parser import ClaimPost, code_like, extract_candidates
from finding_memeland.claims.taunts import TAUNT_POOL, TauntEngine
from finding_memeland.content.templates import (
    POST_REPLY_MISSING_REPOST,
    POST_REPLY_TIMED_OUT,
    POST_REPLY_WRONG_DOOR,
)
from finding_memeland.orchestrator.simulation import build_simulation
from finding_memeland.orchestrator.state_machine import HuntState

WALLET_A = "0x" + "a" * 40
WALLET_B = "0x" + "b" * 40
BAD_CHECKSUM = "0x" + "Aa" * 20  # mixed case, wrong EIP-55 checksum


class FakeClaimSource:
    """Scripted mentions stream + thread sweep + reshare/profile lookups.

    `schedule` maps poll-cycle number -> callable returning ClaimPosts (a
    callable so a late entry can read state produced mid-run, e.g. the ask
    tweet id the orchestrator persisted on the hunt row)."""

    def __init__(self):
        self.schedule: dict[int, object] = {}
        self.thread_extra: list[ClaimPost] = []   # only visible to sweep()
        self.reshared: set[str] = set()
        self.profiles: dict[str, dict] = {}
        self.polls = 0
        self._delivered: list[ClaimPost] = []

    def poll(self, since):
        self.polls += 1
        entry = self.schedule.get(self.polls)
        if entry is not None:
            self._delivered += list(entry() if callable(entry) else entry)
        if since is None:
            return list(self._delivered)
        return [p for p in self._delivered if int(p.tweet_id) > int(since)]

    def sweep(self, conversation_id, since):
        return list(self.thread_extra)

    def has_reshared(self, user_id, post_id):
        return user_id in self.reshared

    def lookup_profile(self, user_id):
        return self.profiles.get(
            user_id, {"name": "Some One", "handle": f"u{user_id}", "bio": "gm"}
        )


def _rig(**kw):
    rig = build_simulation(poll_interval_s=60, **kw)
    orch = rig.orchestrator
    src = FakeClaimSource()
    orch._claim_source = src
    orch._taunt_engine = TauntEngine()          # no LLM: pool taunts, chatter silent
    hunt = orch._prepare(200)
    rig.clock.sleep(600)                        # a real gap: prep window then T0
    orch._go_live(hunt)
    return rig, orch, hunt, src


def _post(tid, author, text, at, reply_to, handle=None):
    return ClaimPost(
        tweet_id=str(tid), author_id=str(author),
        author_handle=handle or f"u{author}", text=text, created_at=at,
        conversation_id=None, replied_to_id=reply_to,
    )


def _code(rig):
    return rig.repo.latest_claim_code()


def _ask_id(rig, hunt):
    return rig.repo.hunts[hunt.id].get("pending_ask_tweet_id")


def _replies_to(rig, tid):
    return [t for r, t in rig.publisher.post_replies if r == str(tid)]


# ---------------------------------------------------------------------------
def test_happy_path_code_then_wallet_from_same_account_wins():
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared.add("42")
    src.schedule[1] = lambda: [
        _post(1010, "42", f"found it: {_code(rig)}", t0 + timedelta(minutes=1),
              hunt.reshare_post_id, handle="sharp_anon")
    ]
    src.schedule[5] = lambda: [
        _post(1050, "42", f"here you go {WALLET_A}", t0 + timedelta(minutes=5),
              _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner is not None
    assert winner.submission.sender_x_id == "42"
    assert winner.wallet == WALLET_A
    # The claim post's OWN timestamp is the winning timestamp, not the wallet's.
    assert winner.submission.created_at == t0 + timedelta(minutes=1)
    # The public ask went out as a reply to the winning post.
    asks = _replies_to(rig, 1010)
    assert asks and "you found me" in asks[0] and "10 minutes" in asks[0]
    # The claim row resolved to 'won' and carries the wallet.
    won = [s for s in rig.repo.submissions if s["outcome"] == "won"]
    assert len(won) == 1 and won[0]["dm_id"] == "1010" and won[0]["wallet"] == WALLET_A
    # WAIT_WALLET state cleared on the row (DB doctrine).
    assert rig.repo.hunts[hunt.id]["pending_winner_x_id"] is None


def test_full_hunt_pays_through_run_hunt():
    rig = build_simulation(poll_interval_s=60)
    orch = rig.orchestrator
    src = FakeClaimSource()
    orch._claim_source = src
    orch._taunt_engine = TauntEngine()
    src.reshared.add("9001")
    state = {}

    def first(rig=rig):
        code = rig.repo.latest_claim_code()
        hunt_row = max(rig.repo.hunts.values(), key=lambda r: r["id"])
        state["clue1"] = hunt_row["reshare_post_id"]
        return [_post(2010, "9001", f"code {code}", rig.clock.now(), state["clue1"])]

    def wallet(rig=rig):
        row = max(rig.repo.hunts.values(), key=lambda r: r["id"])
        return [_post(2050, "9001", WALLET_A, rig.clock.now(),
                      row.get("pending_ask_tweet_id"))]

    src.schedule[1] = first
    src.schedule[5] = wallet
    hunt = orch.run_hunt()
    assert hunt.state == HuntState.DONE
    assert len(rig.payout.sent) == 1
    assert rig.payout.sent[0]["wallet"] == WALLET_A
    reveal = next(p for p in rig.publisher.posts if "We have a winner" in p)
    # The integrity reveal MUST survive the channel change: user_id, claim
    # code and salt let anyone recompute the hash published in Clue 1.
    assert hunt.persona.x_user_id in reveal
    assert hunt.claim_code in reveal
    assert hunt.salt in reveal
    assert "Integrity check" in reveal


def test_settlement_earlier_post_beats_later_processed_first():
    """THE anti-sniping rule: created_at (public, auditable) decides — not our
    processing order. A copier's later post processed first must lose."""
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared |= {"7", "8"}
    # Cycle 1 delivers the COPIER's post (minute 5). The original poster's
    # EARLIER post (minute 3) surfaces late, during the settlement window.
    src.schedule[1] = lambda: [
        _post(3020, "8", f"mine! {_code(rig)}", t0 + timedelta(minutes=5),
              hunt.reshare_post_id, handle="sniper")
    ]
    # The original's post has a SMALLER id than the marker by the time it shows
    # up — only the markerless settlement sweep can find it.
    src.thread_extra = [
        _post(3010, "7", f"found: {_code(rig)}", t0 + timedelta(minutes=3),
              hunt.reshare_post_id, handle="original")
    ]
    src.schedule[8] = lambda: [
        _post(3050, "7", WALLET_A, t0 + timedelta(minutes=9), _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner.submission.sender_x_id == "7", "earlier created_at must win"
    # The sniper's row ended 'late' and got the late reply.
    outcomes = {s["dm_id"]: s["outcome"] for s in rig.repo.submissions}
    assert outcomes["3020"] == "late"
    assert any("in line" in t for t in _replies_to(rig, 3020))


def test_missing_repost_is_eliminatory_then_reposting_wins():
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    code = _code(rig)
    # No reshare at claim time -> invalid, told once. Then the player reposts
    # and posts the code AGAIN (new timestamp) -> wins.
    src.schedule[1] = lambda: [
        _post(4010, "55", f"code: {code}", t0 + timedelta(minutes=1),
              hunt.reshare_post_id)
    ]

    def fixed(src=src):
        src.reshared.add("55")
        return [_post(4020, "55", f"ok reposted, {code}",
                      rig.clock.now(), hunt.reshare_post_id)]

    src.schedule[3] = fixed
    src.schedule[9] = lambda: [
        _post(4050, "55", WALLET_A, rig.clock.now(), _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner.submission.sender_x_id == "55"
    assert winner.submission.dm_id == "4020", "the SECOND post is the claim"
    outcomes = {s["dm_id"]: s["outcome"] for s in rig.repo.submissions}
    assert outcomes["4010"] == "no_reshare"
    assert _replies_to(rig, 4010) == [POST_REPLY_MISSING_REPOST]


def test_wallet_only_accepted_from_the_claiming_account():
    """The prize-theft hole, closed: anyone can reply to the public ask with
    a wallet — only the author_id that posted the winning code counts."""
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared.add("42")
    src.schedule[1] = lambda: [
        _post(5010, "42", f"gotcha {_code(rig)}", t0 + timedelta(minutes=1),
              hunt.reshare_post_id)
    ]
    # A thief answers the ask FIRST, then the real winner.
    src.schedule[5] = lambda: [
        _post(5040, "666", f"send it here {WALLET_B}", rig.clock.now(),
              _ask_id(rig, hunt), handle="thief"),
        _post(5050, "42", WALLET_A, rig.clock.now(), _ask_id(rig, hunt)),
    ]
    winner = orch._claim_loop(hunt)
    assert winner.wallet == WALLET_A
    assert winner.submission.sender_x_id == "42"
    assert not _replies_to(rig, 5040), "the thief gets nothing, not even a reply"


def test_invalid_checksum_wallet_gets_one_correction_then_wins():
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared.add("42")
    src.schedule[1] = lambda: [
        _post(6010, "42", f"code {_code(rig)}", t0 + timedelta(minutes=1),
              hunt.reshare_post_id)
    ]
    src.schedule[5] = lambda: [
        _post(6040, "42", f"wallet: {BAD_CHECKSUM}", rig.clock.now(),
              _ask_id(rig, hunt))
    ]
    src.schedule[7] = lambda: [
        _post(6050, "42", WALLET_A, rig.clock.now(), _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner.wallet == WALLET_A
    fixes = _replies_to(rig, 6040)
    assert fixes and "doesn't check out" in fixes[0]


def test_wallet_timeout_reopens_and_queued_claim_is_promoted():
    """10 minutes from OUR ask, no wallet: public 'submission timed out'
    reply, claim lapses, and the next valid claim (queued during the wait)
    gets the ask — the hunt only ever closes by paying someone."""
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared |= {"42", "77"}
    src.schedule[1] = lambda: [
        _post(7010, "42", f"code {_code(rig)}", t0 + timedelta(minutes=1),
              hunt.reshare_post_id, handle="ghost")
    ]
    # While 42 is being waited on, 77 posts the (public) code -> queued.
    src.schedule[6] = lambda: [
        _post(7020, "77", f"i'll take it {_code(rig)}", rig.clock.now(),
              hunt.reshare_post_id, handle="vulture")
    ]
    # 42 never answers. After promotion, 77 sends the wallet.
    src.schedule[18] = lambda: [
        _post(7050, "77", WALLET_B, rig.clock.now(), _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner.submission.sender_x_id == "77"
    assert winner.wallet == WALLET_B
    outcomes = {s["dm_id"]: s["outcome"] for s in rig.repo.submissions}
    assert outcomes["7010"] == "timed_out"
    # The timeout was announced publicly, replying to OUR ask.
    timed = [t for _, t in rig.publisher.post_replies if t == POST_REPLY_TIMED_OUT]
    assert len(timed) == 1


def test_guess_cap_five_then_ignored_and_single_taunt():
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    wrongs = [
        _post(8000 + i, "13", f"guess AB{i}DEF{i}X", t0 + timedelta(minutes=i),
              hunt.reshare_post_id)
        for i in range(1, 8)  # 7 code-like guesses
    ]
    src.schedule[1] = lambda: wrongs
    orch._max_rounds = 6
    try:
        orch._claim_loop(hunt)
    except RuntimeError:
        pass  # no winner — expected
    rows = [s for s in rig.repo.submissions if s["sender_x_id"] == "13"]
    outcomes = [r["outcome"] for r in rows]
    assert outcomes.count("bad_code") == 5
    assert outcomes.count("spam_capped") == 2
    # Exactly ONE taunt for the whole profile, and it's from the safe pool.
    taunts = [t for r, t in rig.publisher.post_replies]
    assert len(taunts) == 1 and taunts[0] in TAUNT_POOL


def test_wrong_door_gets_one_redirect_and_is_not_a_claim():
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared.add("21")
    code = _code(rig)
    src.schedule[1] = lambda: [
        _post(9010, "21", f"code {code}", t0 + timedelta(minutes=1), "tweet-999"),
        _post(9011, "21", f"hello?? {code}", t0 + timedelta(minutes=2), "tweet-999"),
    ]
    src.schedule[3] = lambda: [
        _post(9020, "21", f"ah here: {code}", t0 + timedelta(minutes=4),
              hunt.reshare_post_id)
    ]
    src.schedule[9] = lambda: [
        _post(9050, "21", WALLET_A, rig.clock.now(), _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner.submission.dm_id == "9020"
    outcomes = [s["outcome"] for s in rig.repo.submissions if s["dm_id"] in ("9010", "9011")]
    assert outcomes == ["wrong_door", "wrong_door"]
    # ONE redirect for the profile, not two.
    redirects = [t for _, t in rig.publisher.post_replies if t == POST_REPLY_WRONG_DOOR]
    assert len(redirects) == 1


def test_prep_window_code_is_early_and_never_wins():
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared |= {"4242", "9001"}
    early_at = t0 - timedelta(seconds=30)  # before Clue 1
    src.schedule[1] = lambda: [
        _post(9910, "4242", f"psst {_code(rig)}", early_at, hunt.reshare_post_id)
    ]
    src.schedule[3] = lambda: [
        _post(9920, "9001", f"code {_code(rig)}", t0 + timedelta(minutes=2),
              hunt.reshare_post_id)
    ]
    src.schedule[9] = lambda: [
        _post(9950, "9001", WALLET_A, rig.clock.now(), _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner.submission.sender_x_id == "9001"
    rows = {s["dm_id"]: s["outcome"] for s in rig.repo.submissions}
    assert rows["9910"] == "early"
    assert any("hasn't started" in t for t in _replies_to(rig, 9910))


def test_chatter_gets_silence_and_thread_sweep_catches_strays():
    """'Good morning' never gets a reply (fail-closed funny judge). A stray
    reply-to-a-reply carrying the code — invisible to mentions — is still SEEN
    (via the periodic conversation sweep) and redirected: claims only count as
    direct replies to Clue 1 (Pedro's single-window rule)."""
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared |= {"31", "77"}
    src.schedule[1] = lambda: [
        _post(9810, "99", "Good morning frens", t0 + timedelta(minutes=1),
              hunt.reshare_post_id)
    ]
    # The stray lives ONLY in the thread (sweep-visible), replying to a player.
    src.thread_extra = [
        _post(9820, "31", f"try {_code(rig)}", t0 + timedelta(minutes=2), "1010")
    ]
    # A proper claim later closes the hunt.
    src.schedule[7] = lambda: [
        _post(9830, "77", f"code {_code(rig)}", rig.clock.now(),
              hunt.reshare_post_id)
    ]
    src.schedule[13] = lambda: [
        _post(9850, "77", WALLET_A, rig.clock.now(), _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner.submission.sender_x_id == "77"
    # Chatter: zero replies. Stray: exactly the wrong-door redirect.
    assert not _replies_to(rig, 9810)
    assert _replies_to(rig, 9820) == [POST_REPLY_WRONG_DOOR]
    outcomes = {s["dm_id"]: s["outcome"] for s in rig.repo.submissions}
    assert outcomes["9820"] == "wrong_door"


def test_resume_mid_wallet_wait_survives_restart():
    """Crash after the public ask: the WAIT_WALLET state is on the hunt row;
    a fresh process resumes it and the winner's wallet still lands."""
    rig1 = build_simulation(poll_interval_s=60)
    orch1 = rig1.orchestrator
    src1 = FakeClaimSource()
    orch1._claim_source = src1
    orch1._taunt_engine = TauntEngine()
    hunt1 = orch1._prepare(200)
    orch1._go_live(hunt1)
    src1.reshared.add("42")
    src1.schedule[1] = lambda: [
        _post(9710, "42", f"code {_code(rig1)}", hunt1.live_at + timedelta(minutes=1),
              hunt1.reshare_post_id, handle="patient_one")
    ]
    orch1._max_rounds = 5  # dies after the ask, before any wallet arrives
    try:
        orch1._claim_loop(hunt1)
    except RuntimeError:
        pass
    row = rig1.repo.hunts[hunt1.id]
    assert row["pending_winner_x_id"] == "42", "WAIT_WALLET persisted on the row"
    ask_id = row["pending_ask_tweet_id"]

    # "Restart": same repo, fresh rig/orchestrator/claim source.
    rig2 = build_simulation(repo=rig1.repo, poll_interval_s=60)
    orch2 = rig2.orchestrator
    src2 = FakeClaimSource()
    orch2._claim_source = src2
    orch2._taunt_engine = TauntEngine()
    src2.reshared.add("42")
    src2.schedule[2] = lambda: [
        _post(9750, "42", WALLET_A, rig2.clock.now(), ask_id)
    ]
    resumed = orch2.resume_hunts()
    assert resumed == 1
    assert rig1.repo.hunts[hunt1.id]["state"] == "done"
    assert len(rig2.payout.sent) == 1
    assert rig2.payout.sent[0]["wallet"] == WALLET_A


# ---------------------------------------------------------------------------
# Units: parser + taunt engine safety
# ---------------------------------------------------------------------------
def test_code_like_is_strict_but_matching_is_generous():
    assert code_like("try AB2DEF7X", 8)
    # An ordinary 8-letter word must NOT trigger replies (it's not in the
    # claim-code alphabet as typed) — otherwise every "birthday" mention
    # would get a taunt.
    assert not code_like("happy birthday fren", 8)
    assert not code_like("wallet 0x" + "a" * 40, 8)
    # Matching stays generous: a lowercase-typed code still matches and wins.
    assert "AB2DEF7X" in extract_candidates("i think it's ab2def7x", 8)


def test_taunt_engine_never_leaks_banned_terms():
    class LeakyLLM:
        class messages:
            @staticmethod
            def create(**kw):
                class B:
                    type = "text"
                    text = "lol wrong, it's obviously Amerigo Vespucci"
                return type("R", (), {"content": [B()]})()

    eng = TauntEngine(LeakyLLM(), "m")
    out = eng.taunt("is it WRONGCOD?", banned_terms=("Amerigo", "Vespucci"))
    assert "amerigo" not in out.lower() and "vespucci" not in out.lower()
    assert out in TAUNT_POOL, "leaky variation must fall back to the safe pool"


def test_taunt_engine_chatter_judge_fails_closed():
    assert TauntEngine().should_taunt_chatter("Good morning!") is False

    class DownLLM:
        class messages:
            @staticmethod
            def create(**kw):
                raise ConnectionError("api down")

    assert TauntEngine(DownLLM(), "m").should_taunt_chatter("gm") is False


def test_taunt_pool_is_clue_free_and_url_free():
    for t in TAUNT_POOL:
        assert "http" not in t and "@" not in t
        assert len(t) <= 240


def test_restart_during_settlement_recovers_the_open_claim():
    """Crash BEFORE the ask (candidacy open, row 'pending'): the rebuilt loop
    must recover the claim from the log — otherwise the resume marker skips it
    forever and a LATER claimant would win."""
    rig1 = build_simulation(poll_interval_s=60)
    orch1 = rig1.orchestrator
    src1 = FakeClaimSource()
    orch1._claim_source = src1
    orch1._taunt_engine = TauntEngine()
    hunt1 = orch1._prepare(200)
    orch1._go_live(hunt1)
    src1.reshared.add("42")
    src1.schedule[1] = lambda: [
        _post(9610, "42", f"code {_code(rig1)}", hunt1.live_at + timedelta(minutes=1),
              hunt1.reshare_post_id, handle="patient_one")
    ]
    orch1._max_rounds = 1  # dies inside the settlement window, before the ask
    try:
        orch1._claim_loop(hunt1)
    except RuntimeError:
        pass
    assert rig1.repo.hunts[hunt1.id].get("pending_ask_tweet_id") is None
    assert any(s["outcome"] == "pending" for s in rig1.repo.submissions)

    rig2 = build_simulation(repo=rig1.repo, poll_interval_s=60)
    orch2 = rig2.orchestrator
    src2 = FakeClaimSource()
    orch2._claim_source = src2
    orch2._taunt_engine = TauntEngine()
    src2.reshared.add("42")
    src2.schedule[4] = lambda: [
        _post(9650, "42", WALLET_A, rig2.clock.now(),
              rig1.repo.hunts[hunt1.id].get("pending_ask_tweet_id"))
    ]
    assert orch2.resume_hunts() == 1
    assert rig1.repo.hunts[hunt1.id]["state"] == "done"
    assert rig2.payout.sent and rig2.payout.sent[0]["wallet"] == WALLET_A


def test_validator_outage_keeps_the_wallet_reply_and_extends_the_window():
    """RPC/X down during final validation: the winner's reply must NOT be
    consumed, and the outage must not eat their 10 minutes."""
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared.add("42")
    src.schedule[1] = lambda: [
        _post(9510, "42", f"code {_code(rig)}", t0 + timedelta(minutes=1),
              hunt.reshare_post_id)
    ]
    src.schedule[5] = lambda: [
        _post(9550, "42", WALLET_A, rig.clock.now(), _ask_id(rig, hunt))
    ]
    real_validate = orch._validator.validate
    state = {"fails": 3}

    def flaky(parsed, h):
        if state["fails"] > 0:
            state["fails"] -= 1
            raise ConnectionError("rpc down")
        return real_validate(parsed, h)

    orch._validator.validate = flaky
    winner = orch._claim_loop(hunt)
    assert winner is not None and winner.wallet == WALLET_A
    assert state["fails"] == 0, "the same reply was retried through the outage"


def test_deleted_winning_post_does_not_wedge_the_hunt():
    """If the public ask can never be posted (winning post deleted -> 400),
    the claim is dropped after N attempts and the hunt continues — never a
    permanently wedged candidacy."""
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared |= {"42", "77"}
    src.schedule[1] = lambda: [
        _post(9410, "42", f"code {_code(rig)}", t0 + timedelta(minutes=1),
              hunt.reshare_post_id, handle="deleter")
    ]
    src.schedule[12] = lambda: [
        _post(9420, "77", f"code {_code(rig)}", rig.clock.now(),
              hunt.reshare_post_id, handle="steady")
    ]
    src.schedule[18] = lambda: [
        _post(9450, "77", WALLET_A, rig.clock.now(), _ask_id(rig, hunt))
    ]
    real_reply = rig.publisher.reply_post

    def gone(text, *, in_reply_to):
        if in_reply_to == "9410":
            raise RuntimeError("400: tweet deleted")
        return real_reply(text, in_reply_to=in_reply_to)

    orch._publisher.reply_post = gone
    winner = orch._claim_loop(hunt)
    assert winner.submission.sender_x_id == "77"
    outcomes = {s["dm_id"]: s["outcome"] for s in rig.repo.submissions}
    assert outcomes["9410"] == "timed_out"


def test_wallet_reply_created_after_the_deadline_is_ignored():
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared |= {"42", "77"}
    src.schedule[1] = lambda: [
        _post(9310, "42", f"code {_code(rig)}", t0 + timedelta(minutes=1),
              hunt.reshare_post_id, handle="slowpoke")
    ]
    # The wallet reply is CREATED after due_at (deep breath, too slow) — it
    # must not win, even though it reaches us before Phase 2b runs.
    src.schedule[14] = lambda: [
        _post(9340, "42", WALLET_A,
              rig.repo.hunts[hunt.id]["wallet_due_at"] + timedelta(seconds=30),
              _ask_id(rig, hunt))
    ]
    src.schedule[16] = lambda: [
        _post(9350, "77", f"code {_code(rig)}", rig.clock.now(),
              hunt.reshare_post_id)
    ]
    src.schedule[22] = lambda: [
        _post(9360, "77", WALLET_B, rig.clock.now(), _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner.submission.sender_x_id == "77"
    outcomes = {s["dm_id"]: s["outcome"] for s in rig.repo.submissions}
    assert outcomes["9310"] == "timed_out"


def test_global_taunt_budget_caps_total_replies():
    rig, orch, hunt, src = _rig()
    orch._MAX_TAUNTS_PER_HUNT = 3
    t0 = hunt.live_at
    wrongs = [
        _post(9200 + i, str(100 + i), f"guess AB{i}DEF{i}X",
              t0 + timedelta(minutes=1, seconds=i), hunt.reshare_post_id)
        for i in range(8)  # 8 different accounts, all wrong
    ]
    src.schedule[1] = lambda: wrongs
    orch._max_rounds = 6
    try:
        orch._claim_loop(hunt)
    except RuntimeError:
        pass
    assert len(rig.publisher.post_replies) == 3, "oracle goes silent past the budget"


# ---------------------------------------------------------------------------
# Hunt #4 post-mortem (2026-08-06): the thread got silence — wrong-shape
# guesses ('TSU19') and 'this is hard' chatter never earned a jeer, and a
# lowercase-typed CORRECT code would have been judged as chatter and lost.


def test_guess_like_detects_wrong_shape_attempts_only():
    from finding_memeland.claims.parser import guess_like

    assert guess_like("is it TSU19?", 8)            # letters+digit, wrong length
    assert guess_like("maybe X7Q2ZZ", 8)
    assert not guess_like("WAGMI frens", 8)          # no digit: not an attempt
    assert not guess_like("i hold 10000000 $FIND", 8)  # amount: no letter
    assert not guess_like("AB2DEF7X", 8)             # exact length: code_like's job
    assert not guess_like("wallet 0x" + "a" * 40, 8)
    assert not guess_like("gm", 8)


def test_wrong_shape_guess_gets_a_jeer_without_llm():
    """'TSU19' in the claim thread earns a pool jeer even with no LLM wired —
    once per profile, and it must NOT burn real guess-cap attempts."""
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared |= {"77"}
    src.schedule[1] = lambda: [
        _post(9910, "55", "is it TSU19?", t0 + timedelta(minutes=1),
              hunt.reshare_post_id),
        _post(9911, "55", "ok then TSU20", t0 + timedelta(minutes=2),
              hunt.reshare_post_id),
    ]
    src.schedule[5] = lambda: [
        _post(9920, "77", f"code {_code(rig)}", rig.clock.now(),
              hunt.reshare_post_id)
    ]
    src.schedule[9] = lambda: [
        _post(9930, "77", WALLET_A, rig.clock.now(), _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner.submission.sender_x_id == "77"
    jeers_1 = _replies_to(rig, 9910)
    jeers_2 = _replies_to(rig, 9911)
    assert len(jeers_1) == 1 and jeers_1[0] in TAUNT_POOL
    assert jeers_2 == [], "once per profile — the second guess gets silence"
    outcomes = {s["dm_id"]: s["outcome"] for s in rig.repo.submissions}
    assert outcomes["9910"] == "taunted"
    # NOT logged as bad_code: wrong-shape guesses must not burn guess caps.
    assert "bad_code" not in {outcomes.get("9910"), outcomes.get("9911")}


def test_lowercase_correct_code_wins():
    """'matching is generous' must hold on the gate too: the correct code
    typed in lowercase is a CLAIM, not chatter (latent bug, Hunt #4 PM)."""
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared |= {"42"}
    src.schedule[1] = lambda: [
        _post(9940, "42", f"i think it's {_code(rig).lower()}",
              t0 + timedelta(minutes=1), hunt.reshare_post_id)
    ]
    src.schedule[5] = lambda: [
        _post(9950, "42", WALLET_A, rig.clock.now(), _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner is not None and winner.submission.sender_x_id == "42"


def test_lowercase_wrong_word_stays_chatter_silence():
    """An ordinary lowercase 8-letter word ('birthday') still never triggers
    the code path or a jeer (anti-spam bar unchanged; judge fail-closed)."""
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared |= {"77"}
    src.schedule[1] = lambda: [
        _post(9960, "9", "happy birthday oracle", t0 + timedelta(minutes=1),
              hunt.reshare_post_id)
    ]
    src.schedule[5] = lambda: [
        _post(9970, "77", f"code {_code(rig)}", rig.clock.now(),
              hunt.reshare_post_id)
    ]
    src.schedule[9] = lambda: [
        _post(9980, "77", WALLET_A, rig.clock.now(), _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner.submission.sender_x_id == "77"
    assert not _replies_to(rig, 9960)
    outcomes = {s["dm_id"]: s["outcome"] for s in rig.repo.submissions}
    assert "9960" not in outcomes or outcomes["9960"] not in ("bad_code", "spam_capped")


def test_game_chatter_judged_yes_gets_a_jeer():
    """With an LLM judge saying YES ('this is hard' is game chatter now), the
    oracle replies — once, budget-capped, banned-terms enforced."""

    class YesLLM:
        class messages:
            @staticmethod
            def create(**kw):
                sys = kw.get("system", "")
                txt = "YES" if "judge replies" in sys else "skill issue. the pond forgives no one. 🐸"

                class B:
                    type = "text"
                    text = txt
                return type("R", (), {"content": [B()]})()

    rig, orch, hunt, src = _rig()
    orch._taunt_engine = TauntEngine(YesLLM(), "m")
    t0 = hunt.live_at
    src.reshared |= {"77"}
    src.schedule[1] = lambda: [
        _post(9990, "12", "this is hard, oracle", t0 + timedelta(minutes=1),
              hunt.reshare_post_id)
    ]
    src.schedule[5] = lambda: [
        _post(9991, "77", f"code {_code(rig)}", rig.clock.now(),
              hunt.reshare_post_id)
    ]
    src.schedule[9] = lambda: [
        _post(9992, "77", WALLET_A, rig.clock.now(), _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner.submission.sender_x_id == "77"
    jeers = _replies_to(rig, 9990)
    assert len(jeers) == 1
    outcomes = {s["dm_id"]: s["outcome"] for s in rig.repo.submissions}
    assert outcomes["9990"] == "taunted"


def test_correct_code_takes_precedence_over_guess_shaped_noise():
    """Adversarial precedence (Opus review): a post carrying BOTH a
    guess-shaped token ('TSU19') and the CORRECT code must go down the claim
    path and win — never intercepted by the jeer path. And the only reply the
    winning post gets is the wallet ask, not a pool taunt."""
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.reshared |= {"42"}
    code = _code(rig)
    assert code_like(code, len(code)), "generated codes are code_like by construction"
    src.schedule[1] = lambda: [
        _post(9995, "42", f"TSU19? no wait — it's {code}",
              t0 + timedelta(minutes=1), hunt.reshare_post_id)
    ]
    src.schedule[5] = lambda: [
        _post(9996, "42", WALLET_A, rig.clock.now(), _ask_id(rig, hunt))
    ]
    winner = orch._claim_loop(hunt)
    assert winner is not None and winner.submission.sender_x_id == "42"
    for reply in _replies_to(rig, 9995):
        assert reply not in TAUNT_POOL, "winning post must never be jeered"
    outcomes = {s["dm_id"]: s["outcome"] for s in rig.repo.submissions}
    assert outcomes.get("9995") not in ("taunted", "bad_code")


# ---------------------------------------------------------------------------
# Hunt #5 post-mortem (13/08): o oráculo tem de responder — MEWTWO, perguntas
# de jogo e coles de contrato ficaram em silêncio e a interação é produto
# ---------------------------------------------------------------------------
def test_lone_caps_name_guess_is_guess_like():
    from finding_memeland.claims.parser import guess_like

    assert guess_like("MEWTWO", 8)                    # o caso real do Hunt 5
    assert guess_like("PIKACHU?", 8)                  # pontuação não conta como token
    assert not guess_like("MEWTWO is the answer", 8)  # multi-palavra: vai ao juiz
    assert not guess_like("WAGMI", 8)                 # shout comum: stoplist
    assert not guess_like("GM", 8)                    # curto demais
    assert not guess_like("mewtwo", 8)                # minúsculas: não é grito de palpite


def test_contract_paste_is_jeered_material():
    from finding_memeland.claims.parser import contract_paste_like

    assert contract_paste_like("0x460766cc4158ffbd70feffd3d2891237e064ab54")
    assert contract_paste_like("the answer is 0xDEADbeef99 obviously")
    assert not contract_paste_like("just 0x alone")
    assert not contract_paste_like("no hex here fren")


def test_contract_paste_in_claim_thread_gets_a_pool_taunt_without_judge():
    """O cole do contrato no thread da Clue 1 leva jeer do pool — sem LLM
    nenhum (TauntEngine() sem cliente), provando que não depende do juiz."""
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    src.schedule[1] = lambda: [
        _post(1010, "77", "found it: 0x460766cc4158ffbd70feffd3d2891237e064ab54",
              t0 + timedelta(minutes=1), hunt.reshare_post_id, handle="paster"),
    ]
    src.schedule[3] = lambda: [
        _post(1030, "42", f"ok fine: {_code(rig)}", t0 + timedelta(minutes=3),
              hunt.reshare_post_id, handle="sharp_anon"),
    ]
    src.reshared.add("42")
    src.schedule[6] = lambda: [
        _post(1060, "42", f"wallet {WALLET_A}", t0 + timedelta(minutes=6),
              _ask_id(rig, hunt)),
    ]
    orch._claim_loop(hunt)
    jeers = _replies_to(rig, 1010)
    assert jeers, "o cole de contrato tem de levar resposta"
    assert jeers[0] in TAUNT_POOL
    taunted_rows = [s for s in rig.repo.submissions
                    if s.get("outcome") == "taunted" and s["dm_id"] == "1010"]
    assert taunted_rows, "o jeer fica no log (once-per-profile sobrevive restarts)"


def test_judge_prompt_now_classifies_name_guesses_and_game_questions_as_yes():
    """A recalibração é prompt-level; o contrato mínimo testável é que os
    exemplos do Hunt 5 estão no prompt como YES e o default virou engajamento."""
    from finding_memeland.claims.taunts import _FUNNY_SYSTEM

    assert "NAME GUESSES" in _FUNNY_SYSTEM
    assert "is it pepe?" in _FUNNY_SYSTEM
    assert "what's the first name?" in _FUNNY_SYSTEM
    assert "engages with the hunt in any way, say YES" in _FUNNY_SYSTEM
    # o fail-closed em ERRO mantém-se (testado em
    # test_taunt_engine_chatter_judge_fails_closed)


def test_lone_caps_hostility_never_takes_the_direct_jeer_path():
    """Opus (13/08): jeerar a uma acusação convida os negativos pesados do
    algoritmo (report −468 likes). Hostilidade em caps cai para o juiz, cujo
    NO a hostilidade genuína é o escudo — nunca para o jeer direto."""
    from finding_memeland.claims.parser import guess_like

    for hostile in ("SCAMMER", "RUGPULL", "PONZI", "FRAUD", "REPORTED",
                    "GARBAGE", "THIEVES"):
        assert not guess_like(hostile, 8), hostile
    assert guess_like("MEWTWO", 8)          # os palpites a sério continuam a jeerar

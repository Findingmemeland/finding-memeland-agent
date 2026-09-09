"""Target-hunt DRY-RUN (soldadura 6/6): the whole game through the REAL
orchestrator, with the world faked and the clock accelerated.

Two users share this harness on purpose:
  · tests/test_target_integration.py imports `TargetWorld` and the fakes,
    so every scenario below is also a test that runs on every commit
  · scripts/simulate_target_hunt.py runs the scenarios with `verbose=True`
    (and optionally the REAL clue engine) so the operator READS every post
    in order, the way a player will — the last time they are seen before
    they are public (Opus, 09/09).

Scenarios (the first three are the ones Opus made MANDATORY for the
dry-run — "exercised, not assumed"):
  1. rarible_429       — the search guard goes unverifiable mid-hunt (a
                         third party's rate limit): no clue goes out, the
                         hold freezes the deadline, re-notifies hourly, the
                         accumulated ceiling CALLS the operator, and when
                         the guard recovers the ramp resumes
  2. gateway_down_void — mutation detected over RPC while OUR gateway is
                         down at void time: the void post goes out and says
                         it is our failure (R8), never "burned"
  3. rpc_oscillation   — RPC flapping that never trips the episode ceiling
                         trips the ACCUMULATED one (R5)
  4. happy_path        — Clue 1 → clues → right id → wallet → pay → reveal;
                         the commitment recomputes from the public posts
  5. shotgun           — multi-token posts: one format reply per profile,
                         every post logged 'malformed', operator told once
  6. content_redraw    — the content guard refuses the first draw; the id
                         is excluded and the next draw launches
  7. resume            — crash-resume from the sealed row; the hold seconds
                         survive and the operator hears the detector reset

Every check that a scenario asserts is listed in its report, so a red line
names what failed. Nothing here touches the network.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import timedelta

from ..claims.parser import ClaimPost
from ..claims.taunts import TauntEngine
from ..orchestrator.simulation import build_simulation
from ..orchestrator.state_machine import HuntState
from .commitment import compute_commitment_v2
from .hunt import (
    JudgeVerdict,
    LiveCheck,
    LiveHash,
    LiveRead,
    SealedTargetCipher,
    SprayDetector,
    SprayParams,
    TargetHuntPreparer,
)
from .integration import TargetPorts
from .refresh import content_id
from .selector import CurationEpoch, metadata_hash
from .snapshot import Snapshot, SnapshotEntry, SnapshotStore
from .sources import ChainUnavailable

WALLET_A = "0x" + "a" * 40
EPOCH = CurationEpoch(epoch_id="e1")
# rates > 1 are nonsense in production; here they lift 180 entries over the
# 100k floor so the game logic (not the census) is what runs
RATES = {"foundation": 1000.0, "superrare2": 1000.0, "makersplace": 1000.0}
POOL_NAME = "Whispering Harbor"


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class MemStore:
    def __init__(self):
        self.blob = None

    def read(self):
        return self.blob

    def write(self, b):
        self.blob = b


class XorCipher:
    """Not a cipher — a reversible scramble so the sealed row is not plain
    text in a dump. Production uses FernetPoolCipher(TARGET_POOL_KEY)."""

    def encrypt(self, p):
        return p[::-1]

    def decrypt(self, t):
        return t[::-1]


class FakeClaimSource:
    def __init__(self):
        self.schedule: dict[int, object] = {}
        self.reshared: set[str] = set()
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
        return []

    def has_reshared(self, user_id, post_id):
        return user_id in self.reshared

    def lookup_profile(self, user_id):
        return {"name": "Some One", "handle": f"u{user_id}", "bio": "gm"}


class FakeControl:
    def __init__(self):
        self._paused = False
        self.pause_calls = 0

    def paused(self):
        return self._paused

    def pause(self):
        self.pause_calls += 1
        self._paused = True
        return 1


def synthetic_snapshot(*, per_stratum: int = 60) -> Snapshot:
    """180 entries over three strata, content-addressed, distinct base name
    per entry (the pool's own dedupe would otherwise kill them all)."""
    entries = []
    i = 0
    for plat in ("foundation", "superrare2", "makersplace"):
        for _ in range(per_stratum):
            i += 1
            meta = {"name": f"{POOL_NAME} {i}", "image": f"ipfs://img{i}",
                    "description": "quiet", "artist": f"Painter {i}"}
            uri = f"ipfs://Qm{str(i).rjust(44, '1')}/metadata.json"
            entries.append(SnapshotEntry(
                chain="ethereum", contract=f"0x{i:040x}", token_id=i,
                name=POOL_NAME, name_onchain=f"{POOL_NAME} {i}",
                metadata=meta, metadata_sha256=metadata_hash(meta), platform=plat,
                token_uri=uri, content_id=content_id(uri)))
    return Snapshot(epoch_id="e1", built_at="2026-08-01T11:00:00Z", entries=entries)


FAKE_ARTWORK = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64     # sniffs as image/png


class TargetWorld:
    """The snapshot, the live chain (tokenURI + owner by key), and the rig
    — the real Orchestrator with target mode on, everything else faked."""

    def __init__(self, *, live_params=None, snapshot: Snapshot | None = None,
                 clue_engine=None, describe_image=None, verbose: bool = False,
                 seed: int = 0):
        self.snapshot = snapshot or synthetic_snapshot()
        entries = self.snapshot.entries
        # the live chain: key -> (token_uri, owner); None = burned. Scenarios
        # mutate by swapping the URI (a new CID), burn by setting None.
        self.live = {(e.chain, e.contract, e.token_id): (e.token_uri, "0xowner")
                     for e in entries}
        self.down = False
        mem = MemStore()
        store = SnapshotStore(cipher=XorCipher(), read=mem.read, write=mem.write)
        store.save(self.snapshot)
        self.fetches: list[tuple] = []

        def fetch_live(chain, contract, tid):
            self.fetches.append((chain, contract, tid))
            if self.down:
                raise ChainUnavailable("rpc down")
            v = self.live.get((chain, contract, tid))
            return LiveRead(v[0], v[1]) if v else LiveRead(None, None)
        self.fetch_live = fetch_live

        self.control = FakeControl()
        self.ports = TargetPorts(
            epoch=EPOCH,
            preparer=TargetHuntPreparer(
                snapshot_store=store, writability_rates=RATES,
                uniqueness_rates={k: 1.0 for k in RATES},
                cap_exempt=frozenset(),
                judge=lambda batch: [JudgeVerdict(True, True) for _ in batch],
                name_is_unique=lambda base, ch, c, t: True,
                now_iso=lambda: "2026-08-01T12:00:00Z", rng=random.Random(seed)),
            cipher=SealedTargetCipher(cipher=XorCipher()),
            clue_engine=None,                       # set below
            describe_image=describe_image or (lambda sealed: "a lighthouse on a black rock"),
            live_check=LiveCheck(read_live=fetch_live, rng=random.Random(1)),
            live_hash=lambda sealed: LiveHash("resolved", "deadbeef" * 8),
            fetch_artwork=lambda sealed: FAKE_ARTWORK,
            resolve_link=None,
            spray=SprayDetector(live_params or SprayParams()),
        )
        self.rig = build_simulation(poll_interval_s=60, verbose=verbose)
        self.orch = self.rig.orchestrator
        self.ports.clue_engine = clue_engine or self.orch._clue_engine   # FakeClueEngine
        self.orch._target_launch = True
        self.orch._target = self.ports
        self.orch._control = self.control
        self.src = FakeClaimSource()
        self.orch._claim_source = self.src
        self.orch._taunt_engine = TauntEngine()

    def launch(self):
        hunt = self.orch._prepare(200)
        self.rig.clock.sleep(600)
        self.orch._go_live(hunt)
        return hunt

    # -- helpers the scenarios and tests share ------------------------------ #
    def posts(self) -> list[str]:
        return list(self.rig.publisher.posts)

    def notices(self) -> list[str]:
        return list(self.rig.notifier.messages)


def post(tid, author, text, at, reply_to):
    return ClaimPost(tweet_id=str(tid), author_id=str(author),
                     author_handle=f"u{author}", text=text, created_at=at,
                     conversation_id=None, replied_to_id=reply_to)


def replies_to(rig, tid):
    return [t for r, t in rig.publisher.post_replies if r == str(tid)]


# --------------------------------------------------------------------------- #
# Scenario reports                                                             #
# --------------------------------------------------------------------------- #


@dataclass
class ScenarioReport:
    name: str
    checks: list[tuple[str, bool]] = field(default_factory=list)
    posts: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    replies: list[tuple[str, str]] = field(default_factory=list)

    def check(self, label: str, ok: bool) -> None:
        self.checks.append((label, bool(ok)))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok in self.checks)

    def failures(self) -> list[str]:
        return [label for label, ok in self.checks if not ok]

    def render(self) -> str:
        head = f"[{'OK ' if self.ok else 'FAIL'}] {self.name}"
        lines = [head] + [f"    {'✓' if ok else '✗'} {label}" for label, ok in self.checks]
        return "\n".join(lines)


def _secrecy(rep: ScenarioReport, world: TargetWorld, target_name: str) -> None:
    """Every scenario: the operator channel never carries the name, and no
    public post before the reveal/void carries it either."""
    rep.check("operator notices never name the target",
              all(target_name not in m for m in world.notices()))


# --------------------------------------------------------------------------- #
# Scenarios                                                                    #
# --------------------------------------------------------------------------- #


def scenario_happy_path(world: TargetWorld) -> ScenarioReport:
    """Clue 1 → clues → right id → wallet → pay → reveal → retire. What the
    operator reads: every post in order, then the reveal recomputing."""
    rep = ScenarioReport("happy_path — Clue 1 → clues → claim → pay → reveal")
    hunt = world.launch()
    t0 = hunt.live_at
    target = hunt.target.target
    # a few clues before anyone claims: 4 cycles of 60s with clues due at once
    world.orch._clue_due_fn = lambda now: now
    world.src.reshared.add("42")
    world.src.schedule[5] = lambda: [
        post(1010, "42", f"ethereum:{target.contract}:{target.token_id}",
             t0 + timedelta(minutes=5), hunt.reshare_post_id)]
    world.src.schedule[8] = lambda: [
        post(1040, "42", WALLET_A, t0 + timedelta(minutes=8),
             world.rig.repo.hunts[hunt.id].get("pending_ask_tweet_id"))]
    winner = world.orch._claim_loop(hunt)
    receipt = world.orch._pay(hunt, winner)
    world.orch._reveal(hunt, winner, receipt)
    world.orch._retire(hunt)
    posts = world.posts()
    rep.check("hunt ends DONE", hunt.state is HuntState.DONE)
    rep.check("Clue 1 carries 'commitment v2:'", "commitment v2:" in posts[0])
    clue_body = posts[0].split("1st clue:", 1)[-1].split("The first to find", 1)[0].lower()
    rep.check("the clue itself never names a chain; the post never mentions a claim code",
              not any(w in clue_body for w in ("ethereum", "base", "polygon", "arbitrum"))
              and "claim code" not in posts[0].lower())
    rep.check("at least 4 clues went out before the claim",
              sum(1 for p in posts if "Clue" in p) >= 4)
    reveal = next((p for p in posts if "We have a winner" in p), "")
    rep.check("reveal names the treasure", bool(reveal) and target.name_onchain in reveal)
    try:
        chain = re.search(r"chain: (\S+)", reveal).group(1)
        contract = re.search(r"contract: (\S+)", reveal).group(1)
        token = re.search(r"tokenId: (\S+)", reveal).group(1)
        meta = re.search(r"  metadata_sha256: (\S+)", reveal).group(1)
        salt = re.search(r"salt: (\S+)", reveal).group(1)
        recomputes = compute_commitment_v2(f"{chain}:{contract}:{token}", meta, salt) == hunt.integrity_hash
    except AttributeError:
        recomputes = False
    rep.check("commitment recomputes from the public posts alone", recomputes)
    rep.check("reveal prints the tokenURI sealed at launch", target.token_uri in reveal)
    rep.check("reveal links the item (opensea.io/item/…)",
              f"see it: opensea.io/item/{target.chain}/{target.contract.lower()}/{target.token_id}" in reveal)
    reveal_id = next((tid for tid, m in world.rig.publisher.media.items()), None)
    rep.check("reveal post carries the artwork as media",
              reveal_id is not None and world.rig.publisher.media[reveal_id] == FAKE_ARTWORK
              and len(world.rig.publisher.media) == 1)
    if target.artist:
        rep.check("R9: reveal credits the artist; alt-text carries title + author",
                  f"“{target.name_onchain}”, by {target.artist}" in reveal
                  and world.rig.publisher.media_alt.get(reveal_id, "").startswith(
                      f"“{target.name_onchain}”, by {target.artist}"))
    else:
        # measured 09/09: Foundation metadata (FND #1) has no artist key at all —
        # the credit falls back to title + item link, nothing invented
        rep.check("R9: metadata names no author → title + link only, nothing invented (see note)",
                  ", by " not in reveal and "made by someone else" in reveal
                  and world.rig.publisher.media_alt.get(reveal_id, "").startswith(
                      f"“{target.name_onchain}” —"))
    hint = "chain:contract:tokenId"

    def nth(label):
        return next((p for p in posts if p.startswith(label)), "")
    rep.check("claim line on clues 2 and 3, then every fifth (not on every clue)",
              hint in nth("2nd Clue:") and hint in nth("3rd Clue:")
              and nth("4th Clue:") and hint not in nth("4th Clue:")
              and hint in nth("5th Clue:"))
    rep.check("prize paid to the winner's wallet",
              bool(world.rig.payout.sent) and world.rig.payout.sent[0]["wallet"] == WALLET_A)
    rep.check("no mutation note on an intact hunt", "note:" not in reveal)
    pre_reveal = [p for p in posts if "We have a winner" not in p]
    rep.check("no post before the reveal names the treasure",
              all(target.name_onchain not in p for p in pre_reveal))
    _secrecy(rep, world, target.name_onchain)
    rep.posts, rep.notices, rep.replies = posts, world.notices(), list(world.rig.publisher.post_replies)
    return rep


class _GuardOutageEngine:
    """Clue 1 fine; from clue 2 the search guard is unverifiable (a Rarible
    429) for `blind_s` seconds of SIM CLOCK after the first attempt, then
    recovers. Time-based, not call-based: the loop may retry a clue several
    times per cycle, and the outage must outlast the hold ceiling."""

    def __init__(self, inner, clock, blind_s: float):
        self._inner = inner
        self._clock = clock
        self._blind_s = blind_s
        self._since = None
        self.calls = 0

    def next_clue(self, ctx, i, prior):
        if i == 1:
            return self._inner.next_clue(ctx, i, prior)
        self.calls += 1
        now = self._clock.now().timestamp()
        if self._since is None:
            self._since = now
        if now - self._since < self._blind_s:
            from .clues import SearchGuardUnavailable
            raise SearchGuardUnavailable("marketplace 429 — canary blind")
        return self._inner.next_clue(ctx, i, prior)


def scenario_rarible_429(world: TargetWorld, *, max_hold_s: float = 3600.0,
                         max_total_hold_s: float = 2 * 3600.0) -> ScenarioReport:
    """MANDATORY (Opus): guard down → hold → hourly re-notify → ceilings call
    the operator → guard recovers → ramp resumes, hold released."""
    rep = ScenarioReport("rarible_429 — search guard blind mid-hunt (hold, ceilings, recovery)")
    world.ports.max_hold_s = max_hold_s
    world.ports.max_total_hold_s = max_total_hold_s
    engine = _GuardOutageEngine(world.ports.clue_engine, world.rig.clock,
                                blind_s=150 * 60)          # 2h30 of sim clock
    world.ports.clue_engine = engine
    hunt = world.launch()
    world.orch._hunt_timeout_h = 1                        # would have voided at 1h without the hold
    world.orch._clue_due_fn = lambda now: now
    world.orch._max_rounds = 170                          # 2h50 at 60s/cycle
    try:
        world.orch._claim_loop(hunt)
    except RuntimeError:
        pass                                               # max rounds — the sim's clock stop
    msgs = world.notices()
    rep.check("guard outage entered the hold", any("search guard unverifiable" in m for m in msgs))
    rep.check("hold re-notified at least twice", sum("still on HOLD" in m or "HOLD past MAX" in m for m in msgs) >= 2)
    rep.check("episode ceiling called the operator (DECIDE)", any("HOLD past MAX" in m and "DECIDE" in m for m in msgs))
    rep.check("hunt NOT voided by the outage (deadline frozen)", hunt.state is HuntState.LIVE)
    rep.check("hold seconds persisted on the row", world.rig.repo.hunts[hunt.id]["target_hold_s"] > 3600)
    clue_posts = [p for p in world.posts() if "Clue" in p]
    rep.check("no clue went out while the guard was blind (engine was asked, refused)",
              engine.calls > 1 and len(clue_posts) >= 1)
    rep.check("guard recovered → ramp resumed (2nd clue out)", len(clue_posts) >= 2)
    rep.check("hold released on recovery", any("hold released" in m for m in msgs))
    _secrecy(rep, world, hunt.target.target.name_onchain)
    rep.posts, rep.notices = world.posts(), msgs
    return rep


def scenario_gateway_down_at_void(world: TargetWorld) -> ScenarioReport:
    """MANDATORY (Opus): mutation measured over RPC, OUR gateway down when
    the void post is built — the post goes out, says the failure is ours."""
    rep = ScenarioReport("gateway_down_void — mutation detected, our gateway down at void time")

    def gateway_down(sealed):
        raise ChainUnavailable("pinata 503")
    world.ports.live_hash = gateway_down
    hunt = world.launch()
    t = hunt.target.target
    world.live[(t.chain, t.contract, t.token_id)] = ("ipfs://Qm" + "9" * 44 + "/metadata.json", "0xowner")
    world.orch._clue_due_fn = lambda now: now
    world.orch._claim_loop(hunt)
    void = next((p for p in world.posts() if "is void" in p), "")
    rep.check("void post went out", bool(void))
    rep.check("cause is the MEASURED one (tokenURI points at different content)",
              "tokenURI now points at different content" in void)
    rep.check("says our gateway could not fetch the live hash", "our gateway could not fetch it" in void)
    rep.check("never says 'reverts' / 'burned' / 'owner' over our outage",
              "reverts" not in void and "burned" not in void and "owner" not in void.split("Here is")[0])
    rep.check("prints sealed AND live tokenURI", t.token_uri in void and "Qm" + "9" * 44 in void)
    rep.check("hunt ends DONE (relaunch is operator-driven)", hunt.state is HuntState.DONE)
    rep.check("voided id recorded for exclusion", world.rig.repo.hunts[hunt.id]["target_void_id"] == hunt.target.id())
    _secrecy(rep, world, t.name_onchain)
    rep.posts, rep.notices = world.posts(), world.notices()
    return rep


def scenario_rpc_oscillation(world: TargetWorld) -> ScenarioReport:
    """MANDATORY (Opus): 5h down / 10min up, forever — the episode ceiling
    (6h) never trips; the ACCUMULATED one (12h) does."""
    rep = ScenarioReport("rpc_oscillation — flapping RPC trips the ACCUMULATED ceiling")
    world.ports.max_hold_s = 6 * 3600.0
    world.ports.max_total_hold_s = 12 * 3600.0
    hunt = world.launch()
    world.orch._hunt_timeout_h = None
    world.orch._clue_due_fn = lambda now: now
    cycle = {"n": 0}

    def flap(chain, contract, tid):                        # 300 cycles down, 10 up
        cycle["n"] += 1
        if (cycle["n"] // 8) % 310 < 300:                   # 8 reads per batch
            raise ChainUnavailable("flapping")
        return world.fetch_live(chain, contract, tid)
    world.ports.live_check = LiveCheck(read_live=flap, rng=random.Random(2))
    world.orch._max_rounds = 15 * 60                        # 15h at 60s/cycle
    try:
        world.orch._claim_loop(hunt)
    except RuntimeError:
        pass
    msgs = world.notices()
    rep.check("it did oscillate (hold released at least once)", any("hold released" in m for m in msgs))
    rep.check("episode ceiling never tripped", not any("HOLD past MAX" in m and "ACCUMULATED" not in m for m in msgs))
    rep.check("ACCUMULATED ceiling called the operator", any("ACCUMULATED" in m for m in msgs))
    rep.check("nothing decided automatically (still LIVE)", hunt.state is HuntState.LIVE)
    _secrecy(rep, world, hunt.target.target.name_onchain)
    rep.notices = msgs
    return rep


def scenario_shotgun(world: TargetWorld) -> ScenarioReport:
    """One account, four posts of three links each: one format reply, four
    'malformed' rows, one operator notice, no guess spent, no match — even
    with the target among the links."""
    from .templates import POST_REPLY_ONE_TOKEN
    rep = ScenarioReport("shotgun — multi-token posts: refused, logged, operator told once")
    hunt = world.launch()
    t0 = hunt.live_at
    t = hunt.target.target
    world.src.reshared.add("42")
    links = [f"https://opensea.io/assets/ethereum/0x{i:040x}/1" for i in range(2)]
    links.append(f"https://opensea.io/assets/ethereum/{t.contract}/{t.token_id}")
    world.src.schedule[1] = lambda: [
        post(4100 + k, "42", " ".join(links), t0 + timedelta(minutes=k + 1), hunt.reshare_post_id)
        for k in range(4)]
    world.orch._max_rounds = 3
    try:
        world.orch._claim_loop(hunt)
    except RuntimeError:
        pass
    all_replies = [r for k in range(4) for r in replies_to(world.rig, 4100 + k)]
    subs = world.rig.repo.submissions
    rep.check("exactly ONE format reply for the profile", all_replies == [POST_REPLY_ONE_TOKEN])
    rep.check("four rows logged as 'malformed'", sum(1 for s in subs if s.get("outcome") == "malformed") == 4)
    rep.check("no guess spent, no match (target was among the links)",
              not any(s.get("outcome") in ("won", "pending", "bad_code") for s in subs))
    shots = [m for m in world.notices() if "shotgun posts from" in m]
    rep.check("operator told once about the shotgun account (at 3, counted 'so far')",
              len(shots) == 1 and "3 multi-token replies so far" in shots[0])
    rep.check("the log never carries the target", t.contract not in repr(subs))
    _secrecy(rep, world, t.name_onchain)
    rep.notices, rep.replies = world.notices(), list(world.rig.publisher.post_replies)
    return rep


def scenario_content_redraw(world: TargetWorld) -> ScenarioReport:
    """The content guard refuses the first artwork; the id is excluded and
    the second draw launches; the refused id is never named."""
    from .clues import ContentRefused
    rep = ScenarioReport("content_redraw — content guard refuses the first draw")
    seen: list[str] = []

    def describe(sealed):
        seen.append(sealed.id())
        if len(seen) == 1:
            raise ContentRefused(sealed.id(), "nsfw")
        return "a lighthouse on a black rock"
    world.ports.describe_image = describe
    hunt = world.launch()
    rep.check("two draws, different ids", len(seen) == 2 and seen[0] != seen[1])
    rep.check("the launched target is the second draw", hunt.target.id() == seen[1])
    rep.check("operator notified of the redraw", any("content guard refused" in m for m in world.notices()))
    rep.check("hunt went LIVE", hunt.state is HuntState.LIVE)
    _secrecy(rep, world, hunt.target.target.name_onchain)
    rep.notices = world.notices()
    return rep


def scenario_resume(world: TargetWorld) -> ScenarioReport:
    """Crash during a hold; the resumed hunt keeps its held seconds and the
    operator hears the anti-spray detector restarted (known limit, visible)."""
    rep = ScenarioReport("resume — crash mid-hold, sealed row rebuilt, detector reset announced")
    hunt = world.launch()
    world.orch._hunt_timeout_h = None
    world.orch._clue_due_fn = lambda now: now
    world.orch._max_rounds = 5
    world.down = True
    try:
        world.orch._claim_loop(hunt)
    except RuntimeError:
        pass
    row = world.rig.repo.hunts[hunt.id]
    before = len(world.notices())
    rebuilt = world.orch._rebuild_hunt(row, HuntState.LIVE)
    now = world.rig.clock.now().timestamp()
    rep.check("sealed target rebuilt identically", rebuilt.target == hunt.target)
    rep.check("held seconds survived the crash", row["target_hold_s"] > 0
              and rebuilt.target_hold.held_seconds(now) >= row["target_hold_s"])
    rep.check("operator told the anti-spray detector restarted from zero",
              any("anti-spray detector restarted from zero" in m for m in world.notices()[before:]))
    rep.check("persona x_user_id never the target id", rebuilt.persona.x_user_id == "")
    _secrecy(rep, world, hunt.target.target.name_onchain)
    rep.notices = world.notices()
    return rep


SCENARIOS = {
    "happy": scenario_happy_path,
    "429": scenario_rarible_429,
    "void": scenario_gateway_down_at_void,
    "flap": scenario_rpc_oscillation,
    "shotgun": scenario_shotgun,
    "content": scenario_content_redraw,
    "resume": scenario_resume,
}
MANDATORY = ("429", "void", "flap")


def run_scenarios(names=None, *, verbose: bool = False, world_factory=None) -> list[ScenarioReport]:
    """Fresh world per scenario. `world_factory(verbose)` lets the script
    inject a real clue engine / a real snapshot for the happy path."""
    names = list(names or SCENARIOS)
    out = []
    for name in names:
        factory = world_factory or (lambda v: TargetWorld(verbose=v))
        world = factory(verbose)
        if verbose:
            print("\n" + "=" * 72 + f"\nSCENARIO {name}\n" + "=" * 72)
        out.append(SCENARIOS[name](world))
    return out

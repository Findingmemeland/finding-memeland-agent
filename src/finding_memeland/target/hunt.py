"""Target hunt — prepare, seal, watch, and protect (Option A), with NO
state-machine dependency. The orchestrator's target branch calls these; the
logic tests offline.

Four decisions ratified 06/09 (Pedro, after Opus's review), each mechanical
here:

1. ANTI-SPRAY = PAUSE + HUMAN REVIEW, NEVER A VOID. A void on spray hands
   any loser a veto over the community: can't win → spray → nobody wins.
   The prize is never cancelled or confiscated over spray. And the trigger
   is SHAPE, not volume: in Option A wrong guesses ARE success (a hundred
   people risking names is the goal), so an absolute count punishes
   popularity. A crowd is wide and shallow (most accounts guess 1-2 times);
   a sybil farm is narrow and deep (every account leaning on the cap of
   5). Trigger = BOTH: distinct targets guessed in the puzzle phase above
   N, AND median guesses per account >= 4. N is a RESERVED parameter
   (published numbers are numbers an attacker stops one unit short of);
   what is published is the consequence — see PUBLIC_SPRAY_RULE.

2. CONTENT/PROVENANCE GUARD ON EVERY STRATUM. Human-curated platforms
   still carry NSFW and stolen art, the cost is one judge call per draw
   either way, and a guard with per-stratum exceptions is a guard with a
   branch that will drift the day epoch 2 adds a source. Uniform has no
   such failure mode. The judge answers writability AND content in ONE
   call (the pool is secret; only the drawn target is ever shown to it).

3. TARGET_POOL_KEY — a key of its own (wired in adapters/config). The
   registry is the artifact the tail's cap exemption rests on; coupling it
   to a key that went through ten hunts and many hands is a blast radius
   we don't need. Here: the cipher is injected, and the sealed target's
   repr never shows contents.

4. LIVE CHECK PER CLUE, NEVER SINGLING THE TARGET OUT. One tokenURI read
   per published clue buys days of warning instead of a claim-time
   surprise — but a lone read of THAT token tells the RPC and the gateway
   which one it is, exactly when that is worth money. So the read goes in
   a BATCH with decoys drawn from the snapshot, via generic RPC and public
   IPFS gateway, never an API carrying our key. And the response is
   proportional to what can still be saved: mutation/burn in the PUZZLE
   phase → RELAUNCH (void-reveal + fresh target, an hour for us, nothing
   for players); later → VOID-REVEAL; transport failure → HOLD with the
   operator notified, never a void over an RPC being down (R2).
"""

from __future__ import annotations

import json
import random
import statistics
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from .commitment import compute_commitment_v2, generate_salt
from .selector import CurationEpoch, SelectionRefused, Target, metadata_hash
from .snapshot import Snapshot, SnapshotStore, snapshot_selector, stratum_gate
from .sources import ChainUnavailable

# --------------------------------------------------------------------------- #
# The sealed target — what the hunt row stores (encrypted) for resume          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SealedTarget:
    """The hunt's secret: the target, the salt, the commitment. Lives
    encrypted next to the hunt row; decrypted only inside the orchestrator
    process. repr/str NEVER show contents."""

    target: Target
    salt: str
    commitment: str

    def id(self) -> str:
        return self.target.id()

    def __repr__(self) -> str:
        return f"SealedTarget(epoch={self.target.epoch!r}, commitment set)"

    __str__ = __repr__


class SealedTargetIntegrityError(RuntimeError):
    """Sealed payload unreadable — message never carries contents."""


class SealedTargetCipher:
    """Encrypt/decrypt a SealedTarget through the PoolCipher port
    (FernetPoolCipher(TARGET_POOL_KEY) in production)."""

    def __init__(self, *, cipher):
        self._cipher = cipher

    def seal(self, s: SealedTarget) -> str:
        t = s.target
        doc = {"v": 2, "salt": s.salt, "commitment": s.commitment,
               "target": {"chain": t.chain, "contract": t.contract,
                          "token_id": t.token_id, "name": t.name,
                          "name_onchain": t.name_onchain,
                          "description": t.description, "image": t.image,
                          "metadata_sha256": t.metadata_sha256,
                          "epoch": t.epoch}}
        return self._cipher.encrypt(json.dumps(doc, ensure_ascii=False))

    def unseal(self, blob: str) -> SealedTarget:
        try:
            doc = json.loads(self._cipher.decrypt(blob))
            if doc.get("v") != 2:
                raise ValueError("not a v2 sealed target")
            t = doc["target"]
            target = Target(chain=t["chain"], contract=t["contract"],
                            token_id=int(t["token_id"]), name=t["name"],
                            name_onchain=t["name_onchain"],
                            description=t.get("description", ""),
                            image=t.get("image", ""),
                            metadata_sha256=t["metadata_sha256"],
                            epoch=t["epoch"])
            sealed = SealedTarget(target=target, salt=doc["salt"],
                                  commitment=doc["commitment"])
        except Exception as e:  # noqa: BLE001 — fail closed, no contents
            raise SealedTargetIntegrityError(
                f"sealed target unreadable ({type(e).__name__}) — wrong key "
                "or corrupted row") from e
        # belt and braces: the commitment must recompute
        if compute_commitment_v2(sealed.id(), target.metadata_sha256,
                                 sealed.salt) != sealed.commitment:
            raise SealedTargetIntegrityError(
                "sealed target commitment does not recompute — tampered or "
                "mis-sealed; refusing")
        return sealed


# --------------------------------------------------------------------------- #
# Preparing a hunt: gate → draw → judge (writable + content) → seal            #
# --------------------------------------------------------------------------- #


class LaunchRefused(RuntimeError):
    """The definitive gate said no, or no target survived the judge. The
    message is the gate's Telegram-safe render — counts and strata only."""


@dataclass(frozen=True)
class JudgeVerdict:
    """One private LLM call over the DRAWN target: is it writable (clues
    can be built from it under our doctrine) AND is its content safe to
    point a thousand people at (no NSFW, no stolen art, no scam — decision
    2: every stratum, one call). `reason` is for the operator log and
    never carries the target's name."""

    writable: bool
    content_ok: bool
    reason: str = ""


def select_judged(selector, epoch: CurationEpoch, *,
                  judge: Callable[[Target], JudgeVerdict | None],
                  exclude: frozenset[str] = frozenset(),
                  max_draws: int = 12) -> Target:
    """select_writable v2: draw until a target passes BOTH halves of the
    judge. None (judge unreachable) rejects the draw — fail-closed. `exclude`
    holds target ids that must not be drawn again (a relaunch after a
    mid-puzzle mutation excludes the voided target)."""
    for _ in range(max_draws):
        target = selector.select(epoch)
        if target.id() in exclude:
            continue
        v = judge(target)
        if v is not None and v.writable and v.content_ok:
            return target
    raise SelectionRefused(
        f"no target passed the judge in {max_draws} draws — writability "
        "certification looks stale or the judge is unreachable; re-measure "
        "before launching (fail-closed)")


class TargetHuntPreparer:
    """Everything /launch needs before the first clue, for a target hunt:
    the gate decides (GREEN or refuse), the snapshot draws, the judge
    filters, the commitment seals."""

    def __init__(self, *, snapshot_store: SnapshotStore,
                 writability_rates: dict[str, float],
                 cap_exempt: frozenset[str],
                 judge: Callable[[Target], JudgeVerdict | None],
                 now_iso: Callable[[], str],
                 rng: random.Random | None = None):
        self._store = snapshot_store
        self._rates = dict(writability_rates)
        self._cap_exempt = cap_exempt
        self._judge = judge
        self._now_iso = now_iso
        self._rng = rng

    def load_snapshot(self) -> Snapshot:
        snap = self._store.load()
        if snap is None:
            raise LaunchRefused("no snapshot — run the refresh pipeline "
                                "first (fail-closed)")
        return snap

    def prepare(self, epoch: CurationEpoch, *,
                exclude: frozenset[str] = frozenset()) -> SealedTarget:
        snap = self.load_snapshot()
        gate = stratum_gate(snap, self._rates, cap_exempt=self._cap_exempt,
                            epoch=epoch, now_iso=self._now_iso())
        if gate.verdict != "GREEN":
            raise LaunchRefused("gate is not GREEN — launch refused\n"
                                + gate.render())
        selector = snapshot_selector(snap, rng=self._rng)
        target = select_judged(selector, epoch, judge=self._judge,
                               exclude=exclude)
        salt = generate_salt()
        commitment = compute_commitment_v2(target.id(), target.metadata_sha256,
                                           salt)
        return SealedTarget(target=target, salt=salt, commitment=commitment)


# --------------------------------------------------------------------------- #
# Live check — batched with decoys; proportional response                      #
# --------------------------------------------------------------------------- #

LIVE_INTACT = "intact"
LIVE_MUTATED = "mutated"
LIVE_BURNED = "burned"
LIVE_UNAVAILABLE = "unavailable"

ACT_CONTINUE = "continue"
ACT_RELAUNCH = "relaunch"          # puzzle phase: void-reveal + fresh target
ACT_VOID_REVEAL = "void_reveal"    # later phases: void-reveal, prize to vault
ACT_HOLD = "hold"                  # transport: operator notified, no decision


@dataclass(frozen=True)
class LiveVerdict:
    status: str
    live_metadata_sha256: str | None   # None when burned/unavailable
    reads: int                          # batch size incl. decoys

    def render(self) -> str:            # Telegram-safe
        return f"live check: {self.status} ({self.reads} reads in batch)"


class LiveCheck:
    """`fetch_metadata_generic(chain, contract, token_id) -> dict | None`
    goes through a GENERIC RPC and a PUBLIC IPFS gateway — never an API
    with our key. It returns None when tokenURI/ownerOf revert (burn) or
    metadata cannot be resolved, and raises ChainUnavailable on transport
    trouble. The target read is shuffled into a batch of `decoys` other
    snapshot entries (decision 4): the read pattern never singles the
    target out."""

    def __init__(self, *, fetch_metadata_generic, decoys: int = 7,
                 rng: random.Random | None = None):
        self._fetch = fetch_metadata_generic
        self._decoys = decoys
        self._rng = rng or random.SystemRandom()

    def check(self, sealed: SealedTarget,
              pool: Sequence[tuple[str, str, int]]) -> LiveVerdict:
        t = sealed.target
        me = (t.chain, t.contract.lower(), t.token_id)
        others = [p for p in pool if (p[0], p[1].lower(), p[2]) != me]
        batch = self._rng.sample(others, min(self._decoys, len(others))) + [me]
        self._rng.shuffle(batch)
        result: LiveVerdict | None = None
        for chain, contract, tid in batch:
            try:
                meta = self._fetch(chain, contract, tid)
            except ChainUnavailable:
                if (chain, contract.lower(), tid) == me:
                    result = LiveVerdict(LIVE_UNAVAILABLE, None, len(batch))
                continue                     # a decoy's outage is noise
            except Exception:  # noqa: BLE001 — revert path for a decoy
                meta = None
            if (chain, contract.lower(), tid) != me:
                continue                     # decoy results are discarded
            if not isinstance(meta, dict) or not meta:
                result = LiveVerdict(LIVE_BURNED, None, len(batch))
            else:
                h = metadata_hash(meta)
                status = LIVE_INTACT if h == t.metadata_sha256 else LIVE_MUTATED
                result = LiveVerdict(status, h, len(batch))
        assert result is not None           # the target is always in batch
        return result


PHASE_PUZZLE = "puzzle"      # clues 1-7
PHASE_REVEAL = "reveal"      # clues 8+
PHASE_CLAIM = "claim"        # a claim was accepted; before payout


def live_policy(status: str, *, phase: str) -> str:
    """Proportional response (decision 4). Table, not judgment."""
    if status == LIVE_INTACT:
        return ACT_CONTINUE
    if status == LIVE_UNAVAILABLE:
        return ACT_HOLD
    # mutated or burned
    return ACT_RELAUNCH if phase == PHASE_PUZZLE else ACT_VOID_REVEAL


def void_reveal_ingredients(sealed: SealedTarget,
                            live: LiveVerdict) -> dict:
    """Everything the void announcement publishes (commitment.py: a void is
    as verifiable as a win): target id, committed hash, salt, commitment,
    and the live hash (None = burned: tokenURI no longer resolves)."""
    return {
        "target_id": sealed.id(),
        "metadata_sha256": sealed.target.metadata_sha256,
        "salt": sealed.salt,
        "commitment": sealed.commitment,
        "live_metadata_sha256": live.live_metadata_sha256,
        "cause": live.status,
    }


# --------------------------------------------------------------------------- #
# Anti-spray — shape, not volume; pause, never void                            #
# --------------------------------------------------------------------------- #

PUBLIC_SPRAY_RULE = (
    "If we detect coordinated brute force, the hunt pauses and the operator "
    "reviews it. The prize is never cancelled because of this."
)


@dataclass(frozen=True)
class SprayParams:
    """RESERVED — never published, never printed to Telegram. N anchored on
    our history (hunts ran in the tens of participants); review after two
    hunts of real data. The median criterion does the work: N alone never
    fires without it."""

    min_distinct_targets: int = 200
    min_median_guesses: float = 4.0


@dataclass(frozen=True)
class SprayVerdict:
    triggered: bool
    accounts: int
    distinct_targets: int
    median_per_account: float

    def render(self) -> str:
        """Operator line — counts only, and NEVER the thresholds."""
        head = "spray: PAUSE for review" if self.triggered else "spray: no"
        return (f"{head} ({self.accounts} accounts, "
                f"{self.distinct_targets} distinct targets, median "
                f"{self.median_per_account:.1f}/account)")


class SprayDetector:
    """Evaluate the PUZZLE-PHASE guess log. `guesses` are (account_id,
    target_ref_id) pairs for wrong guesses; the caller filters to the
    puzzle phase. Both conditions must hold. The only action this can ever
    recommend is a pause — there is no void path from here, by design."""

    def __init__(self, params: SprayParams = SprayParams()):
        self._p = params

    def evaluate(self, guesses: Iterable[tuple[str, str]]) -> SprayVerdict:
        per_account: dict[str, int] = {}
        targets: set[str] = set()
        for account, ref in guesses:
            per_account[account] = per_account.get(account, 0) + 1
            targets.add(ref)
        accounts = len(per_account)
        median = float(statistics.median(per_account.values())) if accounts else 0.0
        triggered = (len(targets) > self._p.min_distinct_targets
                     and median >= self._p.min_median_guesses)
        return SprayVerdict(triggered=triggered, accounts=accounts,
                            distinct_targets=len(targets),
                            median_per_account=median)

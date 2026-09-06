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
   popularity. The shape that separates a crowd from a farm is DUPLICATION
   (Opus, 06/09, P0-B — the earlier "narrow and deep" median criterion was
   evadable at zero cost, because accounts are the cheap resource: 250 × 2
   covers what 100 × 5 covers, with a median of 2). An honest crowd
   converges on the same obvious names, so distinct targets sit far below
   total guesses; a coordinated enumeration deduplicates by construction,
   because a repeat is wasted budget. Trigger = BOTH: total guesses in the
   puzzle phase above N, AND distinct/total >= R. Evading it means
   spending guesses on duplicates — the very budget we want burned. The
   median stays in the operator line as a third signal, outside the
   trigger. N and R are RESERVED parameters (published numbers are numbers
   an attacker stops one unit short of); what is published is the
   consequence — see PUBLIC_SPRAY_RULE.

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
   a BATCH with decoys, via generic RPC and public IPFS gateway, never an
   API carrying our key. THE DECOYS ARE DRAWN ONCE, AT SEAL TIME, AND
   STORED IN THE SEALED TARGET (Opus, 06/09, P0-A): decoys re-drawn per
   read are defeated by intersection — eight reads with fresh decoys and
   one constant token, and the anonymity set is 1 after three. One fixed
   batch, reshuffled in order only, makes the intersection the whole
   batch. The sealed batch pads the calls that REPEAT WITH THE TARGET
   FIXED: the live read (every clue) and the one vision fetch.

   THE DECOY RULE, because it is counter-intuitive (Opus, 06/09, the
   mirror of P0-A): WHAT STAYS CONSTANT ACROSS REPEATED CALLS IS WHAT GETS
   IDENTIFIED. So: fix the decoys where the target is fixed (LiveCheck —
   sealed batch), and vary the decoys where the candidate varies (the
   judge — `select_judged` calls it up to `max_draws` times with a
   different candidate each time; with the sealed batch the provider
   would see {D1..D7, X1}, {D1..D7, X2}, … and the varying member of the
   last batch is the target: anonymity 1 by the second call, which at
   ~50% writability is the normal case). The judge therefore gets FRESH
   decoys per call, drawn from the snapshot and discarded. Fix what
   varies, vary what is fixed.

   CUMULATIVE LEAK, written down because it only shows when it is big:
   the RPC/gateway provider sees the same 8 tokens read repeatedly during
   a hunt, so it learns 8 snapshot entries per hunt — ~160 after twenty
   hunts, members of the pool handed to one specific party. Not the pool,
   and not alarming today; it is a reason to rotate providers/gateways
   across hunts and to keep the count in view.
   Response proportional to what can still be saved: mutation/burn in the
   PUZZLE phase → RELAUNCH (void-reveal + fresh target); in the reveal
   phase → VOID-REVEAL; with a VALID CLAIM already matched → PAY, mutation
   noted (verifiably) in the reveal — the commitment binds identity, not
   ownership, and a third party's act must not cost the winner the prize;
   transport failure → HOLD with the operator notified, never a void over
   an RPC being down (R2) — and while held, the void deadline FREEZES
   (HoldLedger): we never void a hunt over our own outage.
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
class Decoy:
    """One anonymity-set member: enough to read its tokenURI, fetch its
    image and hand it to the judge alongside the target."""

    chain: str
    contract: str
    token_id: int
    image: str = ""

    def key(self) -> tuple[str, str, int]:
        return (self.chain, self.contract.lower(), self.token_id)


@dataclass(frozen=True)
class SealedTarget:
    """The hunt's secret: the target, the salt, the commitment — and the
    hunt's ONE decoy batch (P0-A). Lives encrypted next to the hunt row;
    decrypted only inside the orchestrator process. repr/str NEVER show
    contents."""

    target: Target
    salt: str
    commitment: str
    decoys: tuple[Decoy, ...] = ()

    def id(self) -> str:
        return self.target.id()

    def batch_keys(self) -> list[tuple[str, str, int]]:
        """Target + decoys, as read keys, in STORED order (callers shuffle)."""
        t = self.target
        return [(t.chain, t.contract.lower(), t.token_id)] + [d.key() for d in self.decoys]

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
        doc = {"v": 3, "salt": s.salt, "commitment": s.commitment,
               "decoys": [[d.chain, d.contract, d.token_id, d.image]
                          for d in s.decoys],
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
            if doc.get("v") != 3:
                raise ValueError("not a v3 sealed target (decoys missing)")
            t = doc["target"]
            target = Target(chain=t["chain"], contract=t["contract"],
                            token_id=int(t["token_id"]), name=t["name"],
                            name_onchain=t["name_onchain"],
                            description=t.get("description", ""),
                            image=t.get("image", ""),
                            metadata_sha256=t["metadata_sha256"],
                            epoch=t["epoch"])
            decoys = tuple(Decoy(chain=c, contract=a, token_id=int(i), image=im)
                           for c, a, i, im in doc["decoys"])
            sealed = SealedTarget(target=target, salt=doc["salt"],
                                  commitment=doc["commitment"], decoys=decoys)
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
    """One private LLM call: is the piece writable (clues can be built from
    it under our doctrine) AND is its content safe to point a thousand
    people at (no NSFW, no stolen art, no scam — decision 2: every stratum,
    one call). `reason` is for the operator log and never carries a name."""

    writable: bool
    content_ok: bool
    reason: str = ""


# judge(batch: Sequence[Target]) -> Sequence[JudgeVerdict | None], one
# verdict per input, same order. The batch is the drawn target hidden among
# the hunt's decoys (Opus, 06/09, Q4): the judge is an external provider
# that would otherwise learn the target — and, across `max_draws`, the
# sequence of rejects and the final accept — before any player does.
BatchJudge = Callable[[Sequence[Target]], Sequence["JudgeVerdict | None"]]


def judge_in_batch(judge: BatchJudge, target: Target, decoys: Sequence[Target],
                   rng: random.Random) -> JudgeVerdict | None:
    """Hide `target` among `decoys` (shuffled), call the judge once, use ONLY
    the target's verdict. A malformed batch answer (wrong length) counts as
    unreachable — None, fail-closed. `decoys` MUST be fresh for every call
    (see the decoy rule in the module docstring): repeated calls with the
    same decoys and a varying candidate identify the candidate."""
    batch = list(decoys) + [target]
    rng.shuffle(batch)
    pos = next(i for i, t in enumerate(batch) if t.id() == target.id())
    try:
        verdicts = list(judge(batch))
    except Exception:  # noqa: BLE001 — unreachable judge rejects the draw
        return None
    if len(verdicts) != len(batch):
        return None
    return verdicts[pos]


def select_judged(selector, epoch: CurationEpoch, *,
                  judge: BatchJudge, draw_decoys: Callable[[str], list[Target]],
                  rng: random.Random,
                  exclude: frozenset[str] = frozenset(),
                  max_draws: int = 12) -> Target:
    """select_writable v2: draw until a target passes BOTH halves of the
    judge, each verdict obtained inside a FRESH decoy batch (`draw_decoys(
    exclude_id)` is called once per candidate and RE-SAMPLES around the
    candidate, so the batch is always exactly n+1 — a filtered sample would
    make the anonymity set's size vary; its result is discarded after the
    call).

    Residual, recorded not removed (Opus, 06/09): the judge returns
    verdicts, and the target is necessarily among the ones it approved —
    at ~50% writability the effective anonymity of the last batch is ~1/4,
    not 1/8. Inherent to asking. A dummy call after the accept would deny
    the provider the certainty of which batch was last, at the cost of one
    call; not done today.
    None rejects the draw — fail-closed. `exclude` holds target ids that
    must not be drawn again (a relaunch after a mid-puzzle mutation
    excludes the voided target)."""
    for _ in range(max_draws):
        target = selector.select(epoch)
        if target.id() in exclude:
            continue
        decoys = draw_decoys(target.id())
        v = judge_in_batch(judge, target, decoys, rng)
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
                 judge: BatchJudge,
                 now_iso: Callable[[], str],
                 decoys: int = 7,
                 rng: random.Random | None = None):
        self._store = snapshot_store
        self._rates = dict(writability_rates)
        self._cap_exempt = cap_exempt
        self._judge = judge
        self._now_iso = now_iso
        self._n_decoys = decoys
        self._rng = rng or random.SystemRandom()

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
        # judge: FRESH decoys per candidate (decoy rule); sealed batch: drawn
        # ONCE after the target is known, fixed for the hunt's repeated reads
        target = select_judged(
            selector, epoch, judge=self._judge,
            draw_decoys=lambda tid: self._draw_decoys(snap, epoch, exclude_id=tid),
            rng=self._rng, exclude=exclude)
        salt = generate_salt()
        commitment = compute_commitment_v2(target.id(), target.metadata_sha256,
                                           salt)
        decoys = tuple(Decoy(chain=d.chain, contract=d.contract,
                             token_id=d.token_id, image=d.image)
                       for d in self._draw_decoys(snap, epoch,
                                                  exclude_id=target.id()))
        return SealedTarget(target=target, salt=salt, commitment=commitment,
                            decoys=decoys)

    def _draw_decoys(self, snap: Snapshot, epoch: CurationEpoch, *,
                     exclude_id: str) -> list[Target]:
        """Exactly n decoys, re-sampled AROUND the excluded id (never filtered
        after sampling — the anonymity set must have a constant size)."""
        pool = [e for e in snap.entries
                if f"{e.chain}:{e.contract.lower()}:{e.token_id}" != exclude_id]
        n = min(self._n_decoys, len(pool))
        picks = self._rng.sample(pool, n)
        return [Target(chain=e.chain, contract=e.contract, token_id=e.token_id,
                       name=e.name, name_onchain=e.name_onchain,
                       description=str(e.metadata.get("description") or "")[:600],
                       image=str(e.metadata.get("image") or ""),
                       metadata_sha256=e.metadata_sha256, epoch=epoch.epoch_id)
                for e in picks]


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
ACT_PAY_NOTED = "pay_noted"        # valid claim matched: pay; note the mutation
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
    trouble. The target read is shuffled into the hunt's SEALED decoy
    batch (decision 4 + P0-A): the same batch on every read, reshuffled in
    order only, so the intersection of all reads is the whole batch. This
    is the "target fixed ⇒ decoys fixed" half of the decoy rule; the judge
    is the other half (candidate varies ⇒ decoys vary)."""

    def __init__(self, *, fetch_metadata_generic,
                 rng: random.Random | None = None):
        self._fetch = fetch_metadata_generic
        self._rng = rng or random.SystemRandom()

    def check(self, sealed: SealedTarget) -> LiveVerdict:
        t = sealed.target
        me = (t.chain, t.contract.lower(), t.token_id)
        batch = sealed.batch_keys()
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
        if result is None:                   # cannot happen: target is in batch
            raise RuntimeError("live check produced no verdict for the target")
        return result


PHASE_PUZZLE = "puzzle"      # clues 1-7
PHASE_REVEAL = "reveal"      # clues 8+
PHASE_CLAIM = "claim"        # a claim was accepted; before payout


def live_policy(status: str, *, phase: str, valid_claim: bool = False) -> str:
    """Proportional response (decision 4). Table, not judgment.

    `valid_claim`: a claim matching the SEALED id already exists. Then a
    mutation/burn never costs the winner the prize (Opus, 06/09): the
    commitment binds identity+metadata so that identity never depends on
    the owner; the player found the right token, a third party's act after
    that is noted in the reveal, verifiably, and the prize is paid."""
    if status == LIVE_INTACT:
        return ACT_CONTINUE
    if status == LIVE_UNAVAILABLE:
        return ACT_HOLD
    # mutated or burned
    if valid_claim:
        return ACT_PAY_NOTED
    return ACT_RELAUNCH if phase == PHASE_PUZZLE else ACT_VOID_REVEAL


class HoldLedger:
    """Freezes the void deadline while we are on HOLD (guard or live check
    unverifiable): every second held is added to the deadline, so a hunt is
    never voided over our own outage. Times are epoch seconds."""

    def __init__(self):
        self._held = 0.0
        self._since: float | None = None

    def start(self, now: float) -> None:
        if self._since is None:
            self._since = now

    def stop(self, now: float) -> None:
        if self._since is not None:
            self._held += max(0.0, now - self._since)
            self._since = None

    def held_seconds(self, now: float) -> float:
        open_span = (now - self._since) if self._since is not None else 0.0
        return self._held + max(0.0, open_span)

    def effective_deadline(self, base_deadline: float, now: float) -> float:
        return base_deadline + self.held_seconds(now)


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
    our history (hunts ran in the tens of participants); R starts at 0.9;
    review both after two hunts of real data."""

    min_total_guesses: int = 200
    min_distinct_ratio: float = 0.9


@dataclass(frozen=True)
class SprayVerdict:
    triggered: bool
    accounts: int
    total_guesses: int
    distinct_targets: int
    distinct_ratio: float
    median_per_account: float          # third signal, outside the trigger

    def render(self) -> str:
        """Operator line — counts only, and NEVER the thresholds."""
        head = "spray: PAUSE for review" if self.triggered else "spray: no"
        return (f"{head} ({self.accounts} accounts, {self.total_guesses} "
                f"guesses, {self.distinct_targets} distinct = "
                f"{self.distinct_ratio:.0%} dedup, median "
                f"{self.median_per_account:.1f}/account)")


class SprayDetector:
    """Evaluate the PUZZLE-PHASE guess log. `guesses` are (account_id,
    target_ref_id) pairs for wrong guesses; the caller filters to the
    puzzle phase. Trigger = total > N AND distinct/total >= R (P0-B). The
    only action this can ever recommend is a pause — there is no void path
    from here, by design."""

    def __init__(self, params: SprayParams = SprayParams()):
        self._p = params

    def evaluate(self, guesses: Iterable[tuple[str, str]]) -> SprayVerdict:
        per_account: dict[str, int] = {}
        targets: set[str] = set()
        total = 0
        for account, ref in guesses:
            per_account[account] = per_account.get(account, 0) + 1
            targets.add(ref)
            total += 1
        accounts = len(per_account)
        median = float(statistics.median(per_account.values())) if accounts else 0.0
        ratio = (len(targets) / total) if total else 0.0
        triggered = (total > self._p.min_total_guesses
                     and ratio >= self._p.min_distinct_ratio)
        return SprayVerdict(triggered=triggered, accounts=accounts,
                            total_guesses=total, distinct_targets=len(targets),
                            distinct_ratio=ratio, median_per_account=median)

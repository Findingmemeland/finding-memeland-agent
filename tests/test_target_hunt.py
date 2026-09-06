"""Target hunt — gate-refused launch, judged draw, sealed target, live check
with decoys + proportional policy, anti-spray by shape (pause, never void)."""

from __future__ import annotations

import random

import pytest

from finding_memeland.target.commitment import verify_commitment_v2
from finding_memeland.target.hunt import (
    ACT_CONTINUE,
    ACT_HOLD,
    ACT_RELAUNCH,
    ACT_VOID_REVEAL,
    LIVE_BURNED,
    LIVE_INTACT,
    LIVE_MUTATED,
    LIVE_UNAVAILABLE,
    PHASE_CLAIM,
    PHASE_PUZZLE,
    PHASE_REVEAL,
    PUBLIC_SPRAY_RULE,
    JudgeVerdict,
    LaunchRefused,
    LiveCheck,
    SealedTarget,
    SealedTargetCipher,
    SealedTargetIntegrityError,
    SprayDetector,
    SprayParams,
    TargetHuntPreparer,
    live_policy,
    select_judged,
    void_reveal_ingredients,
)
from finding_memeland.target.selector import (
    CurationEpoch,
    SelectionRefused,
    Target,
    metadata_hash,
)
from finding_memeland.target.snapshot import Snapshot, SnapshotEntry, SnapshotStore
from finding_memeland.target.sources import ChainUnavailable

EPOCH = CurationEpoch(epoch_id="e1")
NAME = "Whispering Harbor"


def entry(i, platform="foundation"):
    meta = {"name": f"{NAME} {i}", "image": f"ipfs://img{i}"}
    return SnapshotEntry(chain="ethereum", contract=f"0x{i:040x}", token_id=i,
                         name=NAME, name_onchain=f"{NAME} {i}", metadata=meta,
                         metadata_sha256=metadata_hash(meta), platform=platform)


def big_snapshot(built_at="2026-09-05T00:00:00Z"):
    """Three strata, all under the caps, comfortably GREEN."""
    entries = []
    i = 0
    for plat in ("foundation", "superrare2", "makersplace"):
        for _ in range(300):
            i += 1
            entries.append(entry(i, plat))
    return Snapshot(epoch_id="e1", built_at=built_at, entries=entries)


RATES = {"foundation": 300.0, "superrare2": 300.0, "makersplace": 300.0}
# rates >1 are nonsense in production; here they lift 900 entries over the
# 100k floor so the gate logic (not the census) is what's under test


class MemStore:
    def __init__(self):
        self.blob = None

    def read(self):
        return self.blob

    def write(self, b):
        self.blob = b


class XorCipher:
    def encrypt(self, p):
        return p[::-1]

    def decrypt(self, t):
        return t[::-1]


def store_with(snap):
    mem = MemStore()
    st = SnapshotStore(cipher=XorCipher(), read=mem.read, write=mem.write)
    if snap is not None:
        st.save(snap)
    return st


def ok_judge(t):
    return JudgeVerdict(writable=True, content_ok=True)


def preparer(snap, judge=ok_judge, now="2026-09-06T00:00:00Z"):
    return TargetHuntPreparer(snapshot_store=store_with(snap),
                              writability_rates=RATES,
                              cap_exempt=frozenset(), judge=judge,
                              now_iso=lambda: now, rng=random.Random(0))


# --------------------------------------------------------------------------- #
# Prepare                                                                      #
# --------------------------------------------------------------------------- #


def test_prepare_seals_a_target_with_a_verifiable_commitment():
    sealed = preparer(big_snapshot()).prepare(EPOCH)
    assert isinstance(sealed, SealedTarget)
    assert verify_commitment_v2(sealed.id(), sealed.target.metadata_sha256,
                                sealed.salt, sealed.commitment)
    assert sealed.id().startswith("ethereum:")
    assert NAME not in repr(sealed) and "0x" not in repr(sealed)


def test_no_snapshot_refuses_launch():
    with pytest.raises(LaunchRefused):
        preparer(None).prepare(EPOCH)


def test_gate_not_green_refuses_launch_with_telegram_safe_report():
    stale = big_snapshot(built_at="2026-07-01T00:00:00Z")      # > 14 dias
    with pytest.raises(LaunchRefused) as e:
        preparer(stale).prepare(EPOCH)
    msg = str(e.value)
    assert "not GREEN" in msg and "AMBER" in msg
    assert NAME not in msg and "0x0000" not in msg


def test_wrong_epoch_refuses_launch():
    with pytest.raises(LaunchRefused) as e:
        preparer(big_snapshot()).prepare(CurationEpoch(epoch_id="e2"))
    assert "RED" in str(e.value)


def test_judge_needs_both_halves_and_none_rejects():
    verdicts = iter([
        JudgeVerdict(writable=True, content_ok=False),    # arte roubada/NSFW
        JudgeVerdict(writable=False, content_ok=True),    # não escrevível
        None,                                             # juiz inalcançável
        JudgeVerdict(writable=True, content_ok=True),
    ])
    seen = []

    def judge(t):
        seen.append(t.id())
        return next(verdicts)

    sealed = preparer(big_snapshot(), judge=judge).prepare(EPOCH)
    assert len(seen) == 4 and sealed.id() == seen[-1]


def test_judge_exhaustion_refuses():
    with pytest.raises(SelectionRefused):
        preparer(big_snapshot(),
                 judge=lambda t: JudgeVerdict(False, True)).prepare(EPOCH)


def test_relaunch_excludes_the_voided_target():
    p = preparer(big_snapshot())
    first = p.prepare(EPOCH)
    again = preparer(big_snapshot()).prepare(EPOCH,
                                             exclude=frozenset({first.id()}))
    assert again.id() != first.id()


def test_select_judged_skips_excluded_then_accepts():
    from finding_memeland.target.snapshot import snapshot_selector
    snap = big_snapshot()
    sel = snapshot_selector(snap, rng=random.Random(3))
    first = snapshot_selector(snap, rng=random.Random(3)).select(EPOCH)
    t = select_judged(sel, EPOCH, judge=ok_judge,
                      exclude=frozenset({first.id()}))
    assert t.id() != first.id()


# --------------------------------------------------------------------------- #
# Sealed cipher                                                                #
# --------------------------------------------------------------------------- #


def test_sealed_round_trip_is_ciphered_and_verified():
    sealed = preparer(big_snapshot()).prepare(EPOCH)
    c = SealedTargetCipher(cipher=XorCipher())
    blob = c.seal(sealed)
    assert NAME not in blob and sealed.salt not in blob
    back = c.unseal(blob)
    assert back == sealed


def test_sealed_tamper_fails_closed_without_contents():
    sealed = preparer(big_snapshot()).prepare(EPOCH)
    c = SealedTargetCipher(cipher=XorCipher())
    blob = c.seal(sealed)
    tampered = XorCipher().encrypt(
        XorCipher().decrypt(blob).replace(sealed.salt, "0" * 32))
    with pytest.raises(SealedTargetIntegrityError) as e:
        c.unseal(tampered)
    assert NAME not in str(e.value)
    with pytest.raises(SealedTargetIntegrityError):
        c.unseal("garbled")


# --------------------------------------------------------------------------- #
# Live check                                                                   #
# --------------------------------------------------------------------------- #


def sealed_and_pool():
    snap = big_snapshot()
    sealed = preparer(snap).prepare(EPOCH)
    pool = [(e.chain, e.contract, e.token_id) for e in snap.entries]
    return sealed, pool, snap


def test_live_intact_mutated_burned_unavailable():
    sealed, pool, snap = sealed_and_pool()
    by = {(e.chain, e.contract, e.token_id): e.metadata for e in snap.entries}
    t = sealed.target
    me = (t.chain, t.contract, t.token_id)

    def world(mode):
        def fetch(chain, contract, tid):
            key = (chain, contract, tid)
            if key == me:
                if mode == "mutated":
                    return {**by[key], "image": "ipfs://swapped"}
                if mode == "burned":
                    return None
                if mode == "down":
                    raise ChainUnavailable("rpc")
            return by.get(key)
        return fetch

    for mode, status in (("intact", LIVE_INTACT), ("mutated", LIVE_MUTATED),
                         ("burned", LIVE_BURNED), ("down", LIVE_UNAVAILABLE)):
        v = LiveCheck(fetch_metadata_generic=world(mode),
                      rng=random.Random(1)).check(sealed, pool)
        assert v.status == status, mode
        assert t.name not in v.render()
    ok = LiveCheck(fetch_metadata_generic=world("intact"),
                   rng=random.Random(1)).check(sealed, pool)
    assert ok.live_metadata_sha256 == t.metadata_sha256


def test_live_check_reads_target_inside_a_shuffled_decoy_batch():
    sealed, pool, snap = sealed_and_pool()
    t = sealed.target
    me = (t.chain, t.contract, t.token_id)
    reads = []
    by = {(e.chain, e.contract, e.token_id): e.metadata for e in snap.entries}

    def fetch(chain, contract, tid):
        reads.append((chain, contract, tid))
        return by[(chain, contract, tid)]

    positions = set()
    for seed in range(10):
        reads.clear()
        v = LiveCheck(fetch_metadata_generic=fetch, decoys=7,
                      rng=random.Random(seed)).check(sealed, pool)
        assert v.reads == 8 and len(reads) == 8
        assert reads.count(me) == 1
        assert len(set(reads)) == 8                 # decoys distintos
        positions.add(reads.index(me))
    assert len(positions) > 1                       # nunca sempre no fim


def test_decoy_outage_is_noise_not_a_hold():
    sealed, pool, snap = sealed_and_pool()
    t = sealed.target
    by = {(e.chain, e.contract, e.token_id): e.metadata for e in snap.entries}

    def fetch(chain, contract, tid):
        if (chain, contract, tid) != (t.chain, t.contract, t.token_id):
            raise ChainUnavailable("decoy rpc blip")
        return by[(chain, contract, tid)]

    v = LiveCheck(fetch_metadata_generic=fetch, rng=random.Random(2)).check(
        sealed, pool)
    assert v.status == LIVE_INTACT


def test_live_policy_is_proportional():
    assert live_policy(LIVE_INTACT, phase=PHASE_PUZZLE) == ACT_CONTINUE
    assert live_policy(LIVE_UNAVAILABLE, phase=PHASE_CLAIM) == ACT_HOLD
    assert live_policy(LIVE_MUTATED, phase=PHASE_PUZZLE) == ACT_RELAUNCH
    assert live_policy(LIVE_BURNED, phase=PHASE_PUZZLE) == ACT_RELAUNCH
    assert live_policy(LIVE_MUTATED, phase=PHASE_REVEAL) == ACT_VOID_REVEAL
    assert live_policy(LIVE_BURNED, phase=PHASE_CLAIM) == ACT_VOID_REVEAL


def test_void_reveal_publishes_every_ingredient():
    sealed, pool, snap = sealed_and_pool()
    from finding_memeland.target.hunt import LiveVerdict
    ing = void_reveal_ingredients(sealed, LiveVerdict(LIVE_BURNED, None, 8))
    assert ing["target_id"] == sealed.id()
    assert ing["salt"] == sealed.salt and ing["commitment"] == sealed.commitment
    assert ing["metadata_sha256"] == sealed.target.metadata_sha256
    assert ing["live_metadata_sha256"] is None and ing["cause"] == LIVE_BURNED
    assert verify_commitment_v2(ing["target_id"], ing["metadata_sha256"],
                                ing["salt"], ing["commitment"])


# --------------------------------------------------------------------------- #
# Anti-spray                                                                   #
# --------------------------------------------------------------------------- #


def crowd(n_accounts, guesses_each):
    return [(f"acc{a}", f"ethereum:0x{a:040x}:{g}")
            for a in range(n_accounts) for g in range(guesses_each)]


def test_popular_crowd_never_triggers_even_above_n():
    """Larga e rasa: 300 contas a arriscar 1-2 vezes → 450 alvos distintos,
    acima de N — e NÃO dispara, porque a mediana fica em 1-2."""
    guesses = crowd(150, 1) + [(f"b{a}", f"ethereum:0x{a+500:040x}:{g}")
                               for a in range(150) for g in range(2)]
    v = SprayDetector(SprayParams(min_distinct_targets=200)).evaluate(guesses)
    assert v.distinct_targets > 200 and not v.triggered
    assert v.median_per_account < 4


def test_sybil_farm_at_the_cap_triggers():
    """Estreita e funda: 50 contas encostadas ao cap de 5 → 250 distintos,
    mediana 5 → PAUSA."""
    v = SprayDetector(SprayParams(min_distinct_targets=200)).evaluate(crowd(50, 5))
    assert v.triggered and v.median_per_account == 5


def test_deep_but_small_farm_below_n_does_not_trigger():
    v = SprayDetector(SprayParams(min_distinct_targets=200)).evaluate(crowd(20, 5))
    assert not v.triggered                          # 100 distintos < N


def test_spray_verdict_never_prints_thresholds_and_has_no_void():
    from finding_memeland.target import hunt
    v = SprayDetector(SprayParams(min_distinct_targets=200)).evaluate(crowd(50, 5))
    assert "200" not in v.render() and "4.0" not in v.render()
    assert "PAUSE" in v.render()
    assert "void" not in PUBLIC_SPRAY_RULE.lower()
    assert "never cancelled" in PUBLIC_SPRAY_RULE
    assert not hasattr(hunt.SprayDetector, "void")


def test_empty_guess_log_is_quiet():
    v = SprayDetector().evaluate([])
    assert not v.triggered and v.accounts == 0

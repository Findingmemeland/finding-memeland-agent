"""Target hunt — gate-refused launch, judged draw, sealed target, live check
with decoys + proportional policy, anti-spray by shape (pause, never void)."""

from __future__ import annotations

import random

import pytest

from finding_memeland.target.commitment import verify_commitment_v2
from finding_memeland.target.hunt import (
    ACT_CONTINUE,
    ACT_HOLD,
    ACT_PAY_NOTED,
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
    HoldLedger,
    JudgeVerdict,
    LaunchRefused,
    LiveCheck,
    SealedTarget,
    SealedTargetCipher,
    SealedTargetIntegrityError,
    SprayDetector,
    SprayParams,
    TargetHuntPreparer,
    judge_in_batch,
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


def ok_judge(batch):
    return [JudgeVerdict(writable=True, content_ok=True) for _ in batch]


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
    """O juiz vê LOTES (alvo escondido entre decoys); só o veredicto do
    sorteado conta. Aqui o fake devolve o mesmo veredicto a todo o lote."""
    verdicts = iter([
        JudgeVerdict(writable=True, content_ok=False),    # arte roubada/NSFW
        JudgeVerdict(writable=False, content_ok=True),    # não escrevível
        None,                                             # juiz inalcançável
        JudgeVerdict(writable=True, content_ok=True),
    ])
    batches = []

    def judge(batch):
        batches.append([t.id() for t in batch])
        v = next(verdicts)
        return [v for _ in batch]

    sealed = preparer(big_snapshot(), judge=judge).prepare(EPOCH)
    assert len(batches) == 4
    assert all(len(b) == 8 for b in batches)             # 7 decoys + alvo
    assert sealed.id() in batches[-1]
    assert len(sealed.decoys) == 7


def test_judge_batches_share_no_constant_member_across_draws():
    """Regra dos decoys (Opus 06/09, espelho do P0-A): o candidato varia
    entre chamadas ⇒ os decoys têm de variar. Com decoys fixos o membro
    variável do último lote seria o alvo. Aqui: 4 lotes, intersecção
    vazia — nenhum dos 8 se distingue."""
    verdicts = iter([JudgeVerdict(False, True)] * 3 + [JudgeVerdict(True, True)])
    batches = []

    def judge(batch):
        batches.append({t.id() for t in batch})
        v = next(verdicts)
        return [v for _ in batch]

    preparer(big_snapshot(), judge=judge).prepare(EPOCH)
    assert len(batches) == 4
    assert set.intersection(*batches) == set()


def test_sealed_decoys_are_drawn_after_the_target_and_exclude_it():
    sealed = preparer(big_snapshot()).prepare(EPOCH)
    decoy_ids = {f"{d.chain}:{d.contract.lower()}:{d.token_id}" for d in sealed.decoys}
    assert sealed.id() not in decoy_ids and len(decoy_ids) == 7


def test_judge_in_batch_uses_target_position_and_fails_closed_on_bad_shape():
    from finding_memeland.target.snapshot import snapshot_selector
    snap = big_snapshot()
    sel = snapshot_selector(snap, rng=random.Random(5))
    target = sel.select(EPOCH)
    decoys = [snapshot_selector(snap, rng=random.Random(s_)).select(EPOCH)
              for s_ in range(20, 24)]
    decoys = [d for d in decoys if d.id() != target.id()]

    def judge(batch):                          # reprova tudo excepto o alvo
        return [JudgeVerdict(t.id() == target.id(), True) for t in batch]
    v = judge_in_batch(judge, target, decoys, random.Random(0))
    assert v is not None and v.writable
    assert judge_in_batch(lambda b: [JudgeVerdict(True, True)], target, decoys,
                          random.Random(0)) is None          # tamanho errado
    assert judge_in_batch(lambda b: 1 / 0, target, decoys,
                          random.Random(0)) is None          # juiz rebenta


def test_judge_exhaustion_refuses():
    with pytest.raises(SelectionRefused):
        preparer(big_snapshot(),
                 judge=lambda b: [JudgeVerdict(False, True) for _ in b]
                 ).prepare(EPOCH)


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
    t = select_judged(sel, EPOCH, judge=ok_judge, draw_decoys=lambda tid: [],
                      rng=random.Random(0), exclude=frozenset({first.id()}))
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
    assert back.decoys == sealed.decoys and len(back.decoys) == 7


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
                      rng=random.Random(1)).check(sealed)
        assert v.status == status, mode
        assert t.name not in v.render()
    ok = LiveCheck(fetch_metadata_generic=world("intact"),
                   rng=random.Random(1)).check(sealed)
    assert ok.live_metadata_sha256 == t.metadata_sha256


def test_live_check_reads_the_same_sealed_batch_every_time():
    """P0-A: o lote é fixo por hunt (só a ordem muda) — a intersecção de
    todas as leituras é o lote inteiro, nunca o alvo sozinho."""
    sealed, pool, snap = sealed_and_pool()
    t = sealed.target
    me = (t.chain, t.contract, t.token_id)
    reads = []
    by = {(e.chain, e.contract, e.token_id): e.metadata for e in snap.entries}

    def fetch(chain, contract, tid):
        reads.append((chain, contract, tid))
        return by[(chain, contract, tid)]

    positions, sets = set(), []
    for seed in range(10):
        reads.clear()
        v = LiveCheck(fetch_metadata_generic=fetch,
                      rng=random.Random(seed)).check(sealed)
        assert v.reads == 8 and len(reads) == 8
        assert reads.count(me) == 1
        positions.add(reads.index(me))
        sets.append(frozenset(reads))
    assert len(positions) > 1                       # ordem embaralhada
    assert len(set(sets)) == 1                      # MESMO conjunto sempre
    intersection = frozenset.intersection(*sets)
    assert len(intersection) == 8                   # intersecção = lote


def test_decoy_outage_is_noise_not_a_hold():
    sealed, pool, snap = sealed_and_pool()
    t = sealed.target
    by = {(e.chain, e.contract, e.token_id): e.metadata for e in snap.entries}

    def fetch(chain, contract, tid):
        if (chain, contract, tid) != (t.chain, t.contract, t.token_id):
            raise ChainUnavailable("decoy rpc blip")
        return by[(chain, contract, tid)]

    v = LiveCheck(fetch_metadata_generic=fetch, rng=random.Random(2)).check(sealed)
    assert v.status == LIVE_INTACT


def test_live_policy_is_proportional_and_never_punishes_the_winner():
    assert live_policy(LIVE_INTACT, phase=PHASE_PUZZLE) == ACT_CONTINUE
    assert live_policy(LIVE_UNAVAILABLE, phase=PHASE_CLAIM) == ACT_HOLD
    assert live_policy(LIVE_MUTATED, phase=PHASE_PUZZLE) == ACT_RELAUNCH
    assert live_policy(LIVE_BURNED, phase=PHASE_PUZZLE) == ACT_RELAUNCH
    assert live_policy(LIVE_MUTATED, phase=PHASE_REVEAL) == ACT_VOID_REVEAL
    assert live_policy(LIVE_BURNED, phase=PHASE_CLAIM) == ACT_VOID_REVEAL
    # claim válido já batido: paga-se, a mutação vai como nota no reveal
    assert live_policy(LIVE_MUTATED, phase=PHASE_CLAIM, valid_claim=True) == ACT_PAY_NOTED
    assert live_policy(LIVE_BURNED, phase=PHASE_CLAIM, valid_claim=True) == ACT_PAY_NOTED
    assert live_policy(LIVE_UNAVAILABLE, phase=PHASE_CLAIM, valid_claim=True) == ACT_HOLD


def test_hold_ledger_freezes_the_void_deadline():
    led = HoldLedger()
    base = 1_000.0
    assert led.effective_deadline(base, now=100.0) == base
    led.start(now=100.0)
    assert led.effective_deadline(base, now=160.0) == base + 60   # em hold
    led.stop(now=200.0)
    assert led.effective_deadline(base, now=500.0) == base + 100  # congelado 100s
    led.start(now=600.0)
    led.start(now=650.0)                       # idempotente
    led.stop(now=700.0)
    assert led.held_seconds(now=900.0) == 200


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


def crowd(n_accounts, guesses_each, *, distinct=True):
    """distinct=True: cada palpite é um alvo novo (enumeração coordenada);
    False: toda a gente arrisca os mesmos 20 nomes óbvios (multidão)."""
    out = []
    for a in range(n_accounts):
        for g in range(guesses_each):
            ref = (f"ethereum:0x{a:040x}:{g}" if distinct
                   else f"ethereum:0x{'ab' * 20}:{(a * 7 + g) % 20}")
            out.append((f"acc{a}", ref))
    return out


P = SprayParams(min_total_guesses=200, min_distinct_ratio=0.9)


def test_popular_crowd_never_triggers_even_far_above_n():
    """Multidão honesta converge nos mesmos nomes: 600 palpites, 20 alvos
    distintos → rácio 3%. Não dispara, por mais popular que seja."""
    v = SprayDetector(P).evaluate(crowd(300, 2, distinct=False))
    assert v.total_guesses == 600 and v.distinct_targets == 20
    assert not v.triggered


def test_spread_farm_that_evaded_the_median_now_triggers():
    """P0-B: 250 contas × 2 palpites, todos distintos — mediana 2 (evadia o
    critério antigo a custo zero); rácio 100% → PAUSA."""
    v = SprayDetector(P).evaluate(crowd(250, 2))
    assert v.median_per_account == 2 and v.distinct_ratio == 1.0
    assert v.triggered


def test_deep_farm_at_the_cap_also_triggers():
    v = SprayDetector(P).evaluate(crowd(50, 5))
    assert v.triggered


def test_farm_that_burns_budget_on_duplicates_does_not_trigger():
    """Baixar o rácio custa palpites repetidos — o orçamento que queremos
    queimar. 250 distintos + 100 duplicados = 350 palpites, rácio 71%."""
    dups = [(f"dup{i}", "ethereum:0x" + "cd" * 20 + ":1") for i in range(100)]
    v = SprayDetector(P).evaluate(crowd(250, 1) + dups)
    assert v.total_guesses == 350 and not v.triggered


def test_small_enumeration_below_n_does_not_trigger():
    v = SprayDetector(P).evaluate(crowd(60, 3))        # 180 < N
    assert v.distinct_ratio == 1.0 and not v.triggered


def test_spray_verdict_never_prints_thresholds_and_has_no_void():
    from finding_memeland.target import hunt
    v = SprayDetector(P).evaluate(crowd(250, 2))
    out = v.render()
    assert "200" not in out and "0.9" not in out and "90%" not in out.replace("100%", "")
    assert "PAUSE" in out and "median" in out          # mediana: 3º sinal
    assert "void" not in PUBLIC_SPRAY_RULE.lower()
    assert "never cancelled" in PUBLIC_SPRAY_RULE
    assert not hasattr(hunt.SprayDetector, "void")


def test_empty_guess_log_is_quiet():
    v = SprayDetector().evaluate([])
    assert not v.triggered and v.accounts == 0

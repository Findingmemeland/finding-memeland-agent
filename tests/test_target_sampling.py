"""/snapshot by sampling, retries with backoff, configurable gate (13/09).

The first live refresh listed 847,229 tokens, ran ~40 h and died to
transport (453k reads lost: 429s under the free-tier throughput, then the
spend cap). What changed: listers can draw a UNIFORM sample per stratum;
reads retry with backoff before counting as transport and run on a small
worker pool; the gate's numbers are a GateThresholds object fed from
config (defaults = ratified) and printed when non-default; progress lines
(counts only) reach the operator."""

from __future__ import annotations

import random

import pytest

from finding_memeland.target.refresh import FakeLister, RefreshJob
from finding_memeland.target.snapshot import GateThresholds, stratum_gate
from finding_memeland.target.sources import (
    ChainContractLister,
    ChainUnavailable,
    ContractRegistry,
    RegistryStratumLister,
    epoch1_listers,
)
from test_target_refresh import EPOCH, item, meta_for, token
from test_target_snapshot import UNIQ, snap_with_strata
from test_target_sources import make_rpc

WORDS = ["amber", "quiet", "salt", "velvet", "iron", "pale", "glass", "hollow",
         "silver", "wild"]


def distinct(i: int) -> str:
    """A base name per index that survives normalization AND the in-pool
    dedupe (a trailing serial would be stripped and collide)."""
    return f"{WORDS[i % 10]} {WORDS[(i // 10) % 10]} meridian {WORDS[(i // 100) % 10]}"


# ------------------------------- listers ----------------------------------- #


def test_enumerable_contract_samples_distinct_random_indices():
    ids = list(range(1000, 1100))                     # 100 tokens
    calls = []
    rpc = make_rpc(supply=100, ids=ids)
    inner = rpc.eth_call

    def counting(to, data):
        calls.append(data[:10])
        return inner(to, data)
    rpc = type(rpc)(chain=rpc.chain, eth_call=counting, get_code=rpc.get_code)
    got = [i.token_id for i in ChainContractLister(
        rpc=rpc, platform="foundation", contract="0xF", sample=10,
        rng=random.Random(7)).items()]
    assert len(got) == 10 and len(set(got)) == 10
    assert set(got) <= set(ids)
    assert calls.count("0x4f6ccce7") == 10           # ten tokenByIndex, not 100


def test_sample_larger_than_supply_lists_everything():
    rpc = make_rpc(supply=3, ids=[7, 9, 42])
    got = [i.token_id for i in ChainContractLister(
        rpc=rpc, platform="foundation", contract="0xF", sample=50).items()]
    assert got == [7, 9, 42]


def test_dense_contract_without_enumeration_samples_under_a_found_bound():
    existing = set(range(1, 501))                     # dense 1..500
    rpc = make_rpc(supply=None, existing=existing)
    got = [i.token_id for i in ChainContractLister(
        rpc=rpc, platform="tail2021", contract="0xT", sample=20,
        probe_miss_budget=3, rng=random.Random(3)).items()]
    assert len(got) == 20 and len(set(got)) == 20 and set(got) <= existing
    assert max(got) > 50                              # not just the low ids


def test_sampled_probe_gives_up_on_a_sparse_contract_within_budget():
    rpc = make_rpc(supply=None, existing={1, 2, 3})
    got = [i.token_id for i in ChainContractLister(
        rpc=rpc, platform="tail2021", contract="0xT", sample=20,
        probe_miss_budget=2, rng=random.Random(1)).items()]
    assert set(got) == {1, 2, 3}                      # bound ≤ sample → plain probe


def test_registry_stratum_spreads_the_budget_over_contracts():
    reg = ContractRegistry()
    reg.add("tail2021", ["0xa", "0xb"], chain="ethereum")
    rpc = make_rpc(supply=50, ids=list(range(50)))
    lister = RegistryStratumLister(rpcs={"ethereum": rpc}, stratum="tail2021",
                                   registry=reg, sample=10,
                                   rng=random.Random(2))
    got = list(lister.items())
    assert len(got) == 10                             # 5 per contract
    assert {i.contract for i in got} == {"0xa", "0xb"}


def test_epoch1_listers_accept_a_sample_and_default_to_full():
    reg = ContractRegistry()
    rpc = make_rpc(supply=2, ids=[1, 2])
    full = epoch1_listers(rpcs={"ethereum": rpc}, registry=reg)
    sampled = epoch1_listers(rpcs={"ethereum": rpc}, registry=reg, sample=5)
    assert len(full) == len(sampled) == 6
    assert all(lst._sample == 0 for lst in full)
    assert all(lst._sample == 5 for lst in sampled)


# ------------------------------- refresh ----------------------------------- #


def test_retries_absorb_a_rate_limit_storm_and_workers_keep_the_result():
    names = {i: distinct(i) for i in range(1, 41)}
    items = [item(i, names[i]) for i in range(1, 41)]
    metas = {i: meta_for(names[i]) for i in range(1, 41)}
    flaky = {i: 2 for i in range(1, 41)}              # fails twice, then serves
    notes = []

    def fetch(ch, c, t):
        if flaky[t] > 0:
            flaky[t] -= 1
            raise ChainUnavailable("429")
        return token(metas[t])
    job = RefreshJob(listers=(FakeLister("plat", items),), fetch_token=fetch,
                     owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t",
                     workers=4, retries=3, backoff_s=0.0, pause_s=0.0,
                     progress=notes.append, progress_every=10)
    snap, report = job.build(EPOCH)
    assert snap.size() == 40 and report.transport == 0
    assert any("listado" in n for n in notes) and any("lidos" in n for n in notes)
    assert not any("0x" in n for n in notes)          # counts only, never ids


def test_without_retries_a_storm_still_trips_the_transport_rule():
    names = {i: distinct(i) for i in range(1, 101)}
    items = [item(i, names[i]) for i in range(1, 101)]

    def fetch(ch, c, t):
        raise ChainUnavailable("429")
    job = RefreshJob(listers=(FakeLister("plat", items),), fetch_token=fetch,
                     owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t",
                     workers=4, retries=0)
    from finding_memeland.target.refresh import RefreshFailed
    with pytest.raises(RefreshFailed):
        job.build(EPOCH)


def test_owner_check_exception_counts_as_unverifiable_not_a_crash():
    names = {i: distinct(i) for i in range(1, 6)}
    items = [item(i, names[i]) for i in range(1, 6)]
    metas = {i: meta_for(names[i]) for i in range(1, 6)}

    def eoa(ch, c, t):
        if t == 3:
            raise RuntimeError("boom")
        return True
    job = RefreshJob(listers=(FakeLister("plat", items),),
                     fetch_token=lambda ch, c, t: token(metas[t]),
                     owner_is_eoa=eoa, now_iso=lambda: "t", workers=2)
    snap, report = job.build(EPOCH)
    assert snap.size() == 4 and report.unverifiable == 1


def test_transport_ceiling_trips_within_one_chunk_not_at_the_end():
    """Opus P1-1: 10,000 items, every read an outage — the build must fail
    inside ONE chunk, not after all 10,000 (with 13 s of backoff each that
    was ~54 h; the 10/09 run died at 40 h). min_transport=20 default, so
    the zero-served breaker fires first; the chunk ceiling is the backstop
    for a PARTIAL outage."""
    from finding_memeland.target.refresh import RefreshFailed
    names = {i: distinct(i) for i in range(1, 10_001)}
    items = [item(i, names[i]) for i in range(1, 10_001)]
    calls = []

    def fetch(ch, c, t):
        calls.append(t)
        raise ChainUnavailable("gateway 503")
    job = RefreshJob(listers=(FakeLister("plat", items),), fetch_token=fetch,
                     owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t",
                     workers=4, retries=0, chunk=500)
    with pytest.raises(RefreshFailed) as e:
        job.build(EPOCH)
    assert len(calls) <= 500 and "stopped after" in str(e.value)


def test_zero_served_breaker_stops_a_dead_refresh_inside_the_first_chunk():
    """Opus, volta 2: with retries and the shared pause, a total outage
    could spend up to 4 × 60 s per item before the chunk ceiling ran — a
    day for the first chunk. Once min_transport reads failed and none was
    served, stop at once."""
    from finding_memeland.target.refresh import RefreshFailed
    names = {i: distinct(i) for i in range(1, 5_001)}
    items = [item(i, names[i]) for i in range(1, 5_001)]
    calls = []

    def fetch(ch, c, t):
        calls.append(t)
        raise ChainUnavailable("rpc: throttled")
    job = RefreshJob(listers=(FakeLister("plat", items),), fetch_token=fetch,
                     owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t",
                     workers=4, retries=3, backoff_s=0.0, pause_s=0.0,
                     min_transport_failures=20)
    with pytest.raises(RefreshFailed) as e:
        job.build(EPOCH)
    assert "NONE served" in str(e.value)
    assert len(calls) <= 250 * 4                   # one chunk (default 250) × tries
    assert job._chunk == 250


def test_transport_failures_are_tallied_by_cause_without_ids():
    """13/09 live: 162 of 500 reads lost and the report said only
    'transport'. The tally names the provider and the HTTP status —
    never a URL, CID, contract or token id."""
    from finding_memeland.target.refresh import RefreshFailed, _transport_cause
    names = {i: distinct(i) for i in range(1, 201)}
    items = [item(i, names[i]) for i in range(1, 201)]
    metas = {i: meta_for(names[i]) for i in range(1, 201)}

    class Http(Exception):
        def __init__(self, code):
            super().__init__(f"HTTP Error {code}")
            self.code = code

    def fetch(ch, c, t):
        if t % 3 == 0:
            try:
                raise Http(429)
            except Http as inner:
                raise ChainUnavailable("gateway: HTTPError") from inner
        if t % 7 == 0:
            raise ChainUnavailable("gateway answered non-JSON")
        return token(metas[t])
    job = RefreshJob(listers=(FakeLister("plat", items),), fetch_token=fetch,
                     owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t",
                     workers=2, retries=0)
    with pytest.raises(RefreshFailed) as e:
        job.build(EPOCH)
    msg = str(e.value)
    assert "causes:" in msg and "gateway HTTP 429" in msg
    assert "0x" not in msg and "ipfs" not in msg
    assert _transport_cause(ChainUnavailable("rpc:ethereum: throttled")) == "rpc:ethereum throttled"


def test_rate_limit_opens_one_shared_pause_instead_of_faster_retries():
    """Opus P1-2: a 429 is the provider asking for LESS traffic. The first
    worker to see it pauses everyone; the item is retried after the pause,
    never immediately."""
    import time
    names = {i: distinct(i) for i in range(1, 21)}
    items = [item(i, names[i]) for i in range(1, 21)]
    metas = {i: meta_for(names[i]) for i in range(1, 21)}
    state = {"throttle": True, "stamps": []}

    def fetch(ch, c, t):
        state["stamps"].append(time.monotonic())
        if state["throttle"]:
            state["throttle"] = False
            raise ChainUnavailable("rpc: throttled")
        return token(metas[t])
    job = RefreshJob(listers=(FakeLister("plat", items),), fetch_token=fetch,
                     owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t",
                     workers=4, retries=2, backoff_s=0.0, pause_s=0.3)
    t0 = time.monotonic()
    snap, report = job.build(EPOCH)
    assert snap.size() == 20 and report.transport == 0
    assert time.monotonic() - t0 >= 0.3            # everyone waited the pause
    assert job._pause_len == 0.0                   # cleared once served


def test_is_rate_limited_reads_the_cause_chain():
    from finding_memeland.target.refresh import _is_rate_limited

    class Http(Exception):
        code = 429
    try:
        try:
            raise Http("too many")
        except Http as inner:
            raise ChainUnavailable("gw: HTTPError") from inner
    except ChainUnavailable as e:
        assert _is_rate_limited(e)
    assert _is_rate_limited(ChainUnavailable("rpc:ethereum: throttled"))
    assert not _is_rate_limited(ChainUnavailable("gateway 503"))


def test_unreadable_token_is_counted_and_skipped_with_its_own_ceiling():
    """Opus P1-3: a read that raises something that is NOT transport is a
    strange token, not an outage — counted, skipped; a flood of them is a
    parser problem and fails the build by its own rule."""
    from finding_memeland.target.refresh import RefreshFailed
    names = {i: distinct(i) for i in range(1, 101)}
    items = [item(i, names[i]) for i in range(1, 101)]
    metas = {i: meta_for(names[i]) for i in range(1, 101)}

    def fetch(ch, c, t):
        if t in (5, 9):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "strange")
        return token(metas[t])
    job = RefreshJob(listers=(FakeLister("plat", items),), fetch_token=fetch,
                     owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t", workers=2)
    snap, report = job.build(EPOCH)
    assert snap.size() == 98 and report.bad_read == 2 and report.transport == 0

    def flood(ch, c, t):
        raise ValueError("garbage")
    job = RefreshJob(listers=(FakeLister("plat", items),), fetch_token=flood,
                     owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t")
    with pytest.raises(RefreshFailed) as e:
        job.build(EPOCH)
    assert "unreadable" in str(e.value)


def test_report_carries_gross_size_per_stratum_next_to_the_sample():
    """Opus, answer 2: a sampled snapshot must read '15k of 115k', never
    'the stratum collapsed to 15k'."""
    rpc = make_rpc(supply=1000, ids=list(range(1000)))
    lister = ChainContractLister(rpc=rpc, platform="foundation", contract="0xF",
                                 sample=10, rng=random.Random(1))
    names = {i: distinct(i) for i in range(1000)}
    job = RefreshJob(listers=(lister,),
                     fetch_token=lambda ch, c, t: token(meta_for(names[t])),
                     owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t")
    snap, report = job.build(EPOCH)
    assert report.gross == {"foundation": 1000} and snap.size() == 10


# -------------------------------- gate ------------------------------------- #


def test_thresholds_refuse_a_red_band_above_green():
    """Opus P1-4: green_min=200 with red_max=500 (a zero missing) made a
    300-effective pool GREEN in silence. Now it cannot be constructed."""
    with pytest.raises(ValueError):
        GateThresholds(green_min=200, red_max=500)
    with pytest.raises(ValueError):
        GateThresholds(max_share=0.9, hard_share=0.5)
    with pytest.raises(ValueError):
        GateThresholds(hard_share=1.5)
    with pytest.raises(ValueError):
        GateThresholds(red_max=0, green_min=10)
    GateThresholds(green_min=2000, red_max=500, max_share=1.0, hard_share=1.0)


def test_transport_share_is_configurable_for_sampled_mode():
    """14/09 live: 33 of 500 reads lost (6.6%) on the public gateway, and
    the 2% rule — written for the full listing — aborted a 1,000-token
    sample. The share is config; the pipeline hands it to the job."""
    from test_target_wiring import build, settings
    w = build(settings(target_refresh_max_transport_share=0.25))
    assert w.pipeline._max_transport_share == 0.25
    names = {i: distinct(i) for i in range(1, 501)}
    items = [item(i, names[i]) for i in range(1, 501)]
    metas = {i: meta_for(names[i]) for i in range(1, 501)}

    def fetch(ch, c, t):
        if t % 15 == 0:                               # ~6.6% lost
            raise ChainUnavailable("gateway: HTTPError")
        return token(metas[t])
    strict = RefreshJob(listers=(FakeLister("plat", items),), fetch_token=fetch,
                        owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t")
    from finding_memeland.target.refresh import RefreshFailed
    with pytest.raises(RefreshFailed):
        strict.build(EPOCH)
    lenient = RefreshJob(listers=(FakeLister("plat", items),), fetch_token=fetch,
                         owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t",
                         max_transport_share=0.25)
    snap, report = lenient.build(EPOCH)
    assert snap.size() == 467 and report.transport == 33


def test_launch_and_status_read_the_same_thresholds():
    from test_target_wiring import build, settings
    w = build(settings(target_gate_green_min=2000, target_gate_red_max=500))
    assert w.thresholds is w.pipeline._thresholds
    assert w.ports.preparer._thresholds is w.thresholds     # /launch == /status


def test_default_thresholds_are_the_ratified_numbers_and_stay_silent():
    t = GateThresholds()
    assert (t.green_min, t.red_max, t.max_share, t.hard_share) == (100_000, 20_000, 0.40, 0.70)
    s = snap_with_strata({"foundation": 200_000, "tail": 400_000})
    rep = stratum_gate(s, {"foundation": 0.5, "tail": 0.5}, uniqueness_rates=UNIQ,
                       cap_exempt=frozenset({"tail"}))
    assert rep.verdict == "GREEN" and "[gate:" not in rep.detail


def test_custom_thresholds_let_a_single_stratum_test_pool_go_green_and_say_so():
    """Pedro, 13/09: a test hunt over a Foundation-only sample. The numbers
    are config, not a code change, and the verdict prints them."""
    s = snap_with_strata({"foundation": 8_000})
    t = GateThresholds(green_min=2_000, red_max=500, max_share=1.0, hard_share=1.0)
    rep = stratum_gate(s, {"foundation": 0.52}, uniqueness_rates={"foundation": 0.66},
                       thresholds=t)
    assert rep.verdict == "GREEN"
    assert "[gate: green ≥2,000" in rep.detail
    # the same pool under the ratified numbers is RED (and capped)
    rep2 = stratum_gate(s, {"foundation": 0.52}, uniqueness_rates={"foundation": 0.66})
    assert rep2.verdict == "RED"


def test_custom_hard_share_below_one_still_bites():
    s = snap_with_strata({"foundation": 8_000, "superrare": 100})
    t = GateThresholds(green_min=1_000, red_max=100, max_share=0.9, hard_share=0.95)
    rep = stratum_gate(s, {"foundation": 0.5, "superrare": 0.5},
                       uniqueness_rates=UNIQ, thresholds=t)
    assert rep.verdict == "AMBER" and "HARD" in rep.detail


# ------------------------------- config ------------------------------------ #


def test_settings_expose_thresholds_sample_and_exemption():
    from test_target_wiring import settings
    s = settings(target_gate_green_min=2000, target_gate_red_max=500,
                 target_gate_max_share=1.0, target_gate_hard_share=1.0,
                 target_cap_exempt="foundation,tail2021",
                 target_sample_per_stratum=15000)
    t = s.target_gate_thresholds
    assert (t.green_min, t.red_max, t.max_share, t.hard_share) == (2000, 500, 1.0, 1.0)
    assert s.target_cap_exempt_set == {"foundation", "tail2021"}
    assert s.target_sample_per_stratum == 15000
    d = settings().target_gate_thresholds
    assert d == GateThresholds() and settings().target_cap_exempt_set == {"tail2021"}


def test_wiring_carries_the_config_and_refuses_an_unknown_exempt_stratum():
    from test_target_wiring import build, settings
    w = build(settings(target_gate_green_min=2000, target_gate_red_max=500,
                       target_cap_exempt="foundation", target_sample_per_stratum=15000))
    assert w.thresholds.green_min == 2000
    assert w.cap_exempt == {"foundation"} and w.sample_per_stratum == 15000
    assert w.pipeline._sample == 15000 and w.pipeline._thresholds.green_min == 2000
    with pytest.raises(RuntimeError) as e:
        build(settings(target_cap_exempt="fundation"))
    assert "fundation" in str(e.value)
    # a green_min alone (red_max left at the ratified 20,000) is the P1-4
    # misconfiguration — refused at boot, loudly
    with pytest.raises(RuntimeError) as e:
        build(settings(target_gate_green_min=2000))
    assert "gate config refused" in str(e.value) and "red_max" in str(e.value)

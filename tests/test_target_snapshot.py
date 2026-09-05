"""Snapshot pool — persistence, drawing, lazy writability, the gate."""

from __future__ import annotations

import random

import pytest

from finding_memeland.target.selector import CurationEpoch, SelectionRefused
from finding_memeland.target.snapshot import (
    GateVerdict,
    Snapshot,
    SnapshotEntry,
    SnapshotIntegrityError,
    SnapshotSource,
    SnapshotStore,
    gate_verdict,
    select_writable,
    snapshot_selector,
)
from finding_memeland.target.selector import metadata_hash

EPOCH = CurationEpoch(epoch_id="e1")


def entry(i: int, name: str = "Whispering Harbor",
          chain: str = "ethereum") -> SnapshotEntry:
    meta = {"name": f"{name} #{i}", "image": f"ipfs://img{i}",
            "description": "quiet"}
    return SnapshotEntry(
        chain=chain, contract=f"0x{i:040x}", token_id=i, name=name,
        name_onchain=f"{name} #{i}", metadata=meta,
        metadata_sha256=metadata_hash(meta), platform="testplat",
    )


def snap(n: int = 5) -> Snapshot:
    return Snapshot(epoch_id="e1", built_at="2026-09-04T00:00:00Z",
                    entries=[entry(i) for i in range(1, n + 1)])


class MemStore:
    def __init__(self):
        self.blob: str | None = None

    def read(self):
        return self.blob

    def write(self, blob: str):
        self.blob = blob


class XorCipher:
    """Not-crypto stand-in proving save/load round-trips THROUGH the cipher."""

    def encrypt(self, plaintext: str) -> str:
        return plaintext[::-1]

    def decrypt(self, token: str) -> str:
        return token[::-1]


def make_store(mem: MemStore) -> SnapshotStore:
    return SnapshotStore(cipher=XorCipher(), read=mem.read, write=mem.write)


# --------------------------------------------------------------------------- #
# Store                                                                        #
# --------------------------------------------------------------------------- #


def test_save_load_round_trip():
    mem = MemStore()
    store = make_store(mem)
    store.save(snap(3))
    assert mem.blob is not None and "Whispering" not in mem.blob  # ciphered
    loaded = store.load()
    assert loaded.size() == 3
    assert loaded.epoch_id == "e1"
    assert loaded.entries[0].name == "Whispering Harbor"


def test_load_missing_returns_none():
    assert make_store(MemStore()).load() is None


def test_tampered_entry_hash_fails_closed():
    mem = MemStore()
    store = make_store(mem)
    s = snap(1)
    object.__setattr__(s.entries[0], "metadata_sha256", "0" * 64)
    store.save(s)
    with pytest.raises(SnapshotIntegrityError) as e:
        store.load()
    assert "Whispering" not in str(e.value)      # never names an entry


def test_garbled_blob_fails_closed():
    mem = MemStore()
    mem.blob = "not-a-snapshot"
    with pytest.raises(SnapshotIntegrityError):
        make_store(mem).load()


def test_chain_round_trips_through_store():
    mem = MemStore()
    store = make_store(mem)
    s = Snapshot(epoch_id="e1", built_at="2026-09-04T00:00:00Z",
                 entries=[entry(1, chain="ethereum"),
                          entry(2, "Salt Harbor", chain="base")])
    store.save(s)
    loaded = store.load()
    assert [e.chain for e in loaded.entries] == ["ethereum", "base"]


def test_chainless_v1_payload_fails_closed():
    """Um store de antes do fix multi-chain não tem 'chain' — recusar e
    reconstruir, nunca adivinhar uma cadeia para dentro do compromisso."""
    import json
    mem = MemStore()
    store = make_store(mem)
    s = snap(1)
    store.save(s)
    doc = json.loads(XorCipher().decrypt(mem.blob))
    for e in doc["entries"]:
        del e["chain"]
    mem.blob = XorCipher().encrypt(json.dumps(doc, ensure_ascii=False))
    with pytest.raises(SnapshotIntegrityError):
        store.load()


# --------------------------------------------------------------------------- #
# Drawing                                                                      #
# --------------------------------------------------------------------------- #


def test_epoch_mismatch_refuses():
    src = SnapshotSource(snap(3))
    with pytest.raises(SelectionRefused) as e:
        next(src.candidates(CurationEpoch(epoch_id="e2")))
    assert "refresh the snapshot" in str(e.value)


def test_snapshot_selector_draws_offline_and_uniformly():
    s = snap(20)
    seen = set()
    for seed in range(30):
        sel = snapshot_selector(s, rng=random.Random(seed))
        seen.add(sel.select(EPOCH).token_id)
    assert len(seen) > 5              # different seeds, different picks
    t = snapshot_selector(s, rng=random.Random(1)).select(EPOCH)
    assert t.name == "Whispering Harbor"          # base name from the store
    assert t.metadata_sha256 == s.entries[t.token_id - 1].metadata_sha256
    assert t.chain == "ethereum"                  # da ENTRADA, não constante
    assert t.id().startswith("ethereum:")


def test_target_chain_is_per_entry_two_chains_same_pair():
    """P0-1 (revisão Opus 05/09): o mesmo contract:tokenId em duas cadeias
    são dois candidatos distintos, e Target.id() sela a cadeia da entrada —
    uma constante teria selado 'base:' para uma peça de Ethereum e recusado
    o vencedor legítimo."""
    e_eth = entry(1, "North Signal", chain="ethereum")
    e_base = SnapshotEntry(
        chain="base", contract=e_eth.contract, token_id=e_eth.token_id,
        name="South Signal", name_onchain="South Signal #1",
        metadata={"name": "South Signal #1", "image": "ipfs://b"},
        metadata_sha256=metadata_hash(
            {"name": "South Signal #1", "image": "ipfs://b"}),
        platform="baseplat")
    s = Snapshot(epoch_id="e1", built_at="2026-09-04T00:00:00Z",
                 entries=[e_eth, e_base])
    ids = set()
    for seed in range(20):
        t = snapshot_selector(s, rng=random.Random(seed)).select(EPOCH)
        ids.add(t.id())
        # o nome tem de ser o da entrada DA MESMA cadeia (índice por triplo)
        assert t.name == ("North Signal" if t.chain == "ethereum"
                          else "South Signal")
    assert ids == {f"ethereum:{e_eth.contract}:1", f"base:{e_eth.contract}:1"}


def test_select_writable_skips_unwritable_draws():
    s = snap(10)
    sel = snapshot_selector(s, rng=random.Random(7))
    verdicts = iter([False, None, True])
    picked = select_writable(sel, EPOCH,
                             is_writable=lambda t: next(verdicts))
    assert picked.name == "Whispering Harbor"


def test_select_writable_exhaustion_refuses():
    sel = snapshot_selector(snap(10), rng=random.Random(7))
    with pytest.raises(SelectionRefused) as e:
        select_writable(sel, EPOCH, is_writable=lambda t: False, max_draws=3)
    assert "re-measure" in str(e.value)


# --------------------------------------------------------------------------- #
# Gate                                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("pool,rate,verdict", [
    (500_000, 0.22, "GREEN"),        # 110k effective
    (200_000, 0.05, "RED"),          # 10k
    (300_000, 0.20, "AMBER"),        # 60k
    (100_000, 1.0, "GREEN"),         # exactly the floor
    (20_000, 1.0, "RED"),            # exactly the ceiling
])
def test_gate_thresholds(pool, rate, verdict):
    v = gate_verdict(pool, rate)
    assert isinstance(v, GateVerdict)
    assert v.verdict == verdict


def test_gate_red_detail_carries_the_anticircularity_rule():
    assert "never loosen quality filters" in gate_verdict(10_000, 0.5).detail


# --------------------------------------------------------------------------- #
# Stratum counter (the definitive gate)                                        #
# --------------------------------------------------------------------------- #


def snap_with_strata(counts: dict[str, int]) -> Snapshot:
    entries = []
    i = 0
    for platform, n in counts.items():
        for _ in range(n):
            i += 1
            e = entry(i)
            object.__setattr__(e, "platform", platform)
            entries.append(e)
    return Snapshot(epoch_id="e1", built_at="t", entries=entries)


def test_stratum_gate_green_and_per_stratum_rows():
    from finding_memeland.target.snapshot import stratum_gate
    s = snap_with_strata({"foundation": 60_000, "superrare": 50_000,
                          "tail": 200_000})
    # a cauda domina (56%) mas é isenta do tecto: não-enumerável por
    # terceiros (racional do Opus 05/09) — isenção é configuração de época
    rep = stratum_gate(s, {"foundation": 0.5, "superrare": 0.7, "tail": 0.35},
                       cap_exempt=frozenset({"tail"}))
    assert rep.verdict == "GREEN"
    by = {r.stratum: r for r in rep.rows}
    assert by["foundation"].effective == 30_000
    assert by["tail"].effective == 70_000
    assert rep.total_effective == 135_000
    assert abs(by["tail"].share - 70_000 / 135_000) < 1e-6
    assert "foundation" in rep.render() and "GREEN" in rep.render()


def test_stratum_gate_concentration_cap_bites():
    from finding_memeland.target.snapshot import stratum_gate
    s = snap_with_strata({"foundation": 300_000, "tail": 40_000})
    rep = stratum_gate(s, {"foundation": 0.5, "tail": 0.5})
    assert rep.verdict == "AMBER"
    assert "foundation" in rep.detail and "cap" in rep.detail


def test_stratum_gate_unmeasured_stratum_fails_closed():
    from finding_memeland.target.snapshot import stratum_gate
    s = snap_with_strata({"foundation": 100_000, "mystery": 900_000})
    rep = stratum_gate(s, {"foundation": 0.5})
    by = {r.stratum: r for r in rep.rows}
    assert by["mystery"].effective == 0        # sem taxa medida = 0, nunca +
    assert rep.total_effective == 50_000


def test_stratum_gate_hard_cap_binds_even_exempt_strata():
    """70% duro (Opus 05/09): a isenção mata o argumento do prior, não o da
    MEDIÇÃO — um estrato quase-pool-inteiro faz o total herdar a barra de
    erro dele."""
    from finding_memeland.target.snapshot import stratum_gate
    s = snap_with_strata({"tail": 400_000, "foundation": 60_000})
    rep = stratum_gate(s, {"tail": 0.5, "foundation": 0.5},
                       cap_exempt=frozenset({"tail"}))
    assert rep.verdict == "AMBER"
    assert "hard" in rep.detail and "tail" in rep.detail
    assert rep.detail.startswith("HARD stratum share cap 70%")   # manchete certa


def test_stratum_gate_red_when_tiny():
    from finding_memeland.target.snapshot import stratum_gate
    s = snap_with_strata({"foundation": 30_000})
    rep = stratum_gate(s, {"foundation": 0.5})
    assert rep.verdict == "RED"
    assert "never loosen quality filters" in rep.detail


def test_stratum_gate_epoch_mismatch_is_red():
    """P1 (revisão Opus 05/09): um GREEN sobre um pool que o selector vai
    recusar é mentira — época errada é RED."""
    from finding_memeland.target.snapshot import stratum_gate
    s = snap_with_strata({"foundation": 500_000})
    rep = stratum_gate(s, {"foundation": 0.5},
                       epoch=CurationEpoch(epoch_id="e2"))
    assert rep.verdict == "RED"
    assert "época" in rep.detail


def test_stratum_gate_stale_snapshot_blocks_green():
    from finding_memeland.target.snapshot import stratum_gate
    s = snap_with_strata({"foundation": 500_000})
    s.built_at = "2026-08-01T00:00:00Z"
    rep = stratum_gate(s, {"foundation": 0.5},
                       epoch=CurationEpoch(epoch_id="e1"),
                       now_iso="2026-09-05T00:00:00Z")   # 35 dias > 14
    assert rep.verdict == "AMBER"
    assert "refresh" in rep.detail


def test_stratum_gate_fresh_snapshot_stays_green():
    from finding_memeland.target.snapshot import stratum_gate
    s = snap_with_strata({"foundation": 200_000, "superrare": 200_000,
                          "makersplace": 200_000})
    s.built_at = "2026-09-01T00:00:00Z"
    rep = stratum_gate(s, {"foundation": 0.5, "superrare": 0.5,
                           "makersplace": 0.5},
                       epoch=CurationEpoch(epoch_id="e1"),
                       now_iso="2026-09-05T00:00:00Z")   # 4 dias < 14
    assert rep.verdict == "GREEN"


def test_stratum_gate_unparseable_built_at_counts_as_stale():
    from finding_memeland.target.snapshot import stratum_gate
    s = snap_with_strata({"foundation": 500_000})    # built_at="t"
    rep = stratum_gate(s, {"foundation": 0.5},
                       now_iso="2026-09-05T00:00:00Z")
    assert rep.verdict == "AMBER"
    assert "ilegível" in rep.detail

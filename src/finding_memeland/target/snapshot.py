"""Curated-pool snapshot — the stratum as a local, refreshed artifact
(Opus, 04/09: quota off the launch path, no live pre-hunt queries, no
marketplace as a single point of failure at /launch).

How the pieces fit
==================
A REFRESH (weekly, off the hunt path) queries the art platforms, applies the
HARD filters (base name, uniqueness, EOA owner, age) and writes every
survivor into the snapshot WITH its full metadata and the metadata hash
computed right there. Hunts then draw from the snapshot alone:

  · the chosen target is NEVER queried live before the reveal — the
    commitment uses the snapshot-time metadata hash, so the only party who
    ever resolves the target's URI pre-reveal is the refresh job, which
    resolves EVERYONE's
  · a metadata mutation between refresh and reveal breaks verification and
    voids the hunt (prize back to the vault) — the same public rule as
    before, over a slightly longer window; the weekly cadence keeps that
    window small
  · marketplace quota is spent by the refresh, not the launch

Writability is NOT pre-applied to the pool. Two reasons, both load-bearing:
running the writability judge over ~10^5 entries every week is real money
for no security, and — the anti-circularity rule (Opus, 04/09) — the gate
must be measured, not manufactured. So: the gate certifies the pool as
pool_size x writability_rate MEASURED ON A RANDOM SAMPLE, and selection
applies the judge lazily to drawn candidates only (select_writable). The
stratum an attacker would need is "which snapshot entries pass OUR judge
under OUR doctrine" — the API feed is raw material anyone can pull; the
stratum is what survives our judgment, which is not reproducible from
outside (Opus, 04/09, second bias).

The snapshot is a SECRET artifact. It is not the target, but it is the
attacker's dream prior: leak it and the candidate set collapses from "the
chain" to "our pool". It is therefore encrypted at rest with the same
cipher port as the relic pool (PoolCipher / Fernet, key from Doppler), and
its refusal/log strings never carry entry names.

The anti-circularity rule, verbatim, because it is the obvious temptation
when the number comes back low: FIX THE FILTERS FIRST, MEASURE AFTER. A
failed gate widens the SOURCING (more platforms), it never loosens the
quality filters until the number passes.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Callable, Iterator

from .selector import (
    CurationEpoch,
    SelectionRefused,
    Target,
    TargetSelector,
    metadata_hash,
)

# Gate thresholds — validated 04/09 (Rederivacao_Fasquia_OpcaoA.md):
# effective = pool_size x sampled writability rate, against G ~ 5,000 with a
# 20x safety factor. Between the two bounds: widen sourcing, re-measure.
GATE_GREEN_MIN = 100_000
GATE_RED_MAX = 20_000
# No platform/stratum may exceed this share of the pool (Opus, 05/09) — in
# the measured epoch-1 composition it self-satisfies, but the counter
# enforces it so a collapsed stratum can't silently concentrate the rest.
GATE_MAX_STRATUM_SHARE = 0.40


@dataclass(frozen=True)
class SnapshotEntry:
    """One hard-filter survivor, frozen at refresh time. `metadata` is the
    FULL resolved token metadata — the commitment hash is recomputed from it
    and must match `metadata_sha256` (belt and braces against a corrupted
    store)."""

    contract: str
    token_id: int
    name: str            # base name (serial stripped) — what clues cipher
    name_onchain: str
    metadata: dict
    metadata_sha256: str
    platform: str        # which curated source produced it (for rotation)


@dataclass
class Snapshot:
    epoch_id: str
    built_at: str        # ISO-8601 UTC, set by the refresh job
    entries: list[SnapshotEntry] = field(default_factory=list)

    def size(self) -> int:
        return len(self.entries)


class SnapshotIntegrityError(RuntimeError):
    """The stored snapshot does not verify (bad hash, wrong shape). Fail
    closed: a pool that cannot be trusted is a pool we do not draw from."""


# --------------------------------------------------------------------------- #
# Persistence — encrypted with the relic pool's cipher port                    #
# --------------------------------------------------------------------------- #


class SnapshotStore:
    """Serialise/deserialise a Snapshot through a PoolCipher-compatible
    cipher (encrypt(str)->str / decrypt(str)->str; FernetPoolCipher in
    production, NullPoolCipher in tests). Storage I/O is injected as plain
    read/write callables so this works over a file or a Supabase column."""

    def __init__(self, *, cipher, read: Callable[[], str | None],
                 write: Callable[[str], None]):
        self._cipher = cipher
        self._read = read
        self._write = write

    def save(self, snap: Snapshot) -> None:
        payload = json.dumps({
            "v": 1,
            "epoch_id": snap.epoch_id,
            "built_at": snap.built_at,
            "entries": [{
                "contract": e.contract, "tokenId": e.token_id,
                "name": e.name, "name_onchain": e.name_onchain,
                "metadata": e.metadata, "metadata_sha256": e.metadata_sha256,
                "platform": e.platform,
            } for e in snap.entries],
        }, ensure_ascii=False)
        self._write(self._cipher.encrypt(payload))

    def load(self) -> Snapshot | None:
        blob = self._read()
        if blob is None:
            return None
        try:
            doc = json.loads(self._cipher.decrypt(blob))
            entries = []
            for raw in doc["entries"]:
                entry = SnapshotEntry(
                    contract=raw["contract"], token_id=int(raw["tokenId"]),
                    name=raw["name"], name_onchain=raw["name_onchain"],
                    metadata=raw["metadata"],
                    metadata_sha256=raw["metadata_sha256"],
                    platform=raw.get("platform", ""),
                )
                if metadata_hash(entry.metadata) != entry.metadata_sha256:
                    raise SnapshotIntegrityError(
                        "snapshot entry hash mismatch — store corrupted or "
                        "tampered; rebuild the snapshot (no entry named "
                        "by design)")
                entries.append(entry)
            return Snapshot(epoch_id=doc["epoch_id"],
                            built_at=doc["built_at"], entries=entries)
        except SnapshotIntegrityError:
            raise
        except Exception as e:  # noqa: BLE001 — bad cipher/shape, fail closed
            raise SnapshotIntegrityError(
                f"snapshot unreadable ({type(e).__name__}) — wrong key or "
                "corrupted store; rebuild the snapshot") from e


# --------------------------------------------------------------------------- #
# Drawing from the snapshot                                                    #
# --------------------------------------------------------------------------- #


class SnapshotSource:
    """CandidateSource over a snapshot: yields (contract, tokenId) in a fresh
    uniform shuffle per call — the exchangeability the selector's
    first-qualifier draw relies on. Age and the hard filters were the refresh
    job's obligation; the epoch must match, or the pool predates the current
    curation and drawing from it would silently undo a rotation."""

    def __init__(self, snapshot: Snapshot, *, rng: random.Random | None = None):
        self._snap = snapshot
        self._rng = rng or random.SystemRandom()

    def candidates(self, epoch: CurationEpoch) -> Iterator[tuple[str, int]]:
        if epoch.epoch_id != self._snap.epoch_id:
            raise SelectionRefused(
                f"snapshot is for epoch {self._snap.epoch_id!r} but selection "
                f"asked for {epoch.epoch_id!r} — refresh the snapshot before "
                "drawing (fail-closed; a stale pool undoes the rotation)")
        order = list(range(len(self._snap.entries)))
        self._rng.shuffle(order)
        for i in order:
            e = self._snap.entries[i]
            yield e.contract, e.token_id


def snapshot_selector(snapshot: Snapshot, *, chain: str = "base",
                      rng: random.Random | None = None,
                      max_attempts: int = 400) -> TargetSelector:
    """A TargetSelector whose adapters read the snapshot instead of the
    network: NO live query touches any candidate at selection time. The
    hard-filter verdicts are the refresh job's (an entry exists only because
    it passed), so the adapters answer from the store."""
    index = {(e.contract.lower(), e.token_id): e for e in snapshot.entries}

    def fetch_metadata(contract: str, token_id: int) -> dict | None:
        e = index.get((contract.lower(), token_id))
        return e.metadata if e else None

    return TargetSelector(
        source=SnapshotSource(snapshot, rng=rng),
        fetch_metadata=fetch_metadata,
        owner_is_eoa=lambda c, t: (c.lower(), t) in index or None,
        name_is_unique=lambda n, c, t: (c.lower(), t) in index or None,
        chain=chain,
        max_attempts=max_attempts,
    )


def select_writable(selector: TargetSelector, epoch: CurationEpoch, *,
                    is_writable: Callable[[Target], bool | None],
                    max_draws: int = 12) -> Target:
    """Draw until a target passes the writability judge — the lazy
    application of the final, non-reproducible filter. Each draw is uniform
    over the pool, so the accepted target is uniform over the WRITABLE pool.
    `is_writable` is one private LLM call (the judge from
    testar_escrevibilidade's doctrine); None (judge unreachable) rejects the
    draw — fail-closed, never launch on hope.

    max_draws exists because a pool whose certified rate is ~20%+ should
    yield inside a handful of draws; needing more than a dozen means the
    certification is stale — refuse and re-measure rather than grind."""
    for _ in range(max_draws):
        target = selector.select(epoch)
        if is_writable(target) is True:
            return target
    raise SelectionRefused(
        f"no writable target in {max_draws} draws — the pool's certified "
        "writability rate looks stale; re-measure the gate before launching "
        "(fail-closed)")


# --------------------------------------------------------------------------- #
# The gate                                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GateVerdict:
    effective: int
    verdict: str          # "GREEN" | "AMBER" | "RED"
    detail: str


def gate_verdict(pool_size: int, writability_rate: float) -> GateVerdict:
    """effective = pool x sampled rate, against the 04/09 thresholds. AMBER
    is not a launch state: it means widen the SOURCING and re-measure —
    never loosen the quality filters (the anti-circularity rule)."""
    effective = int(pool_size * writability_rate)
    if effective >= GATE_GREEN_MIN:
        verdict = "GREEN"
        detail = f"effective {effective:,} >= {GATE_GREEN_MIN:,} — launchable"
    elif effective <= GATE_RED_MAX:
        verdict = "RED"
        detail = (f"effective {effective:,} <= {GATE_RED_MAX:,} — do not "
                  "launch; widen sourcing (never loosen quality filters)")
    else:
        verdict = "AMBER"
        detail = (f"effective {effective:,} between bounds — widen sourcing "
                  "and re-measure before launching")
    return GateVerdict(effective=effective, verdict=verdict, detail=detail)


# --------------------------------------------------------------------------- #
# The stratum counter — the definitive gate (Opus, 05/09)                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StratumRow:
    stratum: str
    entries: int
    writability_rate: float
    effective: int
    share: float


@dataclass(frozen=True)
class StratumGateReport:
    """The launch decision, PER STRATUM (Opus, 05/09: the 04-05/09 census
    green rests on its weakest leg — the tail — so a total alone is blind:
    'se vier a 80k, precisamos de saber se foi a cauda que colapsou ou o
    Manifold que era optimista, senão não sabemos onde ir buscar sourcing').
    Also enforces the concentration cap the census showed self-satisfying:
    a collapsed stratum must not silently concentrate the rest."""

    rows: tuple[StratumRow, ...]
    total_effective: int
    verdict: str          # "GREEN" | "AMBER" | "RED"
    detail: str

    def render(self) -> str:
        """Operator-log table. Stratum names are labels, never target
        names — safe for Telegram."""
        lines = [f"{'stratum':14} {'entries':>9} {'writ.':>6} "
                 f"{'effective':>10} {'share':>6}"]
        for r in self.rows:
            lines.append(f"{r.stratum:14} {r.entries:>9,} "
                         f"{r.writability_rate:>6.0%} {r.effective:>10,} "
                         f"{r.share:>6.0%}")
        lines.append(f"TOTAL effective: {self.total_effective:,} — "
                     f"{self.verdict}: {self.detail}")
        return "\n".join(lines)


def stratum_gate(snapshot: Snapshot,
                 writability_rates: dict[str, float],
                 *,
                 cap_exempt: frozenset[str] = frozenset()) -> StratumGateReport:
    """Count the REAL pool per stratum (entries carry the platform slug the
    refresh stamped) and apply the gate: total >= GATE_GREEN_MIN, no stratum
    above GATE_MAX_STRATUM_SHARE of the effective pool. `writability_rates`
    are the per-stratum sampled rates (measured 04-05/09; re-sampled per
    epoch); a stratum with no measured rate fails closed at 0.0 — an
    unmeasured stratum contributes nothing to a launch decision.

    `cap_exempt` names strata the concentration cap does NOT bind. The
    cap's threat model (Opus, 05/09) is that revealed hunts shrink the
    attacker's prior to the dominant platform's SEARCH BOX — so it applies
    to endpoint-enumerable strata. A stratum third parties cannot enumerate
    (the 2021 tail of one-off artist contracts: 'ninguém consegue enumerar
    contratos avulsos de 2021 como consulta um endpoint curated') is where
    'o risco e a defesa estão no mesmo sítio' — exempting it is Opus's own
    side-note made mechanical. Exemptions are epoch configuration, decided
    by humans, never inferred."""
    counts: dict[str, int] = {}
    for e in snapshot.entries:
        counts[e.platform or "unknown"] = counts.get(e.platform or "unknown", 0) + 1
    rows = []
    total = 0
    for stratum, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        rate = writability_rates.get(stratum, 0.0)
        eff = int(n * rate)
        total += eff
        rows.append((stratum, n, rate, eff))
    full = []
    over = []
    for stratum, n, rate, eff in rows:
        share = eff / total if total else 0.0
        full.append(StratumRow(stratum=stratum, entries=n,
                               writability_rate=rate, effective=eff,
                               share=share))
        if share > GATE_MAX_STRATUM_SHARE and stratum not in cap_exempt:
            over.append(stratum)

    if total >= GATE_GREEN_MIN and not over:
        verdict, detail = "GREEN", "launchable"
    elif total <= GATE_RED_MAX:
        verdict = "RED"
        detail = (f"total <= {GATE_RED_MAX:,} — do not launch; widen "
                  "sourcing (never loosen quality filters)")
    elif over:
        verdict = "AMBER"
        detail = (f"stratum share cap {GATE_MAX_STRATUM_SHARE:.0%} exceeded "
                  f"by: {', '.join(over)} — widen the OTHER strata")
    else:
        verdict = "AMBER"
        detail = (f"total below {GATE_GREEN_MIN:,} — widen sourcing and "
                  "re-measure before launching")
    return StratumGateReport(rows=tuple(full), total_effective=total,
                             verdict=verdict, detail=detail)

"""Snapshot pipeline — the one operational command behind the weekly
refresh AND the first real snapshot: discovery -> registry -> refresh ->
encrypted snapshot -> stratum gate.

Order and roles are the ratified ones (Opus, 05/09): the stratum counter is
the DEFINITIVE gate — this pipeline is what produces the number that
authorises or refuses a launch, per stratum, fail-closed. Every step that
fails keeps the previous good artifact:

  · a failed scan leaves blocks unscanned for the next run
  · a failed refresh (a platform unlistable) keeps SERVING the previous
    snapshot — the game never runs on a silently smaller pool
  · the gate report is Telegram-safe by construction (counts and strata
    labels; never a target name, never a registry address)

Everything effectful is injected; the wiring logic tests offline.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .discovery import DiscoveryState, EraDiscovery
from .refresh import RefreshFailed, RefreshJob
from .snapshot import CurationEpoch, Snapshot, StratumGateReport, stratum_gate
from .sources import ContractRegistry, epoch1_listers


@dataclass(frozen=True)
class PipelineReport:
    """One run's outcome, operator-facing. `snapshot_is_fresh` False means
    the refresh failed and the gate below was computed over the PREVIOUS
    snapshot — still a valid launch decision, over the pool actually being
    served."""

    blocks_scanned: int
    registry_counts: dict
    snapshot_is_fresh: bool
    snapshot_size: int
    gate: StratumGateReport | None
    note: str = ""

    def render(self) -> str:
        lines = [
            f"scan: +{self.blocks_scanned} blocos | registo: "
            + ", ".join(f"{s}={n}" for s, n in sorted(self.registry_counts.items()))
        ]
        lines.append(
            f"snapshot: {self.snapshot_size:,} entradas "
            + ("(fresco)" if self.snapshot_is_fresh
               else "(ANTERIOR — refresh falhou, ver nota)"))
        if self.note:
            lines.append(f"nota: {self.note}")
        if self.gate is not None:
            lines.append(self.gate.render())
        else:
            lines.append("gate: SEM SNAPSHOT — nada para decidir; "
                         "não lançar (fail-closed)")
        return "\n".join(lines)


class SnapshotPipeline:
    """discovery -> registry -> refresh(epoch-1 listers) -> snapshot ->
    stratum gate. Stores are the encrypted ports built earlier; refresh
    collaborators are the production adapters (chain metadata resolver,
    EOA check, marketplace uniqueness)."""

    def __init__(
        self,
        *,
        discovery: EraDiscovery,
        discovery_store,          # DiscoveryStateStore
        registry_store,           # RegistryStore
        snapshot_store,           # SnapshotStore
        eth_call,                 # p/ os listers da época 1
        fetch_metadata,
        owner_is_eoa,
        name_is_unique,
        now_iso,
        writability_rates: dict[str, float],
        cap_exempt: frozenset[str],
    ):
        self._discovery = discovery
        self._dstore = discovery_store
        self._rstore = registry_store
        self._sstore = snapshot_store
        self._eth_call = eth_call
        self._fetch_metadata = fetch_metadata
        self._owner_is_eoa = owner_is_eoa
        self._name_is_unique = name_is_unique
        self._now_iso = now_iso
        self._rates = dict(writability_rates)
        self._cap_exempt = cap_exempt

    def run(self, epoch: CurationEpoch, *, scan_blocks: int = 300,
            rng: random.Random | None = None) -> PipelineReport:
        # 1) descoberta incremental
        state: DiscoveryState = self._dstore.load()
        scanned = self._discovery.scan(state, scan_blocks, rng)
        self._dstore.save(state)

        # 2) registo reservado
        registry: ContractRegistry = self._rstore.load() or ContractRegistry()
        self._discovery.classify_into(state, registry)
        self._rstore.save(registry)

        # 3) refresh sobre a composição da época 1
        listers = epoch1_listers(eth_call=self._eth_call, registry=registry)
        job = RefreshJob(
            listers=listers,
            fetch_metadata=self._fetch_metadata,
            owner_is_eoa=self._owner_is_eoa,
            name_is_unique=self._name_is_unique,
            now_iso=self._now_iso,
        )
        fresh = True
        note = ""
        try:
            snapshot, _refresh_report = job.build(epoch)
            self._sstore.save(snapshot)
        except RefreshFailed as e:
            fresh = False
            note = str(e)[:160]
            snapshot = self._sstore.load()

        # 4) gate por estrato — sobre o pool que REALMENTE se serve
        if snapshot is None:
            return PipelineReport(blocks_scanned=scanned,
                                  registry_counts=registry.counts(),
                                  snapshot_is_fresh=False, snapshot_size=0,
                                  gate=None, note=note or "sem snapshot")
        gate = stratum_gate(snapshot, self._rates,
                            cap_exempt=self._cap_exempt)
        return PipelineReport(blocks_scanned=scanned,
                              registry_counts=registry.counts(),
                              snapshot_is_fresh=fresh,
                              snapshot_size=snapshot.size(),
                              gate=gate, note=note)

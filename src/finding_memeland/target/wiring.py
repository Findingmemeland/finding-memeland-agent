"""Target-mode composition (soldadura 5/6) — everything main.py needs to
turn Settings into `TargetPorts` + the two operator commands, in one
testable place. main.py stays a thin caller (its diff for target mode is a
dozen lines) and this module composes only from injected clients and
transports, so the wiring itself is exercised offline with fakes.

Doctrine carried here, not in main.py:
  · R1 — RPCs by chain name (`chain_rpcs`), the epoch's chains must exist
  · R2 — every guard with a canary is the production one (ClueSearchGuard,
    MarketNameUniqueness, ChainEoaCheck, listers, EraDiscovery)
  · R3 — the GENERIC family (public providers, per-batch rotation) reads the
    target; the KEYED family reads everyone
  · decision 3 — TARGET_POOL_KEY, its own Fernet cipher, for the sealed
    target AND the three encrypted blobs (discovery/registry/snapshot)
  · resolve_link MANDATORY — RaribleChainProbe + page resolvers (none
    until captured; see adapters)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .adapters import (
    AnthropicBatchJudge,
    AnthropicVision,
    Erc721Metadata,
    GenericMetadata,
    JsonRpc,
    MarketplaceLinkResolver,
    Provider,
    RaribleChainProbe,
    RotatingLiveCheck,
    chain_rpcs,
    code_bytes,
    mint_fetcher,
)
from .clues import TargetClueEngine, describe_image_batched
from .discovery import DiscoveryStateStore, EraDiscovery
from .hunt import (
    LIVE_HASH_RESOLVED,
    LIVE_HASH_UNRESOLVABLE,
    LiveHash,
    SealedTarget,
    SealedTargetCipher,
    SprayDetector,
    SprayParams,
    TargetHuntPreparer,
)
from .integration import TargetPorts
from .pipeline import PipelineReport, SnapshotPipeline
from .search_guard import ClueSearchGuard, MarketNameUniqueness, RaribleSearch
from .selector import CurationEpoch
from .snapshot import Snapshot, SnapshotStore, StratumGateReport, stratum_gate
from .sources import (
    EPOCH1_CAP_EXEMPT,
    EPOCH1_CHAINS,
    EPOCH1_STRATA,
    ChainEoaCheck,
    RegistryStore,
)

BLOB_DISCOVERY = "target:discovery"
BLOB_REGISTRY = "target:registry"
BLOB_SNAPSHOT = "target:snapshot"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TargetWiring:
    """What main.py holds: the ports for the Orchestrator, the pipeline for
    /scan and /snapshot, and the pieces /status reads."""

    ports: TargetPorts
    pipeline: SnapshotPipeline
    epoch: CurationEpoch
    snapshot_store: SnapshotStore
    scan_blocks: int
    writability_rates: dict
    uniqueness_rates: dict

    def gate_now(self) -> StratumGateReport | None:
        """The gate over the STORED snapshot, right now — what /launch will
        see. None when there is no snapshot."""
        snap: Snapshot | None = self.snapshot_store.load()
        if snap is None:
            return None
        return stratum_gate(snap, self.writability_rates,
                            uniqueness_rates=self.uniqueness_rates,
                            cap_exempt=EPOCH1_CAP_EXEMPT,
                            epoch=self.epoch, now_iso=_now_iso())

    def snapshot_fingerprint(self) -> str:
        """Binds a /launch confirmation to the snapshot it was shown over:
        a /snapshot between the prompt and the 'sim' changes it and the
        confirmation is refused (the relic-id check, for targets). Includes
        a short digest of the ENTRIES (P2-4): a rebuild landing on the same
        ISO second, or a stopped clock, must not pass as the same pool."""
        snap = self.snapshot_store.load()
        return "" if snap is None else f"{snap.epoch_id}@{snap.built_at}#{snap.digest()}"

    def scan(self, n_blocks: int | None = None) -> str:
        outcome, registry = self.pipeline.scan(n_blocks or self.scan_blocks)
        if not outcome.canary_ok:
            return ("scan RECUSADO — canário falhou (bloco fixado não devolveu "
                    "exactamente os mints medidos: nó não-arquivo, cadeia "
                    "errada, filtro truncado). Nada varrido.")
        return (f"scan: +{outcome.scanned} blocos ({outcome.zero_mint_blocks} sem "
                f"mints, {outcome.failed} falharam por transporte) | registo: "
                + ", ".join(f"{s}={n}" for s, n in sorted(registry.counts().items())))

    def snapshot(self) -> PipelineReport:
        return self.pipeline.refresh(self.epoch)


def build_target(s, *, anthropic, repo, http_get, http_post, http_get_bytes,
                 solver=None, rng: random.Random | None = None) -> TargetWiring:
    """Compose target mode from Settings `s` and injected clients.

    http_get(url, headers) -> str · http_post(url, body, headers) -> str ·
    http_get_bytes(url, headers) -> bytes — all raise on HTTP/transport
    failure. `repo` provides get_blob/put_blob (db.client.Repo). `solver`
    is the blind solver main.py already selects for relic clues (an
    INDEPENDENT model by default — Hunt #7 post-mortem); None keeps the
    engine's own default, False switches it off."""
    from ..persona.relic_pool import FernetPoolCipher

    missing = s.target_missing()
    if missing:
        raise RuntimeError("target mode not configured: " + ", ".join(missing))
    # P1-3: rate keys must be strata the epoch can produce — a typo would
    # zero a stratum in silence (fail-closed, but pointing the operator at
    # the wrong fix). Loud at boot, with the unknown name.
    for label, rates in (("target_writability_rates", s.target_writability_rate_map),
                         ("target_uniqueness_rates", s.target_uniqueness_rate_map)):
        unknown = sorted(set(rates) - EPOCH1_STRATA)
        if unknown:
            raise RuntimeError(
                f"{label}: unknown stratum {unknown} — known: "
                f"{sorted(EPOCH1_STRATA)} (a typo here silences a stratum)")
        bad = sorted(k for k, v in rates.items() if not 0.0 < v <= 1.0)
        if bad:
            raise RuntimeError(f"{label}: rate out of (0, 1] for {bad}")

    cipher = FernetPoolCipher(s.target_pool_key)
    rng = rng or random.SystemRandom()
    epoch = CurationEpoch(epoch_id=s.target_epoch_id,
                          min_age_days=int(s.target_min_age_days),
                          max_snapshot_age_days=int(s.target_max_snapshot_age_days))

    # -- KEYED family: our RPCs, our gateway (reads everyone) --------------- #
    rpcs = chain_rpcs({"ethereum": s.eth_rpc_url, "base": s.base_rpc_url},
                      http_post=http_post)
    # P1-2 / R1: every chain the epoch touches has a keyed RPC — loud, by name
    keyed_missing = sorted(EPOCH1_CHAINS - set(rpcs))
    if keyed_missing:
        raise RuntimeError(f"no keyed RPC for epoch chain(s) {keyed_missing} "
                           "(eth_rpc_url / base_rpc_url)")
    keyed_meta = Erc721Metadata(rpcs=rpcs, gateway=s.target_ipfs_gateway,
                                http_get=http_get)
    eoa_check = ChainEoaCheck(rpcs=rpcs)
    eth_node = JsonRpc(url=s.eth_rpc_url, http_post=http_post, label="rpc:ethereum")
    discovery = EraDiscovery(chain="ethereum",
                             canary_block=int(s.target_canary_block),
                             canary_mints=int(s.target_canary_mints),
                             fetch_mints=mint_fetcher(eth_node),
                             get_code=code_bytes(eth_node))

    def store(key: str):
        return dict(cipher=cipher, read=lambda: repo.get_blob(key),
                    write=lambda payload: repo.put_blob(key, payload))

    snapshot_store = SnapshotStore(**store(BLOB_SNAPSHOT))
    pipeline = SnapshotPipeline(
        discovery=discovery,
        discovery_store=DiscoveryStateStore(**store(BLOB_DISCOVERY)),
        registry_store=RegistryStore(**store(BLOB_REGISTRY)),
        snapshot_store=snapshot_store,
        rpcs=rpcs,
        fetch_token=keyed_meta.read,
        owner_is_eoa=eoa_check,
        now_iso=_now_iso,
        writability_rates=s.target_writability_rate_map,
        uniqueness_rates=s.target_uniqueness_rate_map,
        cap_exempt=EPOCH1_CAP_EXEMPT,
    )

    # -- marketplace: search guard, uniqueness, chain probe (one key) ------- #
    page = 50
    rarible = RaribleSearch(http_post=http_post, api_key=s.rarible_api_key,
                            size=page)
    search_guard = ClueSearchGuard(search=rarible)
    uniqueness = MarketNameUniqueness(search=rarible, page_size=page)
    resolver = MarketplaceLinkResolver(
        chain_probe=RaribleChainProbe(http_get=http_get, api_key=s.rarible_api_key),
        page_resolvers={},          # Foundation/SuperRare: after their capture
    )

    # -- GENERIC family: public providers, rotation per batch ------------- #
    # A provider is built ONLY with the chains it has an RPC for (P1-2): a
    # provider missing a chain reads that chain as a KeyError at batch time
    # — configuration, loud — never as a silent absence. And every epoch
    # chain must be readable by at least one provider, or the live check
    # for a target on that chain could never rotate onto it.
    pub = s.target_public_rpc_map
    gws = s.target_ipfs_gateway_list
    n = max(len(lst) for lst in pub.values())
    providers = []
    for i in range(n):
        urls = {c: lst[i] for c, lst in pub.items() if i < len(lst)}
        if not urls:
            continue
        providers.append(Provider(name=f"provider{i}", rpc_urls=urls,
                                  gateway=gws[i % len(gws)]))
    covered = set().union(*(set(p.rpc_urls) for p in providers)) if providers else set()
    generic_missing = sorted(EPOCH1_CHAINS - covered)
    if generic_missing:
        raise RuntimeError(f"no public RPC for epoch chain(s) {generic_missing} "
                           "(target_public_rpcs_<chain>) — the live check could "
                           "not read a target there")
    generic = GenericMetadata(providers=providers, http_get=http_get,
                              http_post=http_post, http_get_bytes=http_get_bytes)
    vision = AnthropicVision(anthropic, s.target_vision_model)

    def describe_image(sealed: SealedTarget) -> str:
        with generic.batch():
            return describe_image_batched(
                target_image_url=sealed.target.image,
                decoy_image_urls=[d.image for d in sealed.decoys],
                fetch_bytes_generic=generic.fetch_bytes,
                describe=vision, content_ok=vision.content_ok,
                target_id=sealed.id(), rng=rng)

    def live_hash(sealed: SealedTarget) -> LiveHash:
        """Once, at void/pay-noted time, through OUR gateway (the hunt is
        over; the repeated live check never touched one). TRI-STATE (R8):
        the chain saying "no token" is unresolvable; our gateway/RPC
        failing is unavailable — the post never confuses the two."""
        from .selector import metadata_hash
        from .sources import ChainUnavailable
        # ⚠️ THE ONLY Erc721Metadata IN THE PACKAGE WITH content_addressed_only
        # =False, deliberately (P2-5): the void/pay-noted post publishes what
        # the chain serves TODAY, mutable or not — the reader gets the fact.
        # Every other instance (refresh, selector, live path) keeps the
        # default True; do not "harmonise" this one.
        any_meta = Erc721Metadata(rpcs=rpcs, gateway=s.target_ipfs_gateway,
                                  http_get=http_get, content_addressed_only=False)
        t = sealed.target
        try:
            meta = any_meta(t.chain, t.contract, t.token_id)
        except ChainUnavailable:
            return LiveHash.unavailable()
        if meta is None:
            return LiveHash(LIVE_HASH_UNRESOLVABLE, None)
        if not isinstance(meta, dict) or not meta:
            return LiveHash.unavailable()
        return LiveHash(LIVE_HASH_RESOLVED, metadata_hash(meta))

    preparer = TargetHuntPreparer(
        snapshot_store=snapshot_store,
        writability_rates=s.target_writability_rate_map,
        uniqueness_rates=s.target_uniqueness_rate_map,
        cap_exempt=EPOCH1_CAP_EXEMPT,
        judge=AnthropicBatchJudge(anthropic, s.target_judge_model),
        name_is_unique=uniqueness,
        now_iso=_now_iso, rng=rng)

    ports = TargetPorts(
        epoch=epoch,
        preparer=preparer,
        cipher=SealedTargetCipher(cipher=cipher),
        clue_engine=TargetClueEngine(anthropic, s.anthropic_model,
                                     search_guard=search_guard, solver=solver),
        describe_image=describe_image,
        live_check=RotatingLiveCheck(generic=generic, rng=rng),
        resolve_link=resolver,
        live_hash=live_hash,
        spray=SprayDetector(SprayParams()),
        hold_renotify_s=float(s.target_hold_renotify_s),
        max_hold_s=float(s.target_max_hold_s),
        max_total_hold_s=float(s.target_max_total_hold_s),
    )
    return TargetWiring(ports=ports, pipeline=pipeline, epoch=epoch,
                        snapshot_store=snapshot_store,
                        scan_blocks=int(s.target_scan_blocks),
                        writability_rates=s.target_writability_rate_map,
                        uniqueness_rates=s.target_uniqueness_rate_map)


# Type alias for main.py readers
Notify = Callable[[str], None]

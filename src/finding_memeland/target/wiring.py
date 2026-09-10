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
    OpenSeaChainProbe,
    Provider,
    RaribleChainProbe,
    RotatingLiveCheck,
    chain_rpcs,
    code_bytes,
    creator_credit,
    gateway_url,
    mint_fetcher,
    sniff_media_type,
)
from .clues import AnthropicTruthJudge, TargetClueEngine, describe_image_batched
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
from .integration import ArtworkUnusable, TargetPorts
from .pipeline import PipelineReport, SnapshotPipeline
from .search_guard import (
    ClueSearchGuard,
    MarketNameUniqueness,
    OpenSeaSearch,
    RaribleSearch,
)
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
MAX_ARTWORK_BYTES = 5 * 1024 * 1024      # X image limit; bigger → link only
ARTWORK_TIMEOUT_S = 10                   # a winner is waiting; the picture is optional


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
    # built and tested, NOT wired into ports (decision 10/09: reveal = link only)
    fetch_artwork: Callable | None = None
    # which marketplace serves the search guard / uniqueness / chain probe
    # ('opensea' or 'rarible') — for /status, never for a public post
    market_surface: str = ""

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


def _media_kind(data: bytes) -> str:
    """Names what the gateway served when it is not a still image — for
    the operator's tally, never published."""
    head = data[:64].lstrip()
    if data[4:8] == b"ftyp":
        return "video/mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    if head[:5].lower() == b"<?xml" or head[:4].lower() == b"<svg":
        return "svg"
    if head[:1] == b"<":
        return "html"
    if data[:4] == b"%PDF":
        return "pdf"
    return "unknown"


def build_target(s, *, anthropic, repo, http_get, http_post, http_get_bytes,
                 get_artwork_bytes=None, solver=None,
                 rng: random.Random | None = None) -> TargetWiring:
    """Compose target mode from Settings `s` and injected clients.

    http_get(url, headers) -> str · http_post(url, body, headers) -> str ·
    http_get_bytes(url, headers) -> bytes — all raise on HTTP/transport
    failure. `get_artwork_bytes` (same shape) is the reveal's transport:
    short timeout, NO redirects, reads at most the cap (main._http_get_artwork);
    defaults to http_get_bytes for tests. `repo` provides get_blob/put_blob (db.client.Repo). `solver`
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
    # One surface serves all three, chosen by the key present — OpenSea
    # first (10/09: measured against FND #1, 120 requests/min on the
    # approved key; Rarible's public plans are 100/MONTH or Enterprise).
    # config.target_missing() guarantees at least one key is set.
    page = 50
    if s.opensea_api_key:
        market_surface = "opensea"
        market = OpenSeaSearch(http_get=http_get, api_key=s.opensea_api_key,
                               size=page)
        chain_probe = OpenSeaChainProbe(http_get=http_get,
                                        api_key=s.opensea_api_key)
    else:
        market_surface = "rarible"
        market = RaribleSearch(http_post=http_post, api_key=s.rarible_api_key,
                               size=page)
        chain_probe = RaribleChainProbe(http_get=http_get,
                                        api_key=s.rarible_api_key)
    search_guard = ClueSearchGuard(search=market)
    uniqueness = MarketNameUniqueness(search=market, page_size=page)
    resolver = MarketplaceLinkResolver(
        chain_probe=chain_probe,
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

    get_art = get_artwork_bytes or http_get_bytes

    def fetch_artwork(sealed: SealedTarget) -> bytes | None:
        """The reveal's picture, once, through OUR gateway (the hunt is
        decided — same doctrine as live_hash). Only a content-addressed
        image (the draw guarantees it), only if it sniffs as an image, only
        up to MAX_ARTWORK_BYTES; anything else raises ArtworkUnusable with
        the MEASURED reason (the operator counts how often the reveal
        degrades to text — much 1/1 art is mp4/SVG) and the post carries
        the item link alone. Transport errors propagate (reveal_media
        catches them)."""
        url = gateway_url(sealed.target.image, s.target_ipfs_gateway)
        if url is None or not url.lower().startswith(s.target_ipfs_gateway.lower()):
            raise ArtworkUnusable("image not content-addressed (not fetched)")
        data = get_art(url, {})
        if not data:
            raise ArtworkUnusable("empty body from the gateway")
        if len(data) > MAX_ARTWORK_BYTES:
            raise ArtworkUnusable(f"too big for X (> {MAX_ARTWORK_BYTES // (1024 * 1024)} MB)")
        if sniff_media_type(data) is None:
            raise ArtworkUnusable(f"not a still image ({_media_kind(data)})")
        return data

    def credit(sealed: SealedTarget) -> str:
        """R9 from the chain, at reveal time, on OUR keyed RPCs (the hunt is
        decided). 'name.eth' only with the forward check; else 0x…; else ''."""
        t = sealed.target
        return creator_credit(rpcs, t.chain, t.contract, t.token_id)

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
                                     search_guard=search_guard, solver=solver,
                                     truth_judge=AnthropicTruthJudge(
                                         anthropic, s.target_judge_model)),
        describe_image=describe_image,
        live_check=RotatingLiveCheck(generic=generic, rng=rng),
        resolve_link=resolver,
        live_hash=live_hash,
        # DECISION (Pedro, 10/09): the reveal carries the OpenSea item link
        # and NO attached image — X renders one or the other, and the link
        # card is the one that shows the piece on its marketplace page
        # (measured Hunt #9). `fetch_artwork` stays built and tested for the
        # day the decision changes; it is simply not wired.
        fetch_artwork=None,
        creator_credit=credit,
        spray=SprayDetector(SprayParams()),
        hold_renotify_s=float(s.target_hold_renotify_s),
        max_hold_s=float(s.target_max_hold_s),
        max_total_hold_s=float(s.target_max_total_hold_s),
    )
    return TargetWiring(ports=ports, pipeline=pipeline, epoch=epoch,
                        snapshot_store=snapshot_store,
                        scan_blocks=int(s.target_scan_blocks),
                        writability_rates=s.target_writability_rate_map,
                        uniqueness_rates=s.target_uniqueness_rate_map,
                        fetch_artwork=fetch_artwork,
                        market_surface=market_surface)


# Type alias for main.py readers
Notify = Callable[[str], None]

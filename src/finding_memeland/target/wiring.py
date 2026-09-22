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
from datetime import datetime, UTC
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
    abi_uint,
    gateway_url,
    mint_fetcher,
    shrink_for_vision,
    sniff_media_type,
)
from .clues import (
    AnthropicTruthJudge,
    TargetClueContext,
    TargetClueEngine,
    describe_image_batched,
)
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
from .prepare import (
    PREPARED_TTL_HOURS, SOURCES, Larder, LarderStore, Prepared,
    PrepareRefused, PreparedStore, TargetFinder,
    TargetPreparer as LarderPreparer, used_hmac,
)
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
    SEL_TOKENBYINDEX,
    SEL_TOTAL,
    EPOCH1_CAP_EXEMPT,
    EPOCH1_CHAINS,
    EPOCH1_STRATA,
    ChainEoaCheck,
    ChainUnavailable,
    RegistryStore,
)

BLOB_DISCOVERY = "target:discovery"
BLOB_REGISTRY = "target:registry"
BLOB_SNAPSHOT = "target:snapshot"
BLOB_LARDER = "target:larder"
BLOB_PREPARED = "target:prepared"
PROBE_BYTES = 4096
# 8 s, measured 17/09: of 282 s over 20 draws, ~100 went to gateways that were
# never going to answer. A ranged 4 KB read that has not arrived in 8 s is not
# arriving, and a dead pin gets the same verdict either way.
PROBE_TIMEOUT_S = 8.0
MAX_IMAGE_BYTES = 24 * 1024 * 1024   # vision's ceiling; 171 MB measured 16/09
MAX_ARTWORK_BYTES = 5 * 1024 * 1024      # X image limit; bigger → link only
ARTWORK_TIMEOUT_S = 10                   # a winner is waiting; the picture is optional


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _chains_we_read() -> frozenset[str]:
    """Todas as cadeias de que este processo pode ter de ler um alvo.

    DUAS LISTAS DE FONTES, E ELAS PODEM AFASTAR-SE (22/09). O snapshot antigo
    lê o EPOCH1_CLASSIC; a despensa lê o SOURCES do prepare.py. As duas
    guardas abaixo — RPC com chave, e provedor público para o live check —
    olhavam só para o EPOCH1_CHAINS, que sai do primeiro.

    Hoje isso não dá diferença: está tudo em Ethereum. Mas a tarefa aberta é
    justamente acrescentar uma fonte NOUTRA cadeia, e nesse instante as duas
    guardas ficariam caladas sobre ela. O preço não é um erro no arranque: é
    o /fill a encher a despensa de alvos nessa cadeia, um deles a ser selado,
    a hunt a correr, e o live check a estoirar com um KeyError a meio —
    exactamente o modo de falha que estas guardas existem para impedir.

    A correcção é a união. Uma fonte nova passa a exigir o RPC dessa cadeia
    ANTES de o processo arrancar, e a mensagem diz qual falta."""
    return frozenset(EPOCH1_CHAINS) | frozenset(s.chain for s in SOURCES)


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
    cap_exempt: frozenset = EPOCH1_CAP_EXEMPT
    thresholds: object = None              # snapshot.GateThresholds
    sample_per_stratum: int = 0
    # built and tested, NOT wired into ports (decision 10/09: reveal = link only)
    fetch_artwork: Callable | None = None
    # which marketplace serves the search guard / uniqueness / chain probe
    # ('opensea' or 'rarible') — for /status, never for a public post
    market_surface: str = ""
    # the larder (17/09): targets verified in advance, one used per hunt.
    # THE NEXT THIRTY ANSWERS — encrypted, never rendered, counts only.
    finder: TargetFinder | None = None
    larder_store: LarderStore | None = None
    prepared_store: PreparedStore | None = None
    larder_preparer: object = None            # prepare.TargetPreparer
    pool_key: str = ""
    notify: object = None        # progress lines for /fill and /prepare

    # -- the prepared hunt: read from the DATABASE, never from memory ------ #

    def prepared(self) -> Prepared | None:
        """What `/launch` will publish. A restart between the day before and
        the hour must not lose it (Fable, 17/09)."""
        if self.prepared_store is None:
            return None
        return self.prepared_store.load()

    def prepared_line(self) -> str:
        """One /status line — times and counts, never the target."""
        try:
            p = self.prepared()
        except Exception as e:  # noqa: BLE001 — counts only
            return f"preparado: ilegível ({type(e).__name__}) — corre /prepare"
        if p is None:
            return "preparado: nenhum — corre /prepare"
        left = self.prepared_hours_left(p)
        return (f"preparado às {p.prepared_at[11:16] or '??:??'} UTC "
                f"({p.attempts} tentativa(s)) · "
                + (f"válido mais {left:.0f}h" if left > 0
                   else "EXPIRADO — corre /prepare outra vez"))

    @staticmethod
    def prepared_hours_left(p: Prepared) -> float:
        from datetime import datetime
        try:
            made = datetime.fromisoformat(p.prepared_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError, TypeError):
            return -1.0        # unreadable stamp counts as expired (R8)
        age = (datetime.now(UTC) - made).total_seconds() / 3600
        return PREPARED_TTL_HOURS - age

    def prepared_fingerprint(self) -> str:
        """What the launch confirmation is BOUND to. The commitment, not the
        target: it identifies this preparation uniquely, it is salted (so it
        tells a reader nothing), and it is the very value Clue 1 publishes.
        A /prepare between the prompt and the 'sim' changes it, and the
        operator is asked to run /launch again over the new one."""
        try:
            p = self.prepared()
        except Exception:  # noqa: BLE001 — unreadable binds to nothing
            return ""
        return p.commitment if p is not None else ""

    def prepare(self) -> str:
        """Draw one from the larder, re-verify it, write Clue 1, seal it to
        the database. Replaces the whole snapshot+gate path."""
        if self.larder_preparer is None or self.larder_store is None:
            return "preparação não configurada"
        larder: Larder = self.larder_store.load()
        try:
            prepared, larder = self.larder_preparer.prepare(larder)
        except PrepareRefused as e:
            # A REFUSAL IS A RESULT, NOT A CRASH. Its messages are written
            # leak-free by construction (counts and causes, never a name),
            # so unlike a raw exception they can reach the operator whole —
            # `/prepare FALHOU (PrepareRefused)` would repeat the 16/09
            # mistake of reporting a type where a reason was needed.
            self.larder_store.save(larder)      # whatever it did drop, keep dropped
            return f"⛔ prepare recusado — {e}"
        self.larder_store.save(larder)          # the target is spent
        self.prepared_store.save(prepared)
        return (f"prepare: alvo selado à {prepared.attempts}.ª tentativa · "
                f"despensa {larder.size()} · válido {PREPARED_TTL_HOURS}h")

    def larder_size(self) -> int:
        if self.larder_store is None:
            return 0
        return self.larder_store.load().size()

    def larder_spread(self) -> dict:
        """A forma da despensa, em contagens (ver Larder.spread)."""
        if self.larder_store is None:
            return {}
        return self.larder_store.load().spread()

    def fill(self, want: int) -> str:
        """Draw and verify until the larder holds `want`. Off the clock by
        design: a slow gateway costs time here and nothing else."""
        if self.finder is None or self.larder_store is None:
            return "despensa não configurada"
        larder: Larder = self.larder_store.load()
        try:
            tally = self.finder.fill(larder, want=want, max_draws=12 * want,
                                     notify=self.notify, every=25)
        finally:
            self.larder_store.save(larder)      # keep whatever was found
        return f"fill: {tally.render()} · despensa {larder.size()}"

    def gate_now(self) -> StratumGateReport | None:
        """The gate over the STORED snapshot, right now — what /launch will
        see. None when there is no snapshot."""
        snap: Snapshot | None = self.snapshot_store.load()
        if snap is None:
            return None
        return stratum_gate(snap, self.writability_rates,
                            uniqueness_rates=self.uniqueness_rates,
                            cap_exempt=self.cap_exempt,
                            epoch=self.epoch, now_iso=_now_iso(),
                            thresholds=self.thresholds)

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
                 http_get_range=None,
                 get_artwork_bytes=None, solver=None,
                 rng: random.Random | None = None,
                 progress=None) -> TargetWiring:
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
    keyed_missing = sorted(_chains_we_read() - set(rpcs))
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

    # gate numbers + cap exemption from config (defaults = ratified); an
    # exemption naming an unknown stratum is loud, like the rate keys (P1-3)
    try:
        thresholds = s.target_gate_thresholds
    except ValueError as e:                  # P1-4: inverted bands refuse at boot
        raise RuntimeError(f"target gate config refused: {e}") from e
    cap_exempt = s.target_cap_exempt_set
    unknown_exempt = sorted(cap_exempt - EPOCH1_STRATA)
    if unknown_exempt:
        raise RuntimeError(
            f"target_cap_exempt names strata epoch 1 cannot produce: {unknown_exempt}")

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
        cap_exempt=cap_exempt,
        thresholds=thresholds,
        sample_per_stratum=int(s.target_sample_per_stratum),
        workers=int(s.target_refresh_workers),
        retries=int(s.target_refresh_retries),
        progress=progress,
        rng=rng,
        max_transport_share=float(s.target_refresh_max_transport_share),
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
    generic_missing = sorted(_chains_we_read() - covered)
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
        cap_exempt=cap_exempt,
        judge=AnthropicBatchJudge(anthropic, s.target_judge_model),
        name_is_unique=uniqueness,
        now_iso=_now_iso, rng=rng, thresholds=thresholds)

    # ONE engine, shared by /prepare (Clue 1, on the day before) and the ramp
    # (clues 2+, during the hunt): the guards a clue must pass cannot depend
    # on which command asked for it.
    clue_engine = TargetClueEngine(
        anthropic, s.anthropic_model, search_guard=search_guard, solver=solver,
        truth_judge=AnthropicTruthJudge(anthropic, s.target_judge_model))

    ports = TargetPorts(
        epoch=epoch,
        preparer=preparer,
        cipher=SealedTargetCipher(cipher=cipher),
        clue_engine=clue_engine,
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
        # -- the prepared hunt: /launch reads, it does not draw ---------- #
        # Late-bound on purpose: `prepared_store` is composed below, and
        # composing must stay in one place. The closure resolves when
        # /launch calls it, which is the only moment it matters.
        take_prepared=lambda: prepared_store.load(),
        clear_prepared=lambda: prepared_store.clear(),
        # The guard about the WORLD, run again at launch over the sealed
        # Clue 1 (Fable, 17/09). The judge and the solver are about the
        # clue and the answer — both frozen since yesterday. Whether the
        # piece has become findable in a search box is not.
        recheck_clue_one=(lambda text, *, target_item_id, target_name_onchain:
                          search_guard.check(text, target_item_id=target_item_id,
                                             target_name_onchain=target_name_onchain)),
        prepared_max_age_h=float(PREPARED_TTL_HOURS),
        used_hmac=((lambda tid: used_hmac(tid, s.target_pool_key))
                   if s.target_pool_key else None),
    )
    # -- the larder: find targets in advance, use one per hunt -------------- #
    # a RANGED transport when main.py supplies one (8 s, 4 KB); otherwise the
    # ordinary one, which still works — just slower on dead pins.
    ranged = http_get_range or http_get_bytes

    # ONE gateway is a single point of failure, and at /prepare a single
    # point of failure DESTROYS VERIFIED TARGETS: a candidate whose bytes
    # were read yesterday comes back "dead" today because one host is
    # throttling, and the re-verification spends it. Measured 17/09: three
    # of six candidates "lost the image" overnight, through the same
    # gateway that had served them.
    #
    # It is the rotation rule, which the live check has always had, applied
    # where it was missing. A pin is dead only when EVERY gateway agrees it
    # is; if they all merely fail to answer, that is OUR outage and the
    # caller keeps the candidate.
    probe_gateways = [g for g in ([s.target_ipfs_gateway]
                                  + list(s.target_ipfs_gateway_list)) if g]

    def probe_image(uri: str):
        """RANGED read: the first few KB plus the size. Proves the bytes are
        there (Hunt #11) without pulling a 15 MB artwork. Tried across every
        gateway we have before a pin is called dead."""
        errors = 0
        tried = 0
        for gw in probe_gateways:
            url = gateway_url(uri, gw)
            if url is None:
                continue
            tried += 1
            try:
                got = ranged(url, {"Range": f"bytes=0-{PROBE_BYTES - 1}"})
            except Exception:  # noqa: BLE001 — this host, not the pin
                errors += 1
                continue
            head, size = got if isinstance(got, tuple) else (got, 0)
            if head and sniff_media_type(head) is not None:
                return head, size
        if tried and errors == tried:
            # every host we asked threw: ours, not the candidate's
            raise ChainUnavailable(f"no gateway answered ({errors} tried)")
        return None

    # NOTE (17/09): composing must not touch the network. An earlier draft
    # called enumerable_sources() here and made boot depend on an RPC round
    # trip on every deploy — caught by test_wiring_credit_reads_token_creator,
    # which rightly assumes build_target is pure composition.
    # TargetFinder.load_sizes() already drops a source that will not answer,
    # at the moment it matters, and refuses loudly when none does.
    finder = TargetFinder(
        sources=SOURCES,
        total_supply=lambda c, k: int(rpcs[c].eth_call(k, SEL_TOTAL), 16),
        token_by_index=lambda c, k, i: int(
            rpcs[c].eth_call(k, SEL_TOKENBYINDEX + abi_uint(i)), 16),
        read_token=keyed_meta.read, probe_image=probe_image,
        owner_is_eoa=eoa_check, name_is_unique=uniqueness,
        rng=rng, now_iso=_now_iso)

    larder_store = LarderStore(**store(BLOB_LARDER))
    prepared_store = PreparedStore(**store(BLOB_PREPARED))
    def fetch_artwork_once(uri: str) -> bytes | None:
        """The FULL artwork, once, for the accepted candidate — the only
        place a whole image is downloaded, and then SHRUNK to what the
        vision API takes.

        Two different ceilings, and 16/09 proved they must not be confused:
        MAX_IMAGE_BYTES is how much we are willing to DOWNLOAD (171 MB was
        measured in the wild); VISION_MAX_BYTES is what the provider will
        ACCEPT. A 6 MB artwork sailed past the first and came back from the
        API as a bare BadRequestError. Now it gets resized instead — a clue
        about a lighthouse does not need the pixels the collector paid
        for."""
        for gw in probe_gateways:            # same rotation as the probe
            url = gateway_url(uri, gw)
            if url is None:
                continue
            try:
                data = get_art(url, {})
            except Exception:  # noqa: BLE001 — try the next host
                continue
            if not data or len(data) > MAX_IMAGE_BYTES:
                continue
            if sniff_media_type(data) is None:
                continue
            return shrink_for_vision(data)
        return None

    def write_clue_one(target, description: str):
        """Clue 1, written and put through every guard, ON THE DAY BEFORE.

        This is the slow half of a hunt — up to ten drafts, each judged by
        the consistency judge, the search guard and the blind solver — and
        moving it off launch day is the whole point of `/prepare`. The
        16/09 launch spent 10-25 minutes here with an audience waiting and
        a prompt that promised "seconds"."""
        ctx = TargetClueContext.from_target(target, image_description=description,
                                            metadata=None)
        return clue_engine.next_clue(ctx, 1, [])

    # THE PREPARER MUST BE ABLE TO SPEAK (17/09). Every measured cause in
    # prepare.py — which candidate died of what, which read was ours — was
    # written to a notifier that was never wired, so the first live refusal
    # arrived as a bare tally with nothing behind it. R8 is not just about
    # writing the cause down; it is about it reaching the operator.
    say = progress or (lambda _line: None)
    larder_preparer = LarderPreparer(
        finder=finder, fetch_image=fetch_artwork_once, describe=vision,
        write_clue_one=write_clue_one,
        epoch_id=s.target_epoch_id, key=s.target_pool_key,
        used_hmacs=lambda: repo.used_target_hmacs(),
        now_iso=_now_iso, rng=rng, notify=say)

    return TargetWiring(finder=finder, larder_store=larder_store,
                        notify=say,
                        prepared_store=prepared_store,
                        larder_preparer=larder_preparer,
                        pool_key=s.target_pool_key,
                        ports=ports, pipeline=pipeline, epoch=epoch,
                        snapshot_store=snapshot_store,
                        scan_blocks=int(s.target_scan_blocks),
                        writability_rates=s.target_writability_rate_map,
                        uniqueness_rates=s.target_uniqueness_rate_map,
                        cap_exempt=cap_exempt, thresholds=thresholds,
                        sample_per_stratum=int(s.target_sample_per_stratum),
                        fetch_artwork=fetch_artwork,
                        market_surface=market_surface)


# Type alias for main.py readers
Notify = Callable[[str], None]

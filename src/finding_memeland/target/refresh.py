"""Snapshot refresh — builds the curated pool, weekly, OFF the hunt path.

Division of labour (invariants agreed 04/09):
  · the MARKETPLACE lists candidates (which tokens exist on the curated
    platforms) and answers name-uniqueness — batch calls, disconnected in
    time from any hunt, so no query pattern ever points at a target
  · the CHAIN + a generic gateway give the canonical metadata — the
    commitment hash is computed from tokenURI resolution, never from a
    marketplace's cached view of it
  · the SNAPSHOT is what hunts draw from; nothing here runs at /launch

Filter order per candidate (cheapest first, all fail-closed — an entry that
cannot be verified is an entry that does not enter the pool):
  1. base name (trailing serial stripped) has >= 2 real words       [local]
  2. canonical metadata resolves, is content-addressed (the keyed
     fetcher answers None for plain http) and has an image          [chain]
  3. base name is unique WITHIN the pulled pool — a name seen twice
     across the platforms kills every bearer                        [local]
  4. owner is an EOA                                                [chain]
Global marketplace uniqueness is NOT here (Opus, 06/09): quota-priced,
it is checked at draw time on the drawn candidate (hunt.select_judged) and
enters the gate as a sampled rate per stratum.

The anti-circularity rule applies here above all (Opus, 04/09): when the
pool comes back under the gate, the fix is MORE PLATFORMS in the epoch's
config — these filters do not loosen.

Every effectful collaborator is injected; the OpenSea lister below is the
real adapter for the documented v2 shape and, like every real adapter in
this codebase, is exercised against the live API before production use.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol

from .selector import metadata_hash, name_qualifies, normalize_name
from .snapshot import CurationEpoch, Snapshot, SnapshotEntry


_IPFS_GATEWAY_PATH = re.compile(r"^https?://[^/]+/ipfs/(qm[1-9a-z]{44}|baf[a-z0-9]{20,})",
                                re.IGNORECASE)
_BARE_CID = re.compile(r"^(Qm[1-9A-HJ-NP-Za-km-z]{44}|baf[a-zA-Z0-9]{20,})")


def uri_is_content_addressed(uri: str | None) -> bool:
    """Only content-addressed metadata enters the pool. Three shapes
    qualify, all measured in the wild (2026-09-05 capture):
      · ipfs:// and data: (the obvious ones)
      · a BARE CID with no scheme (Async Art writes tokenURIs like
        'QmWh59…')
      · an http(s) GATEWAY URL whose path is /ipfs/<cid> (SuperRare's
        ipfs.pixura.io — the CID seals the content; the gateway is mere
        transport and can die, as pixura's DNS did, without the content
        becoming unverifiable: any gateway serves the same bytes)
    A plain http(s) URL without a CID serves whatever the host feels like
    today and would fire the mutation-void rule on an honest hunt (Opus,
    04/09) — excluded. The production resolver must return None for it."""
    if not uri:
        return False
    u = uri.strip()
    low = u.lower()
    if low.startswith("ipfs://") or low.startswith("data:"):
        return True
    if _BARE_CID.match(u):
        return True
    return bool(_IPFS_GATEWAY_PATH.match(u))


_CID_PATH = re.compile(r"(Qm[1-9A-HJ-NP-Za-km-z]{44}|baf[a-zA-Z0-9]{20,})((?:/[^?#]*)?)")


def content_id(uri: str | None) -> str | None:
    """The CANONICAL content identity of a token URI — what the live check
    compares (Opus, 06/09): the CID plus the path inside it, with the
    transport stripped. `ipfs://CID/x`, `https://any-gateway/ipfs/CID/x`
    and the bare `CID/x` all yield 'ipfs:CID/x' — a gateway migration with
    the same CID (what happened to SuperRare's pixura) is NOT a mutation.
    `data:` URIs ARE their content: 'data:' + sha256 of the URI. None for
    anything not content-addressed — which the refresh rejects and the
    live check reads as MUTATED (the URI stopped being content-addressed)."""
    if not uri:
        return None
    u = uri.strip()
    low = u.lower()
    if low.startswith("data:"):
        return "data:" + hashlib.sha256(u.encode("utf-8")).hexdigest()
    if low.startswith("ipfs://"):
        rest = u[7:]
        if rest.lower().startswith("ipfs/"):
            rest = rest[5:]
        m = _CID_PATH.match(rest.lstrip("/"))
    elif _BARE_CID.match(u):
        m = _CID_PATH.match(u)
    elif _IPFS_GATEWAY_PATH.match(u):
        m = _CID_PATH.search(u)
    else:
        return None
    if not m:
        return None
    path = m.group(2).rstrip("/")
    return f"ipfs:{m.group(1)}{path}"


@dataclass(frozen=True)
class PlatformItem:
    """One token as a platform lists it. `name` here is only a pre-filter
    hint — the canonical name/metadata come from the chain resolver. `chain`
    is PER ITEM (Opus review, 05/09): the pool is multi-chain, so the chain
    travels with the candidate from listing to snapshot to Target.id() —
    a constant would seal wrong commitments."""

    platform: str
    chain: str
    contract: str
    token_id: int
    name: str


@dataclass(frozen=True)
class TokenRead:
    """What the refresh reads per token, in ONE chain round-trip: the raw
    tokenURI (kept for the snapshot: the live check compares its content
    id, the void post publishes it) and the resolved metadata."""

    token_uri: str
    metadata: dict | None


class PlatformLister(Protocol):
    """Yields every item of one curated platform (paginated underneath).
    Raises on transport failure — the refresh reports and keeps the previous
    snapshot rather than building a silently smaller pool."""

    name: str

    def items(self) -> Iterable[PlatformItem]: ...


@dataclass
class RefreshReport:
    """Stage counts for the gate and the operator log. Counts only — no
    entry is ever named here; this reaches Telegram."""

    pulled: int = 0
    after_name: int = 0
    after_pool_dedupe: int = 0
    after_metadata: int = 0
    after_eoa: int = 0
    pool_size: int = 0
    unverifiable: int = 0
    transport: int = 0     # metadata fetches lost to RPC/gateway trouble (skipped)
    not_content_addressed: int = 0   # tokenURI or image on a mutable host
    bad_read: int = 0      # a read that raised something that is NOT transport (skipped)
    # gross size per stratum as the lister saw it (totalSupply for an
    # enumerable contract, listed count otherwise) — diagnostic only, so a
    # sampled snapshot reads "15k of 115k" and not "the stratum collapsed"
    gross: dict = field(default_factory=dict)


class RefreshFailed(RuntimeError):
    """A platform could not be listed. Fail-closed for the BUILD, not the
    game: the caller keeps serving the previous snapshot."""


def _is_rate_limited(e: BaseException) -> bool:
    """A 429 / throttled answer anywhere in the exception chain: the RPC
    adapter labels it 'throttled' (JSON-RPC -32005/429 bodies); an HTTP 429
    from urllib carries `.code` on the cause."""
    seen = 0
    cur: BaseException | None = e
    while cur is not None and seen < 6:
        if getattr(cur, "code", None) == 429 or getattr(cur, "status", None) == 429:
            return True
        text = str(cur).lower()
        if "throttled" in text or "429" in text or "rate limit" in text:
            return True
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return False


class RefreshJob:
    """Builds a Snapshot for one epoch from the epoch's platform listers.

    Collaborators take the CHAIN first — a multi-chain pool means each
    lookup must know which chain's RPC/marketplace view to consult:
    fetch_token(chain, contract, token_id) -> TokenRead | None
                                              (None = tokenURI reverts)
    owner_is_eoa(chain, contract, token_id)   -> bool | None
    now_iso() -> str                                     (built_at stamp)

    CONTENT-ADDRESSED, TWICE (Opus, 06/09): the tokenURI must be
    content-addressed (else the metadata is whatever the host serves
    today), AND the metadata's `image` must be too — the commitment seals
    the metadata hash, and a metadata whose image is a mutable https:// lets
    the owner swap the picture with tokenURI and hash untouched: the live
    check says intact while the artwork the clues describe is gone. Both
    are checked here, both are rejections, both are counted.

    GLOBAL name uniqueness is NOT a refresh filter any more (Opus, 06/09,
    5/6 review): it is a quota-priced marketplace call per survivor —
    60-80k calls per weekly refresh, millions a year, to reconfirm a
    property that almost never changes and that is irrelevant for every
    entry that is never drawn. It follows writability's path: the gate
    certifies a SAMPLED uniqueness_rate per stratum, and select_judged
    checks the drawn candidate lazily (hunt.py), at the moment it matters
    rather than seven days before. The refresh keeps the IN-POOL dedupe
    (local, free) — the part a marketplace cannot do for us.
    """

    def __init__(
        self,
        *,
        listers: tuple[PlatformLister, ...],
        fetch_token: Callable[[str, str, int], "TokenRead | None"],
        owner_is_eoa: Callable[[str, str, int], bool | None],
        now_iso: Callable[[], str],
        max_transport_share: float = 0.02,
        min_transport_failures: int = 20,
        workers: int = 1,
        retries: int = 0,
        backoff_s: float = 1.0,
        progress: Callable[[str], None] | None = None,
        progress_every: int = 5_000,
        chunk: int = 250,
        pause_s: float = 5.0,
        max_bad_read_share: float = 0.05,
    ):
        """13/09, after the first live refresh (847k tokens, 40 h, 53% of
        the metadata reads lost to 429s and to a quota cap), reviewed by
        Opus the same day:
          · reads run in CHUNKS of `chunk` items on a pool of `workers`;
            the transport ceiling (2% and ≥ min_transport) is re-evaluated
            after EVERY chunk, so an outage fails the build within one
            chunk, never at the end of the job (P1-1). The chunk is the
            blast radius (the pool pre-submits it), hence 250, not 2,000;
          · ZERO-SERVED breaker (Opus, volta 2): once `min_transport`
            reads have failed and NONE has been served, the build stops at
            once, inside the chunk — a job that could not read a single
            piece has no evidence it can read at all (R2 applied to the
            refresh itself); with the shared pause this fires in minutes,
            not after a chunk of 60 s waits;
          · a 429 / "throttled" answer is not retried faster — it sets a
            pause shared by all workers (`pause_s`, doubling up to 60 s
            while it keeps happening); 5xx/timeouts retry per item with
            backoff (P1-2);
          · a read that raises something that is NOT transport is counted
            (`bad_read`) and skipped, with its own ceiling, like the owner
            check — one strange token never throws away a paid job (P1-3);
          · `progress` gets counts-only lines every `progress_every` items."""
        self._listers = listers
        self._fetch_token = fetch_token
        self._owner_is_eoa = owner_is_eoa
        self._now_iso = now_iso
        self._max_transport_share = max_transport_share
        self._min_transport = min_transport_failures
        self._workers = max(1, int(workers))
        self._retries = max(0, int(retries))
        self._backoff = max(0.0, float(backoff_s))
        self._progress = progress
        self._every = max(1, int(progress_every))
        self._chunk = max(1, int(chunk))
        self._pause_s = max(0.0, float(pause_s))
        self._max_bad_share = max_bad_read_share
        self._pause_lock = threading.Lock()
        self._pause_until = 0.0
        self._pause_len = 0.0

    def _note(self, text: str) -> None:
        if self._progress is not None:
            try:
                self._progress(text)
            except Exception:  # noqa: BLE001 — a progress line never breaks a build
                pass

    # -- rate limit: one shared pause, never a faster retry (P1-2) ----------- #

    def _wait_pause(self) -> None:
        while True:
            with self._pause_lock:
                remaining = self._pause_until - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 1.0))

    def _throttled(self) -> None:
        """The first worker to see a 429 opens a pause for everyone; while
        the provider keeps saying it, the pause doubles (cap 60 s)."""
        with self._pause_lock:
            now = time.monotonic()
            if self._pause_until > now:
                return                     # a pause is already running
            self._pause_len = min(60.0, self._pause_len * 2 if self._pause_len else self._pause_s)
            self._pause_until = now + self._pause_len

    def _served(self) -> None:
        with self._pause_lock:
            self._pause_len = 0.0

    def _read_with_retries(self, it: "PlatformItem"):
        """fetch_token with: shared pause on rate limit; per-item backoff on
        other transport trouble; raises the last ChainUnavailable."""
        from .sources import ChainUnavailable
        for attempt in range(self._retries + 1):
            self._wait_pause()
            try:
                out = self._fetch_token(it.chain, it.contract, it.token_id)
            except ChainUnavailable as e:
                if attempt >= self._retries:
                    raise
                if _is_rate_limited(e):
                    self._throttled()      # slow everyone down, then retry
                else:
                    time.sleep(self._backoff * (3 ** attempt))
                continue
            self._served()
            return out
        raise AssertionError("unreachable")

    def _transport_ceiling_hit(self, report: "RefreshReport", attempted: int) -> bool:
        return (report.transport >= self._min_transport
                and report.transport > self._max_transport_share * max(attempted, 1))

    def _map(self, fn, items: list) -> Iterable:
        """Apply `fn` over `items` with the worker pool, yielding (item,
        result | exception) as they complete; order is irrelevant here."""
        if self._workers == 1:
            for it in items:
                try:
                    yield it, fn(it)
                except Exception as e:  # noqa: BLE001 — classified by the caller
                    yield it, e
            return

        def _safe(it):
            try:
                return it, fn(it)
            except Exception as e:  # noqa: BLE001
                return it, e
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            yield from pool.map(_safe, items)

    @staticmethod
    def _qualify(it, read, resolved: list, report: "RefreshReport",
                 epoch: CurationEpoch) -> None:
        """Stage-2 filters for one read token (content-addressed twice,
        canonical base name); appends to `resolved` when it qualifies."""
        meta = read.metadata
        if not (isinstance(meta, dict) and meta.get("image")):
            return
        # content-addressed, twice: the URI and the image (see class doc)
        if content_id(read.token_uri) is None \
                or not uri_is_content_addressed(str(meta.get("image"))):
            report.not_content_addressed += 1
            return
        base = normalize_name(str(meta.get("name") or "").strip())
        if not name_qualifies(base, min_words=epoch.min_words):
            return
        resolved.append((it, base, read))

    def build(self, epoch: CurationEpoch) -> tuple[Snapshot, RefreshReport]:
        from .sources import ChainUnavailable   # local: sources imports PlatformItem

        report = RefreshReport()

        # -- pull everything first: pool-wide dedupe needs the full view ---- #
        pulled: list[PlatformItem] = []
        for lister in self._listers:
            try:
                before = len(pulled)
                pulled.extend(lister.items())
                listed = len(pulled) - before
                gross = getattr(lister, "gross_total", None)
                report.gross[lister.name] = int(gross) if gross else listed
                self._note(f"refresh: {lister.name} listado — {listed:,} tokens"
                           + (f" (de {int(gross):,})" if gross and gross > listed else ""))
            except Exception as e:  # noqa: BLE001
                raise RefreshFailed(
                    f"platform {lister.name!r} unlistable "
                    f"({type(e).__name__}) — snapshot NOT rebuilt; keep "
                    "serving the previous one"
                ) from e
        report.pulled = len(pulled)

        # -- 1. cheap prefilter, ONLY for items whose lister supplied a
        # name. Chain-native listers (sources.py) supply name="" by design
        # — their canonical name comes from the metadata resolver — so an
        # empty name defers to stage 2 instead of failing here.
        prefiltered = [it for it in pulled
                       if not it.name
                       or name_qualifies(normalize_name(it.name),
                                         min_words=epoch.min_words)]
        report.after_name = len(prefiltered)

        # -- 2. canonical metadata + canonical base name -------------------- #
        # A transport failure on ONE item (RPC blip, gateway 5xx) skips that
        # item — it is retried next refresh — and is COUNTED. Past a share of
        # the attempts the build fails as a whole (RefreshFailed → previous
        # snapshot keeps serving): a systematic outage must never produce a
        # pool that is merely, honestly, smaller — the gate would read it as
        # a collapsed stratum and the operator would go widen sourcing that
        # is not the problem. (Found 06/09 checking Foundation's gateway note.)
        resolved: list[tuple[PlatformItem, str, TokenRead]] = []
        self._note(f"refresh: {len(prefiltered):,} tokens listados — a ler metadata")
        done = 0
        served = 0
        # chunked: the pool never holds more than one chunk of futures, and
        # the ceilings are checked between chunks (P1-1). The counters below
        # are touched ONLY here, on the consuming thread — the workers
        # return values, they never share state.
        for start in range(0, len(prefiltered), self._chunk):
            chunk = prefiltered[start:start + self._chunk]
            for it, read in self._map(self._read_with_retries, chunk):
                done += 1
                if done % self._every == 0:
                    self._note(f"refresh: {done:,}/{len(prefiltered):,} lidos, "
                               f"{len(resolved):,} qualificam, "
                               f"{report.transport:,} transporte, "
                               f"{report.bad_read:,} ilegíveis")
                if isinstance(read, ChainUnavailable):
                    report.transport += 1
                    if served == 0 and report.transport >= self._min_transport:
                        raise RefreshFailed(
                            f"{report.transport} metadata fetches lost to "
                            "transport and NONE served yet — the refresh "
                            "cannot read at all (RPC/gateway/quota); stopped "
                            f"after {done:,} of {len(prefiltered):,}; snapshot "
                            "NOT rebuilt; keep serving the previous one")
                    continue
                served += 1
                if isinstance(read, Exception):
                    report.bad_read += 1       # a strange token, not an outage (P1-3)
                    continue
                if read is None:
                    continue
                self._qualify(it, read, resolved, report, epoch)
            if self._transport_ceiling_hit(report, done):
                raise RefreshFailed(
                    f"{report.transport} of {done} metadata fetches lost to "
                    "transport — outage, not a smaller pool; stopped after "
                    f"{done:,} of {len(prefiltered):,}; snapshot NOT rebuilt; "
                    "keep serving the previous one")
            if (report.bad_read >= self._min_transport
                    and report.bad_read > self._max_bad_share * max(done, 1)):
                raise RefreshFailed(
                    f"{report.bad_read} of {done} reads unreadable (not "
                    "transport) — a parser/shape problem, not a smaller pool; "
                    "snapshot NOT rebuilt; keep serving the previous one")
        report.after_metadata = len(resolved)

        # -- 3. in-pool dedupe on the CANONICAL base name: a base name seen
        # twice kills every bearer (what clues cipher must be unique) ------ #
        counts: dict[str, int] = {}
        for _, base, _r in resolved:
            counts[base.casefold()] = counts.get(base.casefold(), 0) + 1
        resolved = [(it, base, read) for it, base, read in resolved
                    if counts[base.casefold()] == 1]
        report.after_pool_dedupe = len(resolved)

        # -- 4. owner is an EOA (chain call, our RPC). Global uniqueness is
        # deliberately NOT here — see the class docstring ------------------- #
        entries: list[SnapshotEntry] = []
        self._note(f"refresh: {len(resolved):,} únicos no pool — a verificar donos")
        by_key = {(it.chain, it.contract, it.token_id): (it, base, read)
                  for it, base, read in resolved}
        keys = list(by_key)
        for key, eoa in self._map(
                lambda k: self._owner_is_eoa(k[0], k[1], k[2]), keys):
            it, base, read = by_key[key]
            meta = read.metadata
            if isinstance(eoa, Exception) or eoa is None:
                report.unverifiable += 1
                continue
            if eoa is not True:
                continue
            report.after_eoa += 1
            entries.append(SnapshotEntry(
                chain=it.chain,
                contract=it.contract.lower(),
                token_id=it.token_id,
                name=base,
                name_onchain=str(meta.get("name") or "").strip(),
                metadata=meta,
                metadata_sha256=metadata_hash(meta),
                platform=it.platform,
                token_uri=read.token_uri,
                content_id=content_id(read.token_uri) or "",
            ))
        report.pool_size = len(entries)

        snap = Snapshot(epoch_id=epoch.epoch_id, built_at=self._now_iso(),
                        entries=entries)
        return snap, report


# --------------------------------------------------------------------------- #
# Real adapter — OpenSea v2 (needs a live key; verify against the API before  #
# production, same discipline as every measured adapter in this codebase)     #
# --------------------------------------------------------------------------- #


class OpenSeaContractLister:
    """GET /api/v2/chain/{chain}/contract/{address}/nfts — paginated with a
    `next` cursor, key in X-API-KEY.

    MEASURED live 2026-09-04 (scripts/verificar_opensea.py, 3 Base
    contracts): `nfts` list with `identifier` and `name` exactly as
    documented, `next` cursor present, and two facts the docs don't state:
      · Cloudflare returns 403 (error 1010) to requests WITHOUT a
        User-Agent, valid key or not — hence the header below
      · quota comes back in x-ratelimit-limit/-remaining/-reset headers
        (120-request window on the approved key)

    `http_get(url, headers: dict) -> str` is injected."""

    USER_AGENT = "fml-refresh-probe/1.0"   # measured: passes Cloudflare

    def __init__(self, *, http_get, api_key: str, contract: str,
                 platform: str, chain: str,
                 base_url: str = "https://api.opensea.io",
                 page_limit: int = 200, max_pages: int = 500):
        if not api_key:
            raise ValueError("OpenSeaContractLister needs an api key")
        self.name = platform
        self._get = http_get
        self._key = api_key
        self._contract = contract
        self._chain = chain
        self._base = base_url.rstrip("/")
        self._limit = page_limit
        self._max_pages = max_pages

    def items(self) -> Iterable[PlatformItem]:
        cursor = ""
        for _ in range(self._max_pages):
            url = (f"{self._base}/api/v2/chain/{self._chain}/contract/"
                   f"{self._contract}/nfts?limit={self._limit}")
            if cursor:
                url += f"&next={cursor}"
            raw = self._get(url, {"X-API-KEY": self._key,
                                  "Accept": "application/json",
                                  "User-Agent": self.USER_AGENT})
            payload = json.loads(raw or "{}")
            for nft in payload.get("nfts", []) or []:
                try:
                    token_id = int(nft.get("identifier"))
                except (TypeError, ValueError):
                    continue
                yield PlatformItem(
                    platform=self.name,
                    chain=self._chain,
                    contract=self._contract,
                    token_id=token_id,
                    name=str(nft.get("name") or ""),
                )
            cursor = payload.get("next") or ""
            if not cursor:
                return


class FakeLister:
    def __init__(self, name: str, items: list[PlatformItem], *,
                 raises: bool = False):
        self.name = name
        self._items = items
        self._raises = raises

    def items(self) -> Iterable[PlatformItem]:
        if self._raises:
            raise RuntimeError("platform unreachable")
        return list(self._items)

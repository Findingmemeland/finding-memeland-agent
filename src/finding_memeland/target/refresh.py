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
from dataclasses import dataclass
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


class RefreshFailed(RuntimeError):
    """A platform could not be listed. Fail-closed for the BUILD, not the
    game: the caller keeps serving the previous snapshot."""


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
    ):
        self._listers = listers
        self._fetch_token = fetch_token
        self._owner_is_eoa = owner_is_eoa
        self._now_iso = now_iso
        self._max_transport_share = max_transport_share
        self._min_transport = min_transport_failures

    def build(self, epoch: CurationEpoch) -> tuple[Snapshot, RefreshReport]:
        from .sources import ChainUnavailable   # local: sources imports PlatformItem

        report = RefreshReport()

        # -- pull everything first: pool-wide dedupe needs the full view ---- #
        pulled: list[PlatformItem] = []
        for lister in self._listers:
            try:
                pulled.extend(lister.items())
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
        for it in prefiltered:
            try:
                read = self._fetch_token(it.chain, it.contract, it.token_id)
            except ChainUnavailable:
                report.transport += 1
                continue
            if read is None:
                continue
            meta = read.metadata
            if not (isinstance(meta, dict) and meta.get("image")):
                continue
            # content-addressed, twice: the URI and the image (see class doc)
            if content_id(read.token_uri) is None \
                    or not uri_is_content_addressed(str(meta.get("image"))):
                report.not_content_addressed += 1
                continue
            base = normalize_name(str(meta.get("name") or "").strip())
            if not name_qualifies(base, min_words=epoch.min_words):
                continue
            resolved.append((it, base, read))
        report.after_metadata = len(resolved)
        if (report.transport >= self._min_transport
                and report.transport > self._max_transport_share * max(len(prefiltered), 1)):
            raise RefreshFailed(
                f"{report.transport} of {len(prefiltered)} metadata fetches lost "
                "to transport — outage, not a smaller pool; snapshot NOT "
                "rebuilt; keep serving the previous one")

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
        for it, base, read in resolved:
            meta = read.metadata
            eoa = self._owner_is_eoa(it.chain, it.contract, it.token_id)
            if eoa is None:
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

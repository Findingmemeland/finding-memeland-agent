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
  2. base name is unique WITHIN the pulled pool — a name seen twice
     across the platforms kills every bearer                        [local]
  3. canonical metadata resolves and has an image                   [chain]
  4. owner is an EOA                                                [chain]
  5. base name is unique on the marketplace                         [API]
Global uniqueness runs LAST because it is the only quota-priced filter:
everything the local and chain checks can kill dies before spending a call.

The anti-circularity rule applies here above all (Opus, 04/09): when the
pool comes back under the gate, the fix is MORE PLATFORMS in the epoch's
config — these filters do not loosen.

Every effectful collaborator is injected; the OpenSea lister below is the
real adapter for the documented v2 shape and, like every real adapter in
this codebase, is exercised against the live API before production use.
"""

from __future__ import annotations

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


class RefreshFailed(RuntimeError):
    """A platform could not be listed. Fail-closed for the BUILD, not the
    game: the caller keeps serving the previous snapshot."""


class RefreshJob:
    """Builds a Snapshot for one epoch from the epoch's platform listers.

    Collaborators take the CHAIN first — a multi-chain pool means each
    lookup must know which chain's RPC/marketplace view to consult:
    fetch_metadata(chain, contract, token_id) -> dict | None
    owner_is_eoa(chain, contract, token_id)   -> bool | None
    name_is_unique(base, chain, contract, token_id) -> bool | None
    now_iso() -> str                                     (built_at stamp)
    """

    def __init__(
        self,
        *,
        listers: tuple[PlatformLister, ...],
        fetch_metadata: Callable[[str, str, int], dict | None],
        owner_is_eoa: Callable[[str, str, int], bool | None],
        name_is_unique: Callable[[str, str, str, int], bool | None],
        now_iso: Callable[[], str],
    ):
        self._listers = listers
        self._fetch_metadata = fetch_metadata
        self._owner_is_eoa = owner_is_eoa
        self._name_is_unique = name_is_unique
        self._now_iso = now_iso

    def build(self, epoch: CurationEpoch) -> tuple[Snapshot, RefreshReport]:
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
        resolved: list[tuple[PlatformItem, str, dict]] = []
        for it in prefiltered:
            meta = self._fetch_metadata(it.chain, it.contract, it.token_id)
            if not (isinstance(meta, dict) and meta.get("image")):
                continue
            base = normalize_name(str(meta.get("name") or "").strip())
            if not name_qualifies(base, min_words=epoch.min_words):
                continue
            resolved.append((it, base, meta))
        report.after_metadata = len(resolved)

        # -- 3. in-pool dedupe on the CANONICAL base name: a base name seen
        # twice kills every bearer (what clues cipher must be unique) ------ #
        counts: dict[str, int] = {}
        for _, base, _m in resolved:
            counts[base.casefold()] = counts.get(base.casefold(), 0) + 1
        resolved = [(it, base, meta) for it, base, meta in resolved
                    if counts[base.casefold()] == 1]
        report.after_pool_dedupe = len(resolved)

        # -- 4..5 per candidate, quota-priced check last -------------------- #
        entries: list[SnapshotEntry] = []
        for it, base, meta in resolved:
            eoa = self._owner_is_eoa(it.chain, it.contract, it.token_id)
            if eoa is None:
                report.unverifiable += 1
                continue
            if eoa is not True:
                continue
            report.after_eoa += 1
            uniq = self._name_is_unique(base, it.chain, it.contract, it.token_id)
            if uniq is None:
                report.unverifiable += 1
                continue
            if uniq is not True:
                continue
            entries.append(SnapshotEntry(
                chain=it.chain,
                contract=it.contract.lower(),
                token_id=it.token_id,
                name=base,
                name_onchain=str(meta.get("name") or "").strip(),
                metadata=meta,
                metadata_sha256=metadata_hash(meta),
                platform=it.platform,
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
                 platform: str, chain: str = "base",
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

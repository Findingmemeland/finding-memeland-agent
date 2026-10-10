"""Search guard — the mechanical form of "no puzzle-phase clue may be a
search" (Opus, 2026-09-04).

Why mechanical and not a prompt instruction: prompts are how emoji were
banned, and emoji appeared anyway; difficulty in this codebase is enforced by
proof, not by prompt (guardrails.py, the blind solver). This guard runs the
attack it defends against: it takes the candidate clue TEXT, uses it as a
marketplace search query, and REJECTS the clue if the target surfaces in the
results. That closes the whole class in one test — a clue quoting the name, a
too-literal art description matching indexed traits, a themed-collection tell
that resolves to the collection and thence the target.

Fail-closed, like every guardrail here: a clue whose searchability cannot be
verified (API down after retries) is NOT publishable. Clue cadence is a random
band, so a delayed piece is a non-event; a leaked piece is forever.

THE CANARY (Opus re-review, 05/09 — P0-4). The first version searched with a
chain fixed at construction ("BASE") while every epoch-1 target lives on
Ethereum: the target could never surface, "absent from 25 results" read as
approval, and EVERY clue passed — including one quoting the name verbatim.
A guard that cannot see its own target has no authority to call a clue
safe. So, before testing the clue, the guard searches the target's exact
on-chain NAME on the target's own chain and REQUIRES the target to appear.
If it doesn't, the index is blind to the target — wrong chain, unindexed
token, key without permissions, malformed filter, or the next variant of
the same family — and the guard refuses (ok=False, found=None) instead of
approving. The chain is never configured: it is derived from the target id
the caller passes, so it cannot drift from the commitment.

The marketplace adapter is injected; RaribleSearch mirrors the measured
request shape from relic_findability.py (2026-08-25: X-API-KEY header,
`fullText` filter, 429 retry); OpenSeaSearch (10/09) is the surface the
wiring prefers — same ports, shape pinned by fixtures opensea_search_*.json.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class MarketSearch(Protocol):
    """Full-text search over the marketplace's NFT index, on ONE chain.

    `chain` is the marketplace's upper-cased blockchain enum ('ETHEREUM',
    'BASE', …), derived by the guard from the target id — adapters never
    hold a chain of their own. Returns the item ids ('CHAIN:0xcontract:
    tokenId', upper-cased chain, as Rarible formats them) surfaced for
    `text`. Raises on transport failure — the guard turns that into a
    fail-closed verdict, adapters never guess."""

    def item_ids(self, text: str, *, chain: str) -> set[str]: ...

    # The marketplace's OWN cap on a query, in characters; 0 = none.
    # MEASURED 17/09: OpenSea answers `{"errors": ["Query must not exceed
    # 100 characters"]}` with an HTTP 400. The limit belongs to the
    # adapter because it is the marketplace's; what to DO about it belongs
    # to the guard, because it is a question about the clue.
    max_query_chars: int


@dataclass(frozen=True)
class SearchGuardVerdict:
    """`ok` is the ONLY field the clue pipeline may act on: False means the
    piece is rejected, whether because the target surfaced (`found=True`) or
    because searchability could not be verified (`found=None` — transport
    failure OR a blind canary). `detail` is for the operator log — it never
    carries the target's name, the clue text, or the chain."""

    ok: bool
    found: bool | None
    detail: str
    # TWO VERY DIFFERENT THINGS HIDE BEHIND found=None, and until 17/09 the
    # callers could not tell them apart. `blind=True` means THE CANARY
    # FAILED: the index does not surface this piece even when searched by
    # its own on-chain name. That is a property of the TARGET, permanent
    # until someone indexes it — not our outage. `blind=False` with
    # found=None is the marketplace not answering, which is ours and passes.
    # The distinction decides whether a candidate is kept or dropped.
    blind: bool = False


class _Unverifiable(Exception):
    pass


class ClueSearchGuard:
    """Reject any clue that works as a search query for the target."""

    def __init__(self, *, search: MarketSearch, retries: int = 2,
                 sleep_s: float = 2.0):
        self._search = search
        self._retries = retries
        self._sleep = sleep_s

    def check(self, clue_text: str, *, target_item_id: str,
              target_name_onchain: str) -> SearchGuardVerdict:
        """`target_item_id` is 'CHAIN:0xcontract:tokenId' (Target.id(), any
        case); `target_name_onchain` is the exact metadata name (Target.
        name_onchain) — the canary query. The clue text goes to the
        marketplace verbatim — no keyword extraction: we test the exact
        artefact the public would see."""
        want = _canonical(target_item_id)
        chain = _chain_of(want)
        if not chain or not target_name_onchain.strip():
            return SearchGuardVerdict(
                ok=False, found=None,
                detail="target id or on-chain name missing — the guard "
                       "cannot run its canary; fail-closed, piece not "
                       "publishable")

        # -- 1. canary: can this index see the target at all? ------------- #
        try:
            seen = self._search_all(target_name_onchain, chain)
        except _Unverifiable as e:
            return SearchGuardVerdict(ok=False, found=None, detail=str(e))
        if want not in seen:
            return SearchGuardVerdict(
                ok=False, found=None, blind=True,
                detail=f"canary failed: the target does not surface for its "
                       f"own on-chain name ({len(seen)} results) — the index "
                       "is blind to the target (chain filter, indexing, key "
                       "or request shape); a blind guard approves nothing. "
                       "Fail-closed, piece not publishable")

        # -- 2. the clue itself ------------------------------------------- #
        try:
            ids = self._search_all(clue_text, chain)
        except _Unverifiable as e:
            return SearchGuardVerdict(ok=False, found=None, detail=str(e))
        if want in ids:
            return SearchGuardVerdict(
                ok=False, found=True,
                detail="clue text surfaces the target on the marketplace "
                       "— the piece IS a search; rejected")
        return SearchGuardVerdict(
            ok=True, found=False,
            detail=f"canary ok; target absent from {len(ids)} search results")

    def _search_all(self, text: str, chain: str) -> set[str]:
        """The WHOLE clue, tested — in as many queries as the marketplace's
        limit forces.

        THE BUG THIS EXISTS FOR (measured 17/09): OpenSea rejects any query
        over 100 characters with an HTTP 400. Every clue is longer than
        that. So every clue-phase check 400ed, every 400 read as "could not
        verify", and the guard has been fail-closed — holding hunts — since
        OpenSea became the surface on 10/09. The canary never showed it: a
        piece NAME is two or three words and always fit.

        Truncating to 100 would have been one line and a silent weakening
        of a guard: the untested tail is exactly where a writer puts the
        literal description. Keyword extraction is the other easy answer
        and the file's first rule forbids it — we test the artefact the
        public would see, not our summary of it.

        So the clue is cut into OVERLAPPING windows on word boundaries and
        every one is searched; the target surfacing in ANY of them rejects
        the clue. The overlap is what keeps a phrase that straddles a cut
        from escaping. Cost: two or three queries instead of one, against a
        120/minute quota."""
        limit = int(getattr(self._search, "max_query_chars", 0) or 0)
        if not limit or len(text) <= limit:
            return self._search_with_retries(text, chain)
        out: set[str] = set()
        for window in _windows(text, limit):
            out |= self._search_with_retries(window, chain)
        return out

    def _search_with_retries(self, text: str, chain: str) -> set[str]:
        last_err = "unknown"
        for attempt in range(self._retries + 1):
            try:
                return {_canonical(i)
                        for i in self._search.item_ids(text, chain=chain)}
            except Exception as e:  # noqa: BLE001 — retry, then fail closed
                last_err = str(e)[:120]
                if attempt < self._retries:
                    time.sleep(self._sleep * (attempt + 1))
        raise _Unverifiable(
            f"unverifiable after {self._retries + 1} attempts ({last_err}) "
            "— fail-closed, piece not publishable")


def _windows(text: str, limit: int, *, overlap_words: int = 4) -> list[str]:
    """`text` cut into pieces of at most `limit` characters, on word
    boundaries, each sharing its last few words with the next.

    The overlap is the point: a cut between "the keeper's" and "last light"
    would let the phrase through untested, and a phrase is exactly what a
    marketplace index matches. A single word longer than the limit is
    truncated — nothing else can be done with it, and it is not a phrase."""
    words = text.split()
    if not words:
        return []
    out: list[str] = []
    cur: list[str] = []
    for word in words:
        word = word[:limit]
        candidate = " ".join(cur + [word])
        if cur and len(candidate) > limit:
            out.append(" ".join(cur))
            cur = cur[-overlap_words:] if len(cur) > overlap_words else cur[:]
            while cur and len(" ".join(cur + [word])) > limit:
                cur.pop(0)
        cur.append(word)
    if cur:
        out.append(" ".join(cur))
    return out


def _canonical(item_id: str) -> str:
    return item_id.strip().upper()


def _chain_of(canonical_item_id: str) -> str:
    """'ETHEREUM:0X…:7' -> 'ETHEREUM'. Empty when the id has no chain."""
    head, sep, _rest = canonical_item_id.partition(":")
    return head if sep and head and not head.startswith("0X") else ""


# --------------------------------------------------------------------------- #
# Real adapter — needs a live connection (NOT sandbox-testable)                #
# --------------------------------------------------------------------------- #


class RaribleSearch:
    """items/search with the MEASURED request shape (relic_findability.py,
    2026-08-25): X-API-KEY header (Bearer returns 403), `fullText` filter
    (`text` misses targets). Raises on failure — the guard owns fail-closed.

    No chain here by design (P0-4): the blockchains filter comes from the
    guard per call, derived from the target. Rarible's enum is the
    upper-cased chain slug ('ETHEREUM', 'BASE', 'POLYGON').

    `http_post(url, body: bytes, headers: dict) -> str` is injected."""

    max_query_chars = 0          # none measured on this surface

    def __init__(self, *, http_post, api_key: str,
                 base_url: str = "https://api.rarible.org/v0.1",
                 size: int = 25):
        if not api_key:
            raise ValueError("RaribleSearch needs an api key — without one the "
                             "guard would fail every clue, stalling the hunt")
        self._post = http_post
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._size = size

    def item_ids(self, text: str, *, chain: str) -> set[str]:
        body = json.dumps({
            "size": self._size,
            "filter": {"fullText": {"text": text},
                       "blockchains": [chain.upper()]},
        }).encode()
        raw = self._post(f"{self._base}/items/search", body, {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-KEY": self._key,
        })
        payload = json.loads(raw or "{}")
        return {str(item.get("id", "")) for item in payload.get("items", []) or []}

    def named_items(self, text: str) -> list[tuple[str, str]]:
        """NameSearch: the SAME request WITHOUT the blockchains filter (the
        hunter's view — see the asymmetry note below), returning (id, name)
        with the name read from `meta.name`. ⚠️ `meta.name` is the documented
        field, pinned by fixture `rarible_search_named.json` once
        scripts/capturar_target.py has captured it — the measured 2026-08-25
        capture only pinned `items[].id`."""
        body = json.dumps({
            "size": self._size,
            "filter": {"fullText": {"text": text}},
        }).encode()
        raw = self._post(f"{self._base}/items/search", body, {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-KEY": self._key,
        })
        payload = json.loads(raw or "{}")
        out: list[tuple[str, str]] = []
        for item in payload.get("items", []) or []:
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            out.append((str(item.get("id", "")), str(meta.get("name", "") or "")))
        return out


# OpenSea's chain slugs for the chains the game knows (the same vocabulary
# as adapters.RARIBLE_CHAIN). Polygon is 'matic' on OpenSea.
OPENSEA_CHAIN = {"ethereum": "ethereum", "polygon": "matic", "base": "base",
                 "arbitrum": "arbitrum", "optimism": "optimism", "zora": "zora"}
# Measured 10/09: the API path says 'matic' but result URLs say 'polygon' —
# both must read back as the game's 'polygon'.
_OPENSEA_SLUG_TO_CHAIN = {v: k for k, v in OPENSEA_CHAIN.items()} | {"polygon": "polygon"}


class OpenSeaSearch:
    """The same two questions asked of OpenSea — GET /api/v2/search with
    `asset_types=nft` — with the MEASURED response shape (capturar_target.py
    `opensea`, 2026-09-10, fixtures opensea_search_*.json): `results[]` of
    {type:'nft', nft:{identifier, contract, name, collection, image_url,
    opensea_url}}. There is NO chain field: the chain is read from the
    `opensea_url` path ('/assets/<slug>/<contract>/<id>'), falling back to
    the `image_url` path ('/<slug>/<contract>/…'). Measured: the target
    (FND #1) surfaces for its own name, filtered and unfiltered; a clue
    sentence yields an empty `results`; quota is 120 requests per 60 s
    window (x-ratelimit-* headers).

    Why it exists (10/09): Rarible's public plans are Free = 100 requests a
    MONTH or Enterprise by contact; a target hunt spends 40-200. Same ports
    as RaribleSearch, same id format ('CHAIN:0xcontract:tokenId' upper-
    cased), same contract: raise on transport failure, never guess.

    `http_get(url, headers: dict) -> str` is injected; the process-wide
    transport already sends a browser User-Agent (Cloudflare 403s without)."""

    # MEASURED 17/09 against the live API, and the reason /prepare could
    # never write a Clue 1: {"errors": ["Query must not exceed 100
    # characters"]}, HTTP 400, deterministic.
    max_query_chars = 100

    def __init__(self, *, http_get, api_key: str,
                 base_url: str = "https://api.opensea.io/api/v2",
                 size: int = 50):
        if not api_key:
            raise ValueError("OpenSeaSearch needs an api key — without one the "
                             "guard would fail every clue, stalling the hunt")
        self._get = http_get
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._size = max(1, min(int(size), 50))       # documented maximum

    def item_ids(self, text: str, *, chain: str) -> set[str]:
        slug = OPENSEA_CHAIN.get(chain.lower())
        if slug is None:
            # a chain OpenSea has no slug for: unverifiable, never "absent"
            raise ValueError("chain not searchable on this surface")
        rows = self._search(text, chains=slug)
        out: set[str] = set()
        for nft in rows:
            found = _opensea_chain_of(nft) or chain
            ident = _opensea_id(nft, found)
            if ident:
                out.add(ident)
        return out

    def named_items(self, text: str) -> list[tuple[str, str]]:
        """NameSearch: UNFILTERED (all chains — the hunter's view). An item
        whose chain cannot be read is kept under 'UNKNOWN:' — it still
        counts as another bearer of the name, which is the conservative
        reading for a uniqueness verdict."""
        out: list[tuple[str, str]] = []
        for nft in self._search(text, chains=None):
            ident = _opensea_id(nft, _opensea_chain_of(nft) or "unknown")
            if ident:
                out.append((ident, str(nft.get("name") or "")))
        return out

    def _search(self, text: str, *, chains: str | None) -> list[dict]:
        from urllib.parse import quote
        url = (f"{self._base}/search?query={quote(text)}"
               f"&asset_types=nft&limit={self._size}")
        if chains:
            url += f"&chains={chains}"
        raw = self._get(url, {"X-API-KEY": self._key,
                              "Accept": "application/json"})
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("search response without a results list")
        rows: list[dict] = []
        for r in payload["results"]:
            if not isinstance(r, dict):
                continue
            nft = r.get("nft")
            if isinstance(nft, dict) and (r.get("type") in (None, "nft")):
                rows.append(nft)
        return rows


def _opensea_chain_of(nft: dict) -> str:
    """The chain name read from the URLs OpenSea puts on every result —
    the game's name for the chains it knows ('ethereum', 'polygon', …),
    OpenSea's own slug for the ones it doesn't (measured: 'soneium'), so
    a homonym on any chain still counts as another bearer; '' when neither
    URL carries a slug."""
    from urllib.parse import urlsplit
    for key, marker in (("opensea_url", "/assets/"), ("image_url", "/")):
        path = urlsplit(str(nft.get(key) or "")).path
        if marker == "/assets/":
            if "/assets/" not in path:
                continue
            slug = path.split("/assets/", 1)[1].split("/", 1)[0]
        else:
            slug = path.strip("/").split("/", 1)[0]
        slug = slug.lower()
        if slug in _OPENSEA_SLUG_TO_CHAIN:
            return _OPENSEA_SLUG_TO_CHAIN[slug]
        if slug.isalpha() and key == "opensea_url":
            return slug
    return ""


def _opensea_id(nft: dict, chain: str) -> str:
    contract = str(nft.get("contract") or "").strip().lower()
    ident = str(nft.get("identifier") or "").strip()
    if not (contract.startswith("0x") and ident):
        return ""
    return f"{chain}:{contract}:{ident}".upper()


# --------------------------------------------------------------------------- #
# Marketplace name-uniqueness — the refresh/selector filter, with R2 canary     #
# --------------------------------------------------------------------------- #


class NameSearch(Protocol):
    """Full-text search returning (item_id, name) pairs ACROSS ALL CHAINS —
    the shape the uniqueness filter needs (ids alone can't say whether
    another item bears the same base name). Same id format as
    MarketSearch ('CHAIN:0x…:tokenId'); raises on transport failure.

    ⚠️ THE ASYMMETRY, written down because it is counter-intuitive and
    someone will want to "harmonise" it (Opus re-review, 06/09):
      · the CLUE guard (ClueSearchGuard) searches FILTERED to the target's
        chain — less noise competing for the N result slots, so the target
        surfaces more easily, so more clues get REJECTED: filtering makes
        that guard MORE conservative
      · the UNIQUENESS guard (below) searches UNFILTERED — it must see what
        the HUNTER sees, and the hunter does not filter by chain. Measured
        05/09 (Fecho_Condicao1_Foundation.md): OpenSea search returns
        multi-chain results; a Solana homonym is a homonym the hunter finds,
        submits, and burns a guess on — the Hunt #7 failure Option A exists
        to bury. Filtering here would make the guard LESS conservative.
    Different questions, opposite answers, same endpoint."""

    def named_items(self, text: str) -> list[tuple[str, str]]: ...


def search_failure_kind(e: BaseException) -> str:
    """How ONE search request failed, in the words the gateway reports
    already use (adapters._failure_kind): the HTTP code ("429", "503",
    "400", …), "timeout", "ligação" (DNS, refused, reset, TLS, a cut-off
    body) — plus the one that is the search's own: "corpo-ilegível", an
    answer that arrived and is not the JSON we know (not JSON at all, or
    without a results list). Anything else is "outro:" and the exception's
    TYPE, so that an "outro" in a report is already half a diagnosis.

    The type, never the message: a transport's message can quote the URL,
    and the URL carries the name being searched.

    Imported lazily: this module stays free of the adapters' imports."""
    from .adapters import _failure_kind
    kind = _failure_kind(e)
    if kind != "outro":
        return kind
    if isinstance(e, ValueError):         # json.JSONDecodeError is one
        return "corpo-ilegível"
    return f"outro:{type(e).__name__}"


# THE CONTROL SEARCH (10/10). When a name's search fails on every attempt,
# one more search is made for this fixed text — nobody's name, cheap for the
# marketplace to answer. It says which of two things happened:
#   · it answers  → the search works right now: the failure is about THAT
#                   name's query ("the name");
#   · it fails    → the search is down for everyone ("the moment").
# Measured on 10/10 (/harvest manifold 150): 25 failed requests, all 503, 24
# of them in 8 questions that failed three times out of three — and nothing
# in the counts could tell the name from the moment. A control that never
# answers reads as "the moment", which is the side that keeps a candidate.
SEARCH_CONTROL_QUERY = "finding memeland"

# A failed request that took this long was the marketplace's own search timing
# out; under it, the request was turned away at the door. Counted apart
# (`failed_speed`), because the two call for different patience.
SLOW_FAILURE_S = 5.0


def is_server_error(kind: str) -> bool:
    """A 5xx, by its `search_failure_kind`."""
    return len(kind) == 3 and kind.isdigit() and kind.startswith("5")


class MarketNameUniqueness:
    """name_is_unique(base, chain, contract, token_id) -> bool | None — the
    callable hunt.select_judged injects, called at DRAW time on the drawn
    candidate inside a fresh decoy batch (Opus, 06/09: it left the refresh,
    where it cost 60-80k calls a week; the gate carries its sampled rate).

    Approval is "no OTHER item, on ANY chain, carries this base name" — an
    R2 guard, so it proves first that the index can see the target: the
    target's own id must be among the results for its base name. One
    unfiltered query serves both the canary and the verdict. If the target
    is absent the answer is None — unverifiable, which the callers fail
    closed on. Never True on an empty result set.

    Two flavours of None, counted separately in `stats` (Opus, 06/09):
      · blind   — target absent from a NON-full page: the index cannot see
                  it (unindexed, key, request shape)
      · crowded — target absent from a FULL page: the name has more bearers
                  than `page_size`; conservative and correct, but it is a
                  not-unique-shaped fact, not an outage — reporting it as
                  'unverifiable' would misdiagnose the refresh
    `stats` is counts only (never names) — safe for the operator log.

    A THIRD None is OURS, and it used to be anonymous (10/10): the request
    itself failed. Every exception was swallowed into `transport` and its
    type thrown away, so when it came back — 2 of the 6 that reached this
    check in one probe — nobody could say whether OpenSea was limiting us,
    timing out or answering something else. Two things are kept now, both
    counts and words only (never the message: it can quote the query):
      · `failed_requests` — every request that failed, by kind, INCLUDING
        the ones a retry then recovered from (those never reach `transport`
        and would stay invisible);
      · `last_transport`  — the kinds of the attempts of THIS call, when it
        ended in a transport failure; empty otherwise. The caller needs it
        to tell "ours" from a verdict: /prepare keeps the target, the
        harvest asks again at the end of the run.

    THE 503 (10/10, once the kind was measured). Three things:
      · a 5xx is NOT retried inside the call. The two quick retries (2 s,
        4 s) recovered at most one failing question in nine and cost sixteen
        requests in one harvest; what helps is waiting, and that is the
        caller's second and third pass;
      · after a question that failed, the CONTROL search (above) is made;
        `last_control` is True (it answered: the name), False (it failed:
        the moment) or None (no control), and `control` counts both;
      · `failed_speed` counts failed requests as "rápidos" / "lentos"."""

    def __init__(self, *, search: NameSearch, page_size: int,
                 retries: int = 2, sleep_s: float = 2.0,
                 item_status: Callable[[str, str, int], object] | None = None,
                 control_query: str | None = SEARCH_CONTROL_QUERY,
                 clock: Callable[[], float] = time.monotonic):
        self._search = search
        self._control_query = control_query
        self._clock = clock
        # "blind" split by ASKING the marketplace for the item itself (30/09,
        # measurement only — the answer stays None): does it not know the
        # piece, or know it and keep it out of the search (flagged or not)?
        # The first means players cannot find it there either; the second
        # may mean the guard is refusing playable targets.
        #
        # 01/10: on 30/09 all 5 blind Base candidates were "clean" — known
        # to OpenSea, unflagged, and still absent from its search. So the
        # lookup may also return (status, name), and the clean ones are split
        # again: does OpenSea hold the SAME name we searched, another one, or
        # none? "Another" or "none" would be our query; "the same" is the
        # search itself. The name never leaves this method — counts do.
        self._item_status = item_status
        self._page = page_size
        self._retries = retries
        self._sleep = sleep_s
        # `crowded_same` (29/09) is a SUB-count of `crowded`, never instead
        # of it: a full page on which at least one OTHER item carries the
        # exact base name. The first /harvest runs lost every candidate to
        # "crowded" and nobody could tell a real namesake from an obscure
        # piece OpenSea merely ranked below 50 look-alikes. Measurement only:
        # the answer stays None either way.
        self.stats = {"unique": 0, "not_unique": 0, "blind": 0,
                      "crowded": 0, "crowded_same": 0, "transport": 0,
                      "blind_unindexed": 0, "blind_flagged": 0,
                      "blind_clean": 0, "blind_unknown": 0,
                      "blind_clean_same": 0, "blind_clean_other": 0,
                      "blind_clean_noname": 0}
        self.failed_requests: dict[str, int] = {}
        self.failed_speed: dict[str, int] = {}
        self.control: dict[str, int] = {}
        self.last_transport: tuple[str, ...] = ()
        self.last_control: bool | None = None

    def __call__(self, base: str, chain: str, contract: str,
                 token_id: int) -> bool | None:
        from .selector import normalize_name
        want = _canonical(f"{chain}:{contract}:{token_id}")
        self.last_transport = ()
        self.last_control = None
        kinds: list[str] = []
        for attempt in range(self._retries + 1):
            started = self._clock()
            try:
                rows = self._search.named_items(base)     # UNFILTERED
                break
            except Exception as e:  # noqa: BLE001 — retry, then unverifiable
                speed = ("lentos" if self._clock() - started >= SLOW_FAILURE_S
                         else "rápidos")
                self.failed_speed[speed] = self.failed_speed.get(speed, 0) + 1
                kind = search_failure_kind(e)
                self.failed_requests[kind] = self.failed_requests.get(kind, 0) + 1
                if kind not in kinds:
                    kinds.append(kind)
                # a 5xx is not asked again here: waiting is what helps, and
                # the caller's later passes are the wait
                if attempt < self._retries and not is_server_error(kind):
                    time.sleep(self._sleep * (attempt + 1))
                    continue
                self.stats["transport"] += 1
                self.last_transport = tuple(kinds)
                self.last_control = self._ask_control()
                return None
        ids = {_canonical(i): n for i, n in rows}
        key = base.casefold()
        if want not in ids:                   # canary failed
            if len(rows) >= self._page:
                self.stats["crowded"] += 1
                if any(normalize_name(n or "").casefold() == key
                       for n in ids.values()):
                    self.stats["crowded_same"] += 1
            else:
                self.stats["blind"] += 1
                self._split_blind(base, chain, contract, token_id)
            return None
        others = [i for i, n in ids.items()
                  if i != want and normalize_name(n or "").casefold() == key]
        self.stats["not_unique" if others else "unique"] += 1
        return not others


    def _ask_control(self) -> bool | None:
        """The control search, after a question that failed. True = it
        answered (any answer, even an empty one); False = it failed too.
        Never raises, never retried: one request."""
        if not self._control_query:
            return None
        try:
            self._search.named_items(self._control_query)
        except Exception:  # noqa: BLE001 — a measurement never breaks a verdict
            self.control["falhou"] = self.control.get("falhou", 0) + 1
            return False
        self.control["respondeu"] = self.control.get("respondeu", 0) + 1
        return True

    def _split_blind(self, base: str, chain: str, contract: str,
                     token_id: int) -> None:
        if self._item_status is None:
            return
        from .selector import normalize_name
        try:
            got = self._item_status(chain, contract, token_id)
        except Exception:  # noqa: BLE001 — a measurement never breaks a verdict
            got = None
        status, name = got if isinstance(got, tuple) else (got, None)
        self.stats[{"missing": "blind_unindexed", "flagged": "blind_flagged",
                    "clean": "blind_clean"}.get(status, "blind_unknown")] += 1
        if status == "clean" and isinstance(got, tuple):
            # the same comparison the verdict uses for namesakes
            if not name:
                self.stats["blind_clean_noname"] += 1
            elif normalize_name(name).casefold() == base.casefold():
                self.stats["blind_clean_same"] += 1
            else:
                self.stats["blind_clean_other"] += 1


class FakeSearch:
    """MarketSearch fake: maps query substrings to item-id sets (tests).
    Only items whose id starts with the queried chain are returned — the
    fake behaves like a real per-chain index, so a guard querying the wrong
    chain sees nothing (the P0-4 blindness, reproducible offline)."""

    def __init__(self, hits: dict[str, set[str]] | None = None, *,
                 raises: bool = False):
        self._hits = hits or {}
        self._raises = raises
        self.queries: list[tuple[str, str]] = []

    def item_ids(self, text: str, *, chain: str) -> set[str]:
        if self._raises:
            raise RuntimeError("marketplace unreachable")
        self.queries.append((text, chain))
        out: set[str] = set()
        for needle, ids in self._hits.items():
            if needle.lower() in text.lower():
                out |= {i for i in ids if i.upper().startswith(chain.upper() + ":")}
        return out


class FakeNameSearch:
    """NameSearch fake: maps query substrings to [(id, name)] rows across
    all chains, like the real multi-chain index (tests)."""

    def __init__(self, hits: dict[str, list[tuple[str, str]]] | None = None,
                 *, raises: bool = False):
        self._hits = hits or {}
        self._raises = raises

    def named_items(self, text: str) -> list[tuple[str, str]]:
        if self._raises:
            raise RuntimeError("marketplace unreachable")
        out: list[tuple[str, str]] = []
        for needle, rows in self._hits.items():
            if needle.lower() in text.lower():
                out += list(rows)
        return out

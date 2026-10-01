"""Production adapters for the target game (soldadura 5/6) — where the code
meets the world, behind the ports the rest of the package already tests.

Discipline (project rule): capture raw → parser offline → run. Every parser
here is written against `tests/fixtures/target/*.json` captured by
`scripts/capturar_target.py`; the standard-shaped ones (JSON-RPC, ABI string
decoding, ERC-721 Transfer logs) are also pinned by tests on synthetic
payloads so a fixture that arrives different breaks a test, not a hunt.

R1 — no chain by default: every RPC is built by NAME (`chain_rpc(chain=…)`),
`rpcs` is a dict chain → ChainRpc, a missing chain is a KeyError at
construction, never a silent fallback.

Two families of metadata fetchers, deliberately separate:
  · KEYED  (our RPC, our gateway) — the refresh and the EOA check, which
    resolve EVERYONE's metadata; there is no secret to protect there.
  · GENERIC (public RPCs + public IPFS gateways, no key) — the live check
    and the image fetch, which read the TARGET (hidden in its sealed decoy
    batch). Nothing that carries our identity ever reads the target.

THE GATEWAY ONLY APPEARS WHERE THE READ DOES NOT REPEAT (Opus, 06/09,
measured: 1 public IPFS gateway in 13 works, so there is no gateway
rotation to speak of): the image batch (once per hunt, with decoys) and
the refresh (reads everyone before filtering — what it exposes is the
platform, not our pool). The path that REPEATS during a hunt is pure RPC:
tokenURI for the content id, ownerOf for the burn, over the public RPCs
in rotation per batch. In the two non-repeating paths a dedicated, paid
gateway is acceptable — the leak goes to that gateway anyway and the
defence there is decoys and diffusion, not transport anonymity. What can
never be keyed nor repeated is the read of the target during the hunt —
and that read no longer touches a gateway at all.

ROTATION IS PER BATCH, NEVER PER READ (Opus, 06/09, 5/6 review). The live
check reads target + 7 decoys; a provider chosen per read would show
provider A the target alone and provider B one decoy — the batch would stop
existing as an anonymity set at the adapter level after being defended
twice at the logic level. So `GenericMetadata.batch()` picks ONE RPC and
ONE gateway for everything inside the `with`, and rotates between batches.
Inside a batch there is no failover either: a provider that is down makes
the read ChainUnavailable (→ HOLD, R5), and the next batch rotates. A
failover mid-batch would split the batch across providers.

Burn vs outage (load-bearing): `LiveCheck` reads `None` as BURNED and any
non-ChainUnavailable exception as a revert; a gateway answering a 200 with
an HTML rate-limit page must therefore NEVER surface as None. Only a
tokenURI/ownerOf REVERT is None; unparseable bodies, HTTP errors and
timeouts are ChainUnavailable.

Marketplace link resolution (`MarketplaceLinkResolver`) is MANDATORY in
production (Opus): a link with contract+tokenId but no chain is resolved by
PROBING the marketplace index per chain (exactly one chain must own the
token — two hits is ambiguity, fail-closed); slug-only pages (Foundation
@artist pages, SuperRare artworks) need page parsers that are written ONLY
against captured HTML — until the capture exists they resolve to None,
which the claim path answers with the format rule. Whether that is an
acceptable public rule is Pedro's decision, not this module's.
"""

from __future__ import annotations

import base64
import binascii
import http.client
import itertools
import json
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Sequence
from urllib.parse import quote, urlsplit

from .claim import CHAIN_ALIASES, TargetRef, _ADDR_RE, _ADDR_TID_RE, _QUERY_TID_RE
from .hunt import JudgeVerdict, LiveRead
from .refresh import TokenRead, uri_is_content_addressed
from .selector import Target
from .sources import (
    SEL_OWNEROF,
    SEL_TOKENURI,
    ChainRpc,
    ChainUnavailable,
    GatewayNotJson,
    GatewayUnavailable,
    MetadataInvalid,
    MetadataPinGone,
    RpcUnavailable,
    TokenUriUndecodable,
)

HttpGet = Callable[[str, dict], str]           # (url, headers) -> body text
HttpPost = Callable[[str, bytes, dict], str]   # (url, body, headers) -> text
HttpGetBytes = Callable[[str, dict], bytes]

TRANSFER_TOPIC = ("0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")
ZERO_TOPIC = "0x" + "0" * 64
_CID_RE = re.compile(r"^(qm[1-9a-z]{44}|baf[a-z0-9]{20,})(/.*)?$", re.I)
_IPFS_PATH_RE = re.compile(r"/ipfs/((?:qm[1-9a-z]{44}|baf[a-z0-9]{20,})(?:/[^?#]*)?)", re.I)


# --------------------------------------------------------------------------- #
# JSON-RPC                                                                     #
# --------------------------------------------------------------------------- #


class RpcError(RuntimeError):
    """The node answered — with an error. `revert` is True ONLY for an
    execution revert (measured 06/09 on Alchemy, publicnode, drpc, 1rpc:
    code 3, message "execution reverted: …", data 0x08c379a0…). Everything
    else a node says is NOT a revert and never becomes 'no such token':
    cloudflare-eth answers a revert AND its own trouble with a bare
    -32603 "Internal error", ankr answers -32000 "Unauthorized" — read as
    a burn, either would void an honest hunt. So callers map revert →
    None and any other RpcError → ChainUnavailable."""

    def __init__(self, code: int, message: str, *, revert: bool,
                 data: str | None = None):
        super().__init__(f"rpc error {code}: {message[:120]}")
        self.code = code
        self.revert = revert
        # the revert's own bytes (30/09): a reason or a custom error, or
        # nothing — what tells a wallet refusing a signature from a contract
        # without the function (sources._erc1271_answer)
        self.data = data if isinstance(data, str) else None


_REVERT_SELECTORS = ("0x08c379a0", "0x4e487b71")   # Error(string), Panic(uint)


def _is_revert(code: int, message: str, data: object) -> bool:
    if code == 3:
        return True
    if "revert" in (message or "").lower():
        return True
    return isinstance(data, str) and data.lower().startswith(_REVERT_SELECTORS)


class JsonRpc:
    """One endpoint. `http_post(url, body, headers) -> text` raises on
    HTTP/transport failure; everything that is not a well-formed JSON-RPC
    answer is ChainUnavailable (never a silent None). The URL is never
    formatted into messages (Alchemy keys live in the path)."""

    def __init__(self, *, url: str, http_post: HttpPost, label: str = ""):
        self._url = url
        self._post = http_post
        self.label = label or urlsplit(url).netloc
        self._ids = itertools.count(1)

    def call(self, method: str, params: list) -> object:
        body = json.dumps({"jsonrpc": "2.0", "id": next(self._ids),
                           "method": method, "params": params}).encode()
        try:
            text = self._post(self._url, body, {"Content-Type": "application/json"})
        except Exception as e:  # noqa: BLE001 — transport
            raise ChainUnavailable(f"{self.label}: {type(e).__name__}") from e
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ChainUnavailable(f"{self.label}: non-JSON answer") from e
        if not isinstance(doc, dict):
            raise ChainUnavailable(f"{self.label}: malformed answer")
        if "error" in doc and doc["error"]:
            err = doc["error"] if isinstance(doc["error"], dict) else {}
            code = int(err.get("code", 0) or 0)
            msg = str(err.get("message", "") or "")
            # rate limits / capacity answer as JSON-RPC errors on some
            # providers (Alchemy 429 bodies, -32005 "limit exceeded")
            low = msg.lower()
            if code in (429, -32005, -32016, -32029) or "rate" in low \
                    or "capacity" in low or "too many requests" in low:
                raise ChainUnavailable(f"{self.label}: throttled")
            raise RpcError(code, msg, revert=_is_revert(code, msg, err.get("data")),
                           data=err.get("data"))
        if "result" not in doc:
            raise ChainUnavailable(f"{self.label}: no result field")
        return doc["result"]

    # -- the ChainRpc shape ------------------------------------------------ #
    def eth_call(self, to: str, data: str) -> str:
        r = self.call("eth_call", [{"to": to, "data": data}, "latest"])
        return str(r or "0x")

    def get_code(self, addr: str) -> str:
        return str(self.call("eth_getCode", [addr, "latest"]) or "0x")

    def get_logs(self, *, from_block: int, to_block: int,
                 topics: list) -> list[dict]:
        r = self.call("eth_getLogs", [{"fromBlock": hex(from_block),
                                       "toBlock": hex(to_block),
                                       "topics": topics}])
        if not isinstance(r, list):
            raise ChainUnavailable(f"{self.label}: eth_getLogs not a list")
        return r


def chain_rpc(*, chain: str, url: str, http_post: HttpPost) -> ChainRpc:
    """R1: the adapter is BOUND to the chain's name at construction."""
    if not chain or not url:
        raise ValueError(f"chain_rpc needs both a chain name and a URL "
                         f"(chain={chain!r}, url={'set' if url else 'EMPTY'})")
    rpc = JsonRpc(url=url, http_post=http_post, label=f"rpc:{chain}")
    return ChainRpc(chain=chain, eth_call=rpc.eth_call, get_code=rpc.get_code)


def chain_rpcs(urls: dict[str, str], *, http_post: HttpPost) -> dict[str, ChainRpc]:
    """chain → ChainRpc from a chain → URL map; empty URLs are SKIPPED
    (a chain with no RPC is absent, and every consumer raises KeyError on
    an absent chain — configuration error, loud, never a default)."""
    return {c: chain_rpc(chain=c, url=u, http_post=http_post)
            for c, u in urls.items() if u}


# --------------------------------------------------------------------------- #
# ABI helpers (synthetic-tested; fixture-pinned when the capture lands)        #
# --------------------------------------------------------------------------- #


def abi_uint(n: int) -> str:
    return n.to_bytes(32, "big").hex()


def decode_abi_string(data: str) -> str:
    """ABI-decode a single `string` return value. Raises ValueError on
    anything that is not a well-formed dynamic string — callers map that
    to ChainUnavailable, never to 'burned'."""
    if not isinstance(data, str) or not data.startswith("0x"):
        raise ValueError("not hex data")
    raw = bytes.fromhex(data[2:])
    if len(raw) < 64:
        raise ValueError("too short for a dynamic string")
    offset = int.from_bytes(raw[:32], "big")
    if offset + 32 > len(raw):
        raise ValueError("offset out of range")
    length = int.from_bytes(raw[offset:offset + 32], "big")
    start = offset + 32
    if start + length > len(raw):
        raise ValueError("length out of range")
    return raw[start:start + length].decode("utf-8", "replace")


def address_from_word(data: str) -> str | None:
    if not isinstance(data, str) or not data.startswith("0x") or len(data) < 66:
        return None
    addr = "0x" + data[2:66][-40:]
    return None if int(addr, 16) == 0 else addr


# --------------------------------------------------------------------------- #
# Creator credit (R9): tokenCreator → ENS reverse WITH forward check           #
# --------------------------------------------------------------------------- #
#
# Measured 09/09: Foundation metadata carries no artist field at all, so the
# reveal's credit needs the chain. Foundation (FND) and SuperRare both expose
# `tokenCreator(uint256)`. The address becomes a name ONLY through ENS with a
# forward check (Opus): a reverse record is a claim anyone can set on their
# own address; the name must resolve BACK to the same address or it is not
# published — R8 on a person. Order: metadata artist (elsewhere) → ENS name
# (verified) → truncated address → nothing (the item link is attribution).
# Every step here is best-effort: any failure → the next fallback, never an
# exception to the reveal.

SEL_TOKEN_CREATOR = "0x40c1a064"      # tokenCreator(uint256)
SEL_ENS_RESOLVER = "0x0178b8bf"       # resolver(bytes32)
SEL_ENS_NAME = "0x691f3431"           # name(bytes32)
SEL_ENS_ADDR = "0x3b3b57de"           # addr(bytes32)
ENS_REGISTRY = "0x00000000000c2e074ec69a0dfb2997ba6c7d2e1e"
_ENS_LABEL_RE = re.compile(r"^[a-z0-9-]+$")


def _keccak(data: bytes) -> bytes:
    from eth_utils import keccak
    return keccak(data)


def ens_namehash(name: str) -> bytes | None:
    """EIP-137 namehash for ASCII names only (lowercase, [a-z0-9-] labels).
    Anything else (unicode, needing UTS-46 normalisation) → None: we do not
    normalise, so we do not publish."""
    node = b"\x00" * 32
    if not name:
        return node
    labels = name.lower().split(".")
    if any(not _ENS_LABEL_RE.match(lb) for lb in labels):
        return None
    for lb in reversed(labels):
        node = _keccak(node + _keccak(lb.encode()))
    return node


def token_creator(rpc: ChainRpc, contract: str, token_id: int) -> str | None:
    """tokenCreator(tokenId) → address, None when the contract has no such
    function (revert / empty) or the chain is unavailable."""
    try:
        data = rpc.eth_call(contract, SEL_TOKEN_CREATOR + abi_uint(token_id))
    except Exception:  # noqa: BLE001 — credit is best-effort
        return None
    return address_from_word(data)


def ens_name_verified(eth_rpc: ChainRpc, address: str) -> str | None:
    """The address's ENS primary name, ONLY if it resolves back to the same
    address (forward check). None on any gap."""
    try:
        addr = address.lower()
        rev = ens_namehash(f"{addr[2:]}.addr.reverse")
        if rev is None:
            return None
        resolver = address_from_word(eth_rpc.eth_call(ENS_REGISTRY, SEL_ENS_RESOLVER + rev.hex()))
        if not resolver:
            return None
        name = decode_abi_string(eth_rpc.eth_call(resolver, SEL_ENS_NAME + rev.hex())).strip()
        if not name or len(name) > 64:
            return None
        node = ens_namehash(name)
        if node is None:
            return None
        fwd_resolver = address_from_word(eth_rpc.eth_call(ENS_REGISTRY, SEL_ENS_RESOLVER + node.hex()))
        if not fwd_resolver:
            return None
        back = address_from_word(eth_rpc.eth_call(fwd_resolver, SEL_ENS_ADDR + node.hex()))
        return name.lower() if back and back.lower() == addr else None
    except Exception:  # noqa: BLE001 — a reverse without proof is not published
        return None


def creator_credit(rpcs: dict[str, ChainRpc], chain: str, contract: str,
                   token_id: int) -> str:
    """'name.eth' (forward-verified) → '0x1234…abcd' → ''. ENS is read on
    ethereum whatever chain the token lives on (primary names live there);
    without an ethereum RPC the address alone is the credit."""
    rpc = rpcs.get(chain)
    if rpc is None:
        return ""
    addr = token_creator(rpc, contract, token_id)
    if not addr:
        return ""
    eth = rpcs.get("ethereum")
    name = ens_name_verified(eth, addr) if eth is not None else None
    return name or f"{addr[:6]}…{addr[-4:]}"


# --------------------------------------------------------------------------- #
# Mint fetcher + code bytes for EraDiscovery                                   #
# --------------------------------------------------------------------------- #


def mint_fetcher(rpc: JsonRpc) -> Callable[[int], list[tuple[str, int]]]:
    """fetch_mints(block) -> [(contract, token_id)] — ERC-721 Transfer from
    the zero address, ONE block per call (the Alchemy free tier caps
    eth_getLogs at 10 blocks; one block is also what makes EraDiscovery's
    per-block atomicity real). ERC-20 Transfer events share the topic but
    carry 3 topics (value in data) — only 4-topic logs are token mints;
    ERC-1155 uses a different topic and is out of scope for the era scan."""
    def fetch(block: int) -> list[tuple[str, int]]:
        logs = rpc.get_logs(from_block=block, to_block=block,
                            topics=[TRANSFER_TOPIC, ZERO_TOPIC])
        out: list[tuple[str, int]] = []
        for lg in logs:
            topics = lg.get("topics") or []
            if len(topics) != 4 or lg.get("removed"):
                continue
            try:
                out.append((str(lg["address"]).lower(), int(str(topics[3]), 16)))
            except (KeyError, ValueError, TypeError):
                continue
        return out
    return fetch


def code_bytes(rpc: JsonRpc) -> Callable[[str], bytes]:
    def get_code(addr: str) -> bytes:
        h = rpc.get_code(addr)
        try:
            return bytes.fromhex(h[2:] if h.startswith("0x") else h)
        except ValueError as e:
            raise ChainUnavailable(f"{rpc.label}: eth_getCode not hex") from e
    return get_code


# --------------------------------------------------------------------------- #
# tokenURI resolution                                                          #
# --------------------------------------------------------------------------- #


def gateway_url(uri: str, gateway: str) -> str | None:
    """Rewrite a token URI onto `gateway` (…/ipfs/) when it is content-
    addressed: ipfs://, a bare CID (Async), or another host's /ipfs/<cid>
    path (SuperRare's ipfs.pixura.io — the CID seals the content, any
    gateway serves the same bytes). Plain http(s) returns itself (the
    caller decides whether to fetch it); data: returns None (inline)."""
    u = (uri or "").strip()
    low = u.lower()
    if not u or low.startswith("data:"):
        return None
    if low.startswith("ipfs://"):
        path = u[7:]
        if path.lower().startswith("ipfs/"):
            path = path[5:]
        return gateway.rstrip("/") + "/" + path.lstrip("/")
    m = _CID_RE.match(u)
    if m:
        return gateway.rstrip("/") + "/" + u
    if low.startswith("http://") or low.startswith("https://"):
        pm = _IPFS_PATH_RE.search(u)
        if pm:
            return gateway.rstrip("/") + "/" + pm.group(1)
        return u
    return None


def decode_data_uri(uri: str) -> dict | None:
    """data:application/json;base64,… or data:application/json,… → dict.
    Anything else → None."""
    try:
        head, payload = uri.split(",", 1)
    except ValueError:
        return None
    try:
        if ";base64" in head.lower():
            text = base64.b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8", "replace")
        else:
            from urllib.parse import unquote
            text = unquote(payload)
        doc = json.loads(text)
    except (ValueError, binascii.Error):
        return None
    return doc if isinstance(doc, dict) else None


class Erc721Metadata:
    """fetch(chain, contract, token_id) -> dict | None, via ONE RPC per chain
    and ONE gateway: tokenURI (eth_call) → resolve → JSON.

      · REVERT on tokenURI (burned / nonexistent)            → None
      · unparseable ABI answer, gateway non-JSON, HTTP error → ChainUnavailable
      · missing chain in `rpcs`                               → KeyError (R1)

    `http_get(url, headers) -> text` raises on HTTP/transport failure.
    Used as-is for the KEYED family; the GENERIC family wraps it in
    `GenericMetadata`, which supplies the provider chosen for the batch."""

    def __init__(self, *, rpcs: dict[str, ChainRpc], gateway: str,
                 http_get: HttpGet, max_bytes: int = 2_000_000,
                 content_addressed_only: bool = True):
        self._rpcs = dict(rpcs)
        self._gateway = gateway
        self._get = http_get
        self._max = max_bytes
        # The pool's structural filter (commitment.py, refresh.py): a plain
        # http(s) tokenURI serves whatever the host feels like today and
        # would fire the mutation-void rule on an honest hunt, so the KEYED
        # fetcher (refresh/selector) answers None for it — excluded from
        # the pool. The GENERIC fetcher (live check) sets this False: a
        # target whose URI later turned into plain http is read anyway, and
        # the hash decides intact/mutated — never 'burned' by shape.
        self._ca_only = content_addressed_only

    def token_uri(self, chain: str, contract: str, token_id: int) -> str | None:
        rpc = self._rpcs[chain]                       # KeyError: R1, loud
        if rpc.chain != chain:
            raise ValueError("ChainRpc/chain mismatch (R1)")
        try:
            data = rpc.eth_call(contract, SEL_TOKENURI + abi_uint(token_id))
        except ChainUnavailable as e:
            if type(e) is not ChainUnavailable:
                raise                                 # already says where
            raise RpcUnavailable(str(e)) from e
        except RpcError as e:
            if e.revert:
                return None                           # no such token (burned)
            raise RpcUnavailable(f"{chain}: node error, not a revert") from e
        except Exception as e:  # noqa: BLE001 — a fake/other adapter raising
            raise RpcUnavailable(f"{chain}: {type(e).__name__}") from e
        if not data or data == "0x":
            return None                               # no return data: no token
        try:
            return decode_abi_string(data)
        except ValueError as e:
            raise TokenUriUndecodable(f"tokenURI undecodable on {chain}") from e

    def read(self, chain: str, contract: str, token_id: int) -> TokenRead | None:
        """The refresh's collaborator: raw tokenURI + resolved metadata in
        one chain round-trip. None when tokenURI reverts. Metadata is None
        when the URI is not content-addressed (the refresh counts it)."""
        uri = self.token_uri(chain, contract, token_id)
        if uri is None:
            return None
        if not uri_is_content_addressed(uri):
            return TokenRead(token_uri=uri, metadata=None)
        return TokenRead(token_uri=uri, metadata=self._resolve(uri))

    def owner(self, chain: str, contract: str, token_id: int) -> str | None:
        """ownerOf → address, None on revert (burned/nonexistent). Transport
        and non-revert node errors → ChainUnavailable, like token_uri."""
        rpc = self._rpcs[chain]
        try:
            data = rpc.eth_call(contract, SEL_OWNEROF + abi_uint(token_id))
        except ChainUnavailable as e:
            if type(e) is not ChainUnavailable:
                raise
            raise RpcUnavailable(str(e)) from e
        except RpcError as e:
            if e.revert:
                return None
            raise RpcUnavailable(f"{chain}: node error, not a revert") from e
        except Exception as e:  # noqa: BLE001
            raise RpcUnavailable(f"{chain}: {type(e).__name__}") from e
        return address_from_word(data)

    def read_live(self, chain: str, contract: str, token_id: int) -> LiveRead:
        """The live check's collaborator — RPC ONLY, never the gateway:
        tokenURI (None on revert) + ownerOf (None on revert = burned)."""
        return LiveRead(token_uri=self.token_uri(chain, contract, token_id),
                        owner=self.owner(chain, contract, token_id))

    def __call__(self, chain: str, contract: str, token_id: int) -> dict | None:
        uri = self.token_uri(chain, contract, token_id)
        if uri is None:
            return None
        if self._ca_only and not uri_is_content_addressed(uri):
            return None
        return self._resolve(uri)

    def _resolve(self, uri: str) -> dict:
        if uri.lower().startswith("data:"):
            doc = decode_data_uri(uri)
            if doc is None:
                raise MetadataInvalid("data: URI undecodable")
            return doc
        url = gateway_url(uri, self._gateway)
        if url is None:
            raise MetadataInvalid("token URI of unknown scheme")
        try:
            text = self._get(url, {"Accept": "application/json"})
        except Exception as e:  # noqa: BLE001
            raise GatewayUnavailable(f"gateway: {type(e).__name__}") from e
        if len(text) > self._max:
            raise MetadataInvalid("metadata body too large")
        try:
            doc = json.loads(text)
        except ValueError as e:
            # a 200 with an HTML throttle page must NOT read as burned
            raise GatewayNotJson("gateway answered non-JSON") from e
        if not isinstance(doc, dict):
            raise MetadataInvalid("metadata is not an object")
        return doc


# The header that carries a dedicated Pinata gateway's key (30/09). Sent ONLY
# to that gateway's host; main.py's opener drops it on any redirect that
# leaves the host, because urllib would otherwise forward it (measured 30/09).
GATEWAY_KEY_HEADER = "x-pinata-gateway-token"


def _http_status(e: BaseException) -> int | None:
    """The HTTP status an http_get exception carries (urllib's HTTPError has
    `.code`), or None for a timeout / connection error."""
    code = getattr(e, "code", None)
    return code if isinstance(code, int) and 100 <= code <= 599 else None


def _failure_kind(e: BaseException) -> str:
    """How one gateway request failed, for the operator's report (30/09):
    the HTTP code ("429", "504", …), "timeout", "ligação" (DNS, refused,
    reset, TLS, a cut-off body) or "outro". A 429 and a timeout call for
    different answers — being limited is not being slow."""
    code = _http_status(e)
    if code is not None:
        return str(code)
    # before OSError: TimeoutError IS an OSError, and urllib wraps a connect
    # timeout in a URLError whose `reason` is the TimeoutError
    if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
        return "timeout"
    if isinstance(e, (OSError, http.client.HTTPException)):
        return "ligação"
    return "outro"


class GatewayTally:
    """Requests to the larder's gateways by position (1 = the first tried)
    and outcome — the image test's counter (30/09), the same shape as
    FailoverMetadata's `stats["outcomes"]`, for the /harvest report."""

    def __init__(self):
        self.stats = {"outcomes": {}}

    def note(self, position: int, outcome: str) -> None:
        by = self.stats["outcomes"].setdefault(position, {})
        by[outcome] = by.get(outcome, 0) + 1


class FailoverMetadata(Erc721Metadata):
    """The LARDER's reader (29/09): /harvest, /fill, the deposit and the
    /prepare re-read. Our gateway first; when it fails, every other gateway
    we have, one after the other, before the read is called failed.

    WHY. The 29/09 /harvest lost 116 reads to "gateway" — 93 of them in Base,
    more than half of Base's IPFS metadata reads — and 6 Base candidates that
    had JUST been read in the harvest failed their second read, minutes later,
    through the same gateway. Those 6 are measured: the content was there. It
    is the image probe's rule (wiring.probe_image, 17/09), applied to the
    metadata where it was missing.

    NOT the live check: that path is the GENERIC family, one provider per
    batch, and must stay so. This reader reads everyone (harvest) or
    candidates whose image the probe already falls back for, through the
    same gateways in the same order — no exposure the larder did not
    already have.

    Outcomes, when no gateway served JSON:
      · one said 404/410                  → MetadataPinGone (theirs)
      · one answered, but not JSON        → GatewayNotJson (ambiguous, as before)
      · every one threw (timeout, 5xx, …) → GatewayUnavailable (ours, Pedro's rule)

    `fallback_get` reads the fallbacks — main.py gives it a SHORT timeout, so
    a pin nobody serves costs the primary's wait plus a few seconds per extra
    gateway, not 25 s each. `stats["rescued"]` counts reads the primary lost
    and a fallback served: OURS, measured, for the /harvest report.
    `stats["outcomes"]` (30/09) counts every request by gateway position
    (1 = ours) and by outcome — "serviu", "não-json", or `_failure_kind`."""

    def __init__(self, *, rpcs: dict[str, ChainRpc], gateways: Sequence[str],
                 http_get: HttpGet, fallback_get: HttpGet | None = None,
                 gateway_headers: dict[str, dict] | None = None,
                 max_bytes: int = 2_000_000):
        gws = [g for g in dict.fromkeys(gateways) if g]
        if not gws:
            raise ValueError("FailoverMetadata needs at least one gateway")
        super().__init__(rpcs=rpcs, gateway=gws[0], http_get=http_get,
                         max_bytes=max_bytes)
        self._gateways = tuple(gws)
        self._fallback_get = fallback_get or http_get
        # extra headers PER GATEWAY (30/09): the dedicated gateway's key, for
        # that gateway only — a public gateway never sees it
        self._headers = {g: dict(h) for g, h in (gateway_headers or {}).items()}
        self.stats = {"rescued": 0, "outcomes": {}}

    @property
    def gateway_count(self) -> int:
        """Distinct gateways — 1 means there is nothing to rotate to."""
        return len(self._gateways)

    def _note(self, position: int, outcome: str) -> None:
        by = self.stats["outcomes"].setdefault(position, {})
        by[outcome] = by.get(outcome, 0) + 1

    def _resolve(self, uri: str) -> dict:
        if uri.lower().startswith("data:"):
            return super()._resolve(uri)            # inline: no gateway at all
        pairs, seen = [], set()
        for g in self._gateways:
            u = gateway_url(uri, g)
            if u and u not in seen:
                seen.add(u)
                pairs.append((g, u))
        if not pairs:
            raise MetadataInvalid("token URI of unknown scheme")
        urls = [u for _, u in pairs]
        gone = not_json = 0
        for i, (gw, url) in enumerate(pairs):
            get = self._get if i == 0 else self._fallback_get
            try:
                text = get(url, {"Accept": "application/json", **self._headers.get(gw, {})})
            except Exception as e:  # noqa: BLE001 — this host; try the next
                self._note(i + 1, _failure_kind(e))
                if _http_status(e) in (404, 410):
                    gone += 1
                continue
            if len(text) > self._max:
                self._note(i + 1, "serviu")         # the gateway worked; the content is bad
                raise MetadataInvalid("metadata body too large")
            try:
                doc = json.loads(text)
            except ValueError:
                self._note(i + 1, "não-json")
                not_json += 1                       # a throttle page, maybe
                continue
            self._note(i + 1, "serviu")
            if not isinstance(doc, dict):
                raise MetadataInvalid("metadata is not an object")
            if i:
                self.stats["rescued"] += 1
            return doc
        if gone:
            raise MetadataPinGone(f"{gone} of {len(urls)} gateway(s) said 404/410")
        if not_json:
            raise GatewayNotJson(f"{not_json} of {len(urls)} gateway(s) answered non-JSON")
        raise GatewayUnavailable(f"{len(urls)} gateway(s), none answered")


# --------------------------------------------------------------------------- #
# GENERIC family — per-batch rotation                                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Provider:
    """One generic read path: a public RPC URL per chain + a public IPFS
    gateway. Chains missing from `rpc_urls` are simply unreadable through
    this provider (KeyError at read time — R1, loud)."""

    name: str
    rpc_urls: dict[str, str]
    gateway: str


class GenericMetadata:
    """The generic fetcher with rotation PER BATCH. Reads outside a batch
    are refused (RuntimeError): every caller must declare its batch, so
    that the invariant 'one provider per batch' is enforced by construction
    rather than by convention.

        with generic.batch() as fetch:
            live_check.check(sealed)       # 8 reads, one provider

    The `batch()` context picks the next provider round-robin (thread-safe
    counter), builds the Erc721Metadata for it, and exposes it as the
    callable `fetch(chain, contract, token_id)`. `fetch_bytes(url)` (for the
    image batch) uses the SAME provider's gateway. `provider_log` records
    which provider served each batch — the test counts distinct providers
    per batch and requires exactly 1."""

    def __init__(self, *, providers: Sequence[Provider], http_get: HttpGet,
                 http_post: HttpPost, http_get_bytes: HttpGetBytes):
        if not providers:
            raise ValueError("GenericMetadata needs at least one provider")
        self._providers = list(providers)
        self._get = http_get
        self._post = http_post
        self._get_bytes = http_get_bytes
        self._i = 0
        self._lock = threading.Lock()
        self._current: tuple[Provider, Erc721Metadata] | None = None
        self.provider_log: list[str] = []

    @property
    def providers(self) -> list:
        return list(self._providers)

    def _next(self) -> Provider:
        with self._lock:
            p = self._providers[self._i % len(self._providers)]
            self._i += 1
        return p

    @contextmanager
    def batch(self) -> Iterator[Callable[[str, str, int], dict | None]]:
        p = self._next()
        rpcs = chain_rpcs(p.rpc_urls, http_post=self._post)
        fetcher = Erc721Metadata(rpcs=rpcs, gateway=p.gateway, http_get=self._get,
                                 content_addressed_only=False)
        self._current = (p, fetcher)
        self.provider_log.append(p.name)
        try:
            yield fetcher
        finally:
            self._current = None

    def __call__(self, chain: str, contract: str, token_id: int) -> dict | None:
        if self._current is None:
            raise RuntimeError("generic read outside a batch — wrap the "
                               "caller in `with generic.batch():` (one "
                               "provider per batch)")
        return self._current[1](chain, contract, token_id)

    def read_live(self, chain: str, contract: str, token_id: int) -> LiveRead:
        """The live check's read: the batch provider's RPC only — tokenURI +
        ownerOf. The gateway is never touched here (Opus, 06/09): the
        repeated path compares content ids, and a gateway that saw the same
        batch every clue would be P0-A one layer down."""
        if self._current is None:
            raise RuntimeError("generic read outside a batch — wrap the "
                               "caller in `with generic.batch():` (one "
                               "provider per batch)")
        return self._current[1].read_live(chain, contract, token_id)

    def fetch_bytes(self, url: str) -> bytes | None:
        """Image bytes via the batch's gateway (ipfs://… rewritten; plain
        https fetched as-is). Outside a batch: refused, like reads. A
        transport failure PROPAGATES (16/09): the image batch counts the
        cause (HTTP code / timeout) and retries the read in its next round —
        swallowing it here left the refusal saying only "unavailable"."""
        if self._current is None:
            raise RuntimeError("generic fetch outside a batch")
        p = self._current[0]
        target = gateway_url(url, p.gateway)
        if target is None:
            return None
        return self._get_bytes(target, {})


class RotatingLiveCheck:
    """The live check: ONE RPC read of the target (tokenURI + ownerOf, CID
    comparison, no gateway), with FAILOVER across the public providers.

    Why failover is now allowed — and required (Fable, 17/09). The old rule
    said "never change provider mid-batch", because switching split the
    batch of 8 across providers and broke the anonymity set. With the
    decoys gone there is no batch to split, so the rule has no subject
    left — and what it was costing is real: one public RPC having a bad
    minute on clue 3 meant `unavailable → hold → no clue`, a hunt stopping
    for a reason that has nothing to do with the target. That is the same
    class of failure the larder work just removed twice.

    So: try the next provider in the rotation before giving up. HOLD is
    reserved for ALL of them being down, which is the only case where we
    genuinely cannot know.

    THE ROTATION IS THE SECURITY PROPERTY, NOT AN OPTIMISATION. Reading the
    same token every clue from ONE provider hands that provider the answer
    to every hunt, in real time; spread across five, no one of them sees a
    series. Do not collapse this to a single provider, and above all not to
    our keyed RPC."""

    def __init__(self, *, generic: GenericMetadata, rng=None):
        self._generic = generic
        self._rng = rng

    def check(self, sealed):
        from .hunt import LIVE_UNAVAILABLE, LiveCheck
        last = None
        for _ in range(max(1, len(self._generic.providers))):
            # O batch entra-se pelo efeito, não pelo handle: é ele que fixa o
            # provider desta ronda, e `read_live` recusa-se a ler fora dele.
            with self._generic.batch():
                verdict = LiveCheck(read_live=self._generic.read_live,
                                    rng=self._rng).check(sealed)
            if verdict.status != LIVE_UNAVAILABLE:
                return verdict          # measured: intact, mutated or burned
            last = verdict              # this provider could not answer
        return last                     # all of them down: hold (R8)


# --------------------------------------------------------------------------- #
# Marketplace link resolver (MANDATORY in production)                          #
# --------------------------------------------------------------------------- #

# Rarible's blockchain slugs for the chains the claim vocabulary knows.
RARIBLE_CHAIN = {"ethereum": "ETHEREUM", "polygon": "POLYGON", "base": "BASE",
                 "arbitrum": "ARBITRUM", "optimism": "OPTIMISM", "zora": "ZORA"}


class RaribleChainProbe:
    """Which chain owns contract:tokenId? GET /v0.1/items/{CHAIN}:{contract}
    :{tokenId} per known chain. A hit is a 200 whose `id` echoes the asked
    item (shape pinned by fixture rarible_item_*.json when captured); a
    404 is a miss. Exactly ONE hit → that chain; zero → None; two or more
    (same-nonce deploys) → None, ambiguous. Any transport failure on any
    chain → None: a chain we could not ask might have been the owner."""

    def __init__(self, *, http_get: HttpGet, api_key: str,
                 base_url: str = "https://api.rarible.org/v0.1",
                 chains: Sequence[str] = tuple(RARIBLE_CHAIN)):
        if not api_key:
            raise ValueError("RaribleChainProbe needs an API key")
        self._get = http_get
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._chains = tuple(chains)

    def __call__(self, contract: str, token_id: int) -> str | None:
        hits: list[str] = []
        for chain in self._chains:
            slug = RARIBLE_CHAIN.get(chain)
            if slug is None:
                continue
            item = f"{slug}:{contract.lower()}:{token_id}"
            url = f"{self._base}/items/{quote(item, safe=':')}"
            try:
                text = self._get(url, {"X-API-KEY": self._key,
                                       "Accept": "application/json"})
            except Exception as e:  # noqa: BLE001
                if _is_not_found(e):
                    continue
                return None                      # unasked chain: ambiguous
            try:
                doc = json.loads(text)
            except ValueError:
                return None
            if isinstance(doc, dict) and str(doc.get("id", "")).lower() == item.lower():
                hits.append(chain)
        return hits[0] if len(hits) == 1 else None


class OpenSeaChainProbe:
    """The same question asked of OpenSea: GET /api/v2/chain/{slug}/contract/
    {contract}/nfts/{tokenId} per known chain. MEASURED 2026-09-10
    (fixtures opensea_item_*.json): a hit is a 200 whose `nft.contract` and
    `nft.identifier` echo the asked item; a miss is a 404 {'errors':
    ['Item with identifier N not found']}; an unknown slug is a 400
    ('Unrecognized chain') — treated as ambiguous like any other failure,
    never as a miss. Same verdict rules as RaribleChainProbe: exactly ONE
    hit → that chain; zero or several → None; any non-404 failure → None.
    Six GETs per probe, on a 120-per-minute quota: a shotgun of chainless
    links degrades to 'ambiguous' claims, never to a wrong chain."""

    def __init__(self, *, http_get: HttpGet, api_key: str,
                 base_url: str = "https://api.opensea.io/api/v2",
                 chains: Sequence[str] = tuple(RARIBLE_CHAIN)):
        if not api_key:
            raise ValueError("OpenSeaChainProbe needs an API key")
        from .search_guard import OPENSEA_CHAIN
        self._get = http_get
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._chains = tuple(c for c in chains if c in OPENSEA_CHAIN)
        self._slugs = OPENSEA_CHAIN

    def __call__(self, contract: str, token_id: int) -> str | None:
        hits: list[str] = []
        want_c = contract.lower()
        for chain in self._chains:
            slug = self._slugs[chain]
            url = f"{self._base}/chain/{slug}/contract/{want_c}/nfts/{int(token_id)}"
            try:
                text = self._get(url, {"X-API-KEY": self._key,
                                       "Accept": "application/json"})
            except Exception as e:  # noqa: BLE001
                if _is_not_found(e):
                    continue
                return None                      # unasked chain: ambiguous
            try:
                doc = json.loads(text)
            except ValueError:
                return None
            nft = doc.get("nft") if isinstance(doc, dict) else None
            if (isinstance(nft, dict)
                    and str(nft.get("contract", "")).lower() == want_c
                    and str(nft.get("identifier", "")) == str(int(token_id))):
                hits.append(chain)
        return hits[0] if len(hits) == 1 else None

    def item_status(self, chain: str, contract: str, token_id: int) -> str | None:
        """ONE item on ONE chain (30/09) — measurement for the uniqueness
        guard's blind canary: "missing" (a 404: OpenSea does not know it),
        "flagged" (it knows it and marks it is_suspicious / is_disabled /
        is_nsfw — fields measured in opensea_item_ethereum.json), "clean",
        or None for any other answer. Only these words leave here."""
        seen = self.item_seen(chain, contract, token_id)
        return seen[0] if seen else None

    def item_seen(self, chain: str, contract: str,
                  token_id: int) -> tuple[str, str | None] | None:
        """`item_status` plus the NAME OpenSea holds for the item (01/10): the
        guard compares it with the name it searched — in memory, and only a
        count of same / different / none leaves the guard. Same request."""
        slug = self._slugs.get(chain)
        if slug is None:
            return None
        want_c = contract.lower()
        url = f"{self._base}/chain/{slug}/contract/{want_c}/nfts/{int(token_id)}"
        try:
            text = self._get(url, {"X-API-KEY": self._key, "Accept": "application/json"})
        except Exception as e:  # noqa: BLE001
            return ("missing", None) if _is_not_found(e) else None
        try:
            doc = json.loads(text)
        except ValueError:
            return None
        nft = doc.get("nft") if isinstance(doc, dict) else None
        if not (isinstance(nft, dict)
                and str(nft.get("contract", "")).lower() == want_c
                and str(nft.get("identifier", "")) == str(int(token_id))):
            return None
        flagged = any(nft.get(k) is True for k in ("is_suspicious", "is_disabled", "is_nsfw"))
        name = nft.get("name")
        return ("flagged" if flagged else "clean",
                name if isinstance(name, str) and name.strip() else None)


def _is_not_found(e: Exception) -> bool:
    code = getattr(e, "code", None) or getattr(e, "status", None)
    return code == 404


PageResolver = Callable[[str], TargetRef | None]


class MarketplaceLinkResolver:
    """resolve_link(url) -> TargetRef | None — the injected resolver
    `ClaimJudge.judge(text, resolve_link=…)` calls for links claim.py could
    not parse (no chain, or slug-only).

      1. contract + tokenId in the URL (path or ?tokenId=) → chain probe
      2. otherwise a per-host page resolver, if one is registered — these
         are written against captured HTML only (scripts/capturar_target.py
         foundation/superrare); none registered = None, fail-closed.
    """

    def __init__(self, *, chain_probe: Callable[[str, int], str | None],
                 page_resolvers: dict[str, PageResolver] | None = None):
        self._probe = chain_probe
        self._pages = dict(page_resolvers or {})

    def __call__(self, url: str) -> TargetRef | None:
        u = (url or "").strip()
        m = _ADDR_TID_RE.search(u)
        if m:
            addr, tid = m.group(1), int(m.group(2))
        else:
            ma, mq = _ADDR_RE.search(u), _QUERY_TID_RE.search(u)
            if ma and mq:
                addr, tid = ma.group(0), int(mq.group(1))
            else:
                addr, tid = "", -1
        if addr:
            chain = self._probe(addr, tid)
            if chain is None or CHAIN_ALIASES.get(chain) is None:
                return None
            return TargetRef(chain=CHAIN_ALIASES[chain], contract=addr.lower(),
                             token_id=tid)
        host = urlsplit(u).netloc.lower().split("@")[-1].split(":")[0]
        for dom, fn in self._pages.items():
            if host == dom or host.endswith("." + dom):
                try:
                    return fn(u)
                except Exception:  # noqa: BLE001 — fail-closed
                    return None
        return None


# --------------------------------------------------------------------------- #
# LLM judge (batched) and vision                                               #
# --------------------------------------------------------------------------- #

JUDGE_SYSTEM = """You review NFT artworks for a treasure-hunt game in which \
players must identify ONE existing NFT from riddle-like clues about it. For \
EACH item in the JSON list you receive, answer two questions:
1. writable — can 7 gradually-easier clues be written about it under these \
rules: clues may use the title's words obliquely, the artwork's visual \
content, its mood, its lore; never the artist's name, never a URL, never a \
platform or chain; the piece must be distinctive enough that the right \
candidate is checkable against the clues and a wrong one is not.
2. content_ok — is it safe to point a thousand strangers at it: no sexual \
content, no gore, no hate symbols, no doxxing, no plagiarised famous artwork \
or brand, no scam/impersonation vibe.
Answer ONLY a JSON array, one object per item IN THE SAME ORDER, with keys \
"index" (int), "writable" (bool), "content_ok" (bool), "reason" (short, \
never quoting the title)."""


class AnthropicBatchJudge:
    """BatchJudge: judge(batch) -> list[JudgeVerdict | None], one call for
    the whole batch (target hidden among fresh decoys — hunt.judge_in_batch
    shuffles). Text-only today (title + description + image URL host): a
    vision pass over 8 images per draw is a cost/leak trade-off for a
    later review, not a silent default. Any parse trouble → a list of
    None of the right length, which the caller reads as unreachable."""

    def __init__(self, client, model: str, *, max_tokens: int = 1200):
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def __call__(self, batch: Sequence[Target]) -> list[JudgeVerdict | None]:
        items = [{"index": i, "title": t.name_onchain or t.name,
                  "description": (t.description or "")[:600],
                  "image_host": urlsplit(t.image or "").netloc or "ipfs"}
                 for i, t in enumerate(batch)]
        try:
            resp = self._client.messages.create(
                model=self._model, max_tokens=self._max_tokens,
                system=JUDGE_SYSTEM,
                messages=[{"role": "user",
                           "content": json.dumps(items, ensure_ascii=False)}])
            text = "".join(getattr(b, "text", "") for b in resp.content)
            rows = json.loads(_strip_fence(text))
        except Exception:  # noqa: BLE001 — unreachable/malformed: None per item
            return [None] * len(batch)
        out: list[JudgeVerdict | None] = [None] * len(batch)
        if not isinstance(rows, list):
            return out
        for r in rows:
            if not isinstance(r, dict):
                continue
            try:
                i = int(r.get("index"))
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(batch):
                out[i] = JudgeVerdict(writable=bool(r.get("writable")),
                                      content_ok=bool(r.get("content_ok")),
                                      reason=str(r.get("reason", ""))[:160])
        return out


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


VISION_PROMPT = ("Describe this artwork in 4-6 sentences for someone who "
                 "cannot see it: subjects, composition, palette, mood, "
                 "notable details, medium/style. Do not guess the title or "
                 "the artist. Plain prose, no lists.")


def decode_data_image(uri: str, *, max_bytes: int) -> bytes | None:
    """Os bytes de uma imagem GUARDADA NA CADEIA (`data:…`), ou None.

    Não confundir com `decode_data_uri` (acima), que lê METADATA JSON
    on-chain e devolve um dict. Nomes diferentes de propósito: com o mesmo
    nome, este sobrepunha-se àquele e a leitura de metadata `data:` partia.

    O DEFEITO QUE ISTO FECHA (29/09). `uri_is_content_addressed` aceita
    `data:` — e bem: é a forma mais imutável que existe, a imagem vive no
    próprio contrato. Mas o teste da imagem e o descarregamento da arte só
    sabiam ir a gateways IPFS, e `gateway_url` devolve None para `data:`.
    O teste saltava todos os gateways e respondia "sem bytes": nenhum NFT
    on-chain conseguiu alguma vez entrar na despensa, e o relatório dizia
    que era culpa deles.

    Aceita base64 e percent-encoding. Recusa acima de `max_bytes` ANTES de
    descodificar — uma string de 200 MB num tokenURI não pode virar 200 MB
    em memória. None para qualquer coisa que não se descodifique: é do
    candidato, não nosso."""
    import base64
    from urllib.parse import unquote_to_bytes

    u = (uri or "").strip()
    if not u.lower().startswith("data:"):
        return None
    header, sep, payload = u.partition(",")
    if not sep:
        return None
    try:
        if ";base64" in header.lower():
            if len(payload) * 3 // 4 > max_bytes:
                return None
            data = base64.b64decode(payload, validate=False)
        else:
            if len(payload) > max_bytes * 3:      # %XX triplica o tamanho
                return None
            data = unquote_to_bytes(payload)
    except Exception:  # noqa: BLE001 — base64 torto é do candidato
        return None
    return data if 0 < len(data) <= max_bytes else None


def _inline_bitmap(uri: str, *, max_bytes: int) -> bytes | None:
    """Os bytes de um `data:` numa forma que a visão lê: PNG/JPEG/GIF/WebP
    tal como vêm, SVG convertido para PNG. None se nenhuma das duas."""
    data = decode_data_image(uri, max_bytes=max_bytes)
    if not data:
        return None
    if sniff_media_type(data) is not None:
        return data
    if looks_like_svg(data):
        return rasterize_svg(data)
    return None


def probe_inline_image(uri: str, *, max_bytes: int,
                       probe_bytes: int) -> tuple[bytes, int] | None:
    """O teste da imagem para `data:` — mesma forma que o probe por
    gateway: (primeiros bytes, tamanho), ou None. Um SVG só passa se
    rasterizar (ver `rasterize_svg`)."""
    data = _inline_bitmap(uri, max_bytes=max_bytes)
    if data and sniff_media_type(data[:probe_bytes]) is not None:
        return data[:probe_bytes], len(data)
    return None


def inline_artwork(uri: str, *, max_bytes: int) -> bytes | None:
    """A arte inteira de um `data:`, já pronta para a visão, ou None."""
    data = _inline_bitmap(uri, max_bytes=max_bytes)
    if data and sniff_media_type(data) is not None:
        return shrink_for_vision(data)
    return None


# --------------------------------------------------------------------------- #
# SVG → PNG (29/09)                                                             #
# --------------------------------------------------------------------------- #
#
# PORQUÊ. A primeira colheita com imagens on-chain a funcionar deu 9
# candidatos e 0 guardados; 5 dos 9 eram SVG. Arte gerada dentro do contrato
# é quase sempre SVG, e a visão só lê PNG/JPEG/GIF/WebP. Decisão do Pedro,
# 29/09: converter para PNG, para que a visão veja a imagem real — as pistas
# têm de descrever o que o jogador vê no marketplace.
#
# PORQUE resvg-py (e não CairoSVG): wheel autocontido (Rust), sem biblioteca
# de sistema — o CairoSVG precisa do libcairo instalado no Railway. MIT.
#
# O QUE RECUSAMOS, e porquê cada um:
#   · referências externas (href/src/url() que não sejam `#…` nem `data:`) —
#     o renderizador podia ler ficheiros do nosso contentor, e uma obra que
#     depende de recursos de fora não é a obra que está na cadeia;
#   · sem tamanho legível (viewBox ou width/height) — sem ele não sabemos
#     limitar o PNG, e um SVG de 1×100000 viraria gigabytes;
#   · o PNG sair de uma só cor — é o sintoma de texto sem fonte, ou de arte
#     que não desenhou. Dar isso à visão escreveria pistas sobre um vazio.
# Sem a biblioteca instalada: None — o SVG fica recusado como antes, nunca
# rebenta.

SVG_MAX_BYTES = 2_000_000        # o código SVG; arte on-chain real é KB
SVG_RENDER_EDGE = 1024           # lado maior do PNG; a visão não ganha acima

_SVG_EXTERNAL = re.compile(
    rb"""(?:\bhref|\bsrc)\s*=\s*["']\s*(?!#|data:)[^"'\s]"""
    rb"""|url\(\s*["']?\s*(?!#|data:)[^)"'\s]"""
    rb"""|@import""", re.I)
_SVG_ROOT = re.compile(rb"<svg\b[^>]*>", re.I | re.S)
_SVG_VIEWBOX = re.compile(
    rb"""viewBox\s*=\s*["']\s*[-\d.eE+]+[\s,]+[-\d.eE+]+[\s,]+([\d.eE+]+)[\s,]+([\d.eE+]+)""",
    re.I)
_SVG_DIM = re.compile(rb"""\b(width|height)\s*=\s*["']\s*([\d.]+)\s*(?:px)?\s*["']""", re.I)

# Uma fonte que viaja com o código: no contentor do Railway não há garantia
# de fontes de sistema, e SVG on-chain tem muito texto. DejaVu Sans —
# licença Bitstream Vera, redistribuição livre (ver fonts/LICENSE-DejaVu).
_FONT_DIR = Path(__file__).with_name("fonts")


def looks_like_svg(data: bytes) -> bool:
    head = data[:4096].lstrip().lower()
    return head.startswith(b"<") and b"<svg" in head


def svg_size(data: bytes) -> tuple[float, float] | None:
    """(largura, altura) intrínsecas — viewBox primeiro, depois width/height
    em unidades absolutas. None se não se consegue ler (percentagens, nada)."""
    root = _SVG_ROOT.search(data[:65536])
    if not root:
        return None
    tag = root.group(0)
    m = _SVG_VIEWBOX.search(tag)
    if m:
        w, h = float(m.group(1)), float(m.group(2))
    else:
        dims = {k.lower(): float(v) for k, v in _SVG_DIM.findall(tag)}
        w, h = dims.get(b"width", 0.0), dims.get(b"height", 0.0)
    return (w, h) if w > 0 and h > 0 else None


def rasterize_svg(data: bytes, *, max_bytes: int = SVG_MAX_BYTES,
                  edge: int = SVG_RENDER_EDGE) -> bytes | None:
    """SVG → PNG com o lado maior em `edge`, ou None (ver a lista acima).
    Todas as recusas baratas vêm ANTES de importar a biblioteca, e nenhuma
    exceção sai daqui: um SVG torto é do candidato, não nosso."""
    if not data or len(data) > max_bytes or not looks_like_svg(data):
        return None
    if _SVG_EXTERNAL.search(data):
        return None
    size = svg_size(data)
    if size is None:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        import resvg_py
    except ImportError:
        return None
    w, h = size
    fit = {"width": edge} if w >= h else {"height": edge}
    fonts = [str(p) for p in sorted(_FONT_DIR.glob("*.ttf"))]
    try:
        png = bytes(resvg_py.svg_to_bytes(svg_string=text, font_files=fonts or None,
                                          **fit))
    except Exception:  # noqa: BLE001 — o SVG não renderiza: do candidato
        return None
    if sniff_media_type(png) != "image/png" or _is_blank(png):
        return None
    return png


def _is_blank(png: bytes) -> bool:
    """Uma só cor em toda a imagem. Sem Pillow, não se sabe: não é vazio."""
    try:
        from PIL import Image
    except ImportError:
        return False
    import io
    try:
        with Image.open(io.BytesIO(png)) as im:
            im = im.convert("RGBA")
            return all(lo == hi for lo, hi in im.getextrema())
    except Exception:  # noqa: BLE001
        return True


def sniff_media_type(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


# What the vision API will actually accept. These are NOT our numbers —
# they are the provider's, and the 24 MB we allow through the probe is our
# own ceiling on how much we are willing to download, a different thing.
# Confusing the two is what broke /prepare on 16/09: a 6 MB artwork was
# fetched happily and then refused by the API with a bare BadRequestError.
VISION_MAX_BYTES = 3_500_000        # ~5 MB once base64 inflates it by 4/3
VISION_MAX_EDGE = 1568              # the provider's own "no gain above this"


def shrink_for_vision(data: bytes, *, max_bytes: int = VISION_MAX_BYTES,
                      max_edge: int = VISION_MAX_EDGE) -> bytes | None:
    """Make an artwork small enough to describe, or say it cannot be.

    1/1 art is enormous — 14 MB, 19 MB, one of 171 MB, all measured in the
    wild on 16/09. The vision API takes about 5 MB and gains nothing above
    ~1568 px on the long edge, so we resize rather than refuse: a clue
    about a lighthouse does not need the pixels the collector paid for.

    Returns None only when the bytes cannot be opened as an image at all —
    which is the candidate's problem (an SVG, a video, a throttle page
    that happened to start with image magic), not ours. Without Pillow it
    degrades to a plain size cap instead of failing.
    """
    if not data:
        return None
    try:
        from PIL import Image
    except ImportError:                       # degrade, never crash
        return data if len(data) <= max_bytes else None
    import io

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            if (len(data) <= max_bytes and max(im.size) <= max_edge
                    and sniff_media_type(data)):
                return data                   # already fine: don't re-encode
            im = im.convert("RGB")
            im.thumbnail((max_edge, max_edge))
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=85, optimize=True)
            shrunk = out.getvalue()
    except Exception:  # noqa: BLE001 — not an image we can read
        return None
    return shrunk if len(shrunk) <= max_bytes else None


CONTENT_PROMPT = ("Content review for a public treasure hunt that will point "
                  "a thousand strangers at this artwork. Answer ONLY a JSON "
                  "object {\"content_ok\": true|false, \"reason\": \"...\"}. "
                  "content_ok is false for sexual content, gore, hate symbols, "
                  "doxxing, a plagiarised famous artwork or brand, or a scam/"
                  "impersonation vibe. Never guess the title or the artist.")


class AnthropicVision:
    """describe(bytes) -> str for clues.describe_image_batched, and
    content_ok(bytes) -> bool | None for its content guard (Opus, 06/09:
    the guard sees the image, once, on bytes already fetched). Non-image
    bytes (an HTML throttle page, an SVG, a video) → '' / None — the caller
    fails closed rather than describing a 404 page."""

    def __init__(self, client, model: str, *, max_tokens: int = 600):
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def __call__(self, data: bytes) -> str:
        media = sniff_media_type(data or b"")
        if media is None:
            return ""
        resp = self._client.messages.create(
            model=self._model, max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": media,
                    "data": base64.b64encode(data).decode()}},
                {"type": "text", "text": VISION_PROMPT},
            ]}])
        return "".join(getattr(b, "text", "") for b in resp.content).strip()

    def content_ok(self, data: bytes) -> bool | None:
        media = sniff_media_type(data or b"")
        if media is None:
            return None
        try:
            resp = self._client.messages.create(
                model=self._model, max_tokens=200,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": media,
                        "data": base64.b64encode(data).decode()}},
                    {"type": "text", "text": CONTENT_PROMPT},
                ]}])
            text = "".join(getattr(b, "text", "") for b in resp.content)
            doc = json.loads(_strip_fence(text))
            return bool(doc["content_ok"])
        except Exception:  # noqa: BLE001 — unreachable/malformed: unknown
            return None


__all__ = [
    "AnthropicBatchJudge", "AnthropicVision", "Erc721Metadata",
    "GenericMetadata", "JsonRpc", "MarketplaceLinkResolver", "Provider",
    "RaribleChainProbe", "RotatingLiveCheck", "RpcError", "SEL_OWNEROF",
    "address_from_word", "chain_rpc", "chain_rpcs", "code_bytes",
    "decode_abi_string", "decode_data_uri", "gateway_url", "mint_fetcher",
    "sniff_media_type",
]

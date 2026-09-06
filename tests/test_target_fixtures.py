"""Parsers pinned to RAW captures (scripts/capturar_target.py, run on
Pedro's Mac 2026-09-06 22:06 UTC). Each test replays one fixture through the
production adapter and asserts the classification the game depends on.
When a provider changes shape, re-capture and let these tests say so."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finding_memeland.target.adapters import (
    Erc721Metadata,
    JsonRpc,
    RaribleChainProbe,
    RpcError,
    chain_rpcs,
    mint_fetcher,
)
from finding_memeland.target.search_guard import ClueSearchGuard, RaribleSearch
from finding_memeland.target.sources import ChainEoaCheck, ChainRpc, ChainUnavailable

FIX = Path(__file__).parent / "fixtures" / "target"
C = "0x3b3ee1931dc30c1957379fac9aba94d1c48a5405"


def fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


def replay(name: str):
    """http_post that answers with the fixture's raw body (HTTP errors as
    urllib would raise them)."""
    doc = fixture(name)
    body = doc["body"]
    text = body if isinstance(body, str) else json.dumps(body)
    status = int(doc["_meta"]["status"])

    class HttpErr(Exception):
        code = status

    def post(url, data, headers):
        if status == 0 or status >= 400:
            raise HttpErr(text[:80])
        return text
    return post


def test_fixture_dir_present():
    assert (FIX / "rpc_tokenuri_revert.json").exists(), "corre scripts/capturar_target.py"


# --------------------------- reverts vs outages --------------------------- #


def _is_error_fixture(name: str) -> bool:
    body = fixture(name)["body"]
    return isinstance(body, dict) and "error" in body


@pytest.mark.parametrize("name", ["rpc_tokenuri_revert"])
def test_execution_reverted_shape_reads_as_no_such_token(name):
    """Alchemy (and, on the first capture, publicnode/drpc/1rpc alike):
    code 3 + 'execution reverted: …' + data 0x08c379a0… →
    RpcError(revert=True) → fetcher None."""
    if not _is_error_fixture(name):
        pytest.skip(f"{name} captured a success — covered by the live-token test")
    rpc = JsonRpc(url="https://n", http_post=replay(name))
    with pytest.raises(RpcError) as e:
        rpc.eth_call(C, "0xc87b56dd" + "00" * 32)
    assert e.value.revert
    rpcs = chain_rpcs({"ethereum": "https://n"}, http_post=replay(name))
    f = Erc721Metadata(rpcs=rpcs, gateway="g", http_get=lambda u, h: "{}")
    assert f("ethereum", C, 42) is None


def test_live_token_uri_decodes_and_is_content_addressed():
    """rpc_tokenuri: the live FND token (probed by the capture script). Once
    captured, its tokenURI must ABI-decode and be content-addressed, or the
    Foundation stratum does not enter the pool at all."""
    if _is_error_fixture("rpc_tokenuri"):
        pytest.skip("rpc_tokenuri still holds the #42 revert — re-run the capture")
    from finding_memeland.target.adapters import decode_abi_string, gateway_url
    from finding_memeland.target.refresh import uri_is_content_addressed
    uri = decode_abi_string(fixture("rpc_tokenuri")["body"]["result"])
    assert uri and uri_is_content_addressed(uri), uri
    assert gateway_url(uri, "https://gw/ipfs/").startswith("https://gw/ipfs/")


def test_keyed_fetch_end_to_end_on_real_fixtures():
    """FND #1 through the production path: Alchemy tokenURI (ABI string
    'ipfs://<cid>/metadata.json') → rewritten onto the gateway → Pinata's
    200 JSON → dict with name+image. ownerOf → address whose code is '0x'
    (rpc_getcode_eoa_plain) → EOA True."""
    if _is_error_fixture("rpc_tokenuri"):
        pytest.skip("rpc_tokenuri still holds the #42 revert — re-run the capture")
    gw = fixture("gateway_0")
    assert gw["_meta"]["status"] == 200 and isinstance(gw["body"], dict)
    tokenuri = fixture("rpc_tokenuri")["body"]
    ownerof = fixture("rpc_ownerof")["body"]
    plain = fixture("rpc_getcode_eoa_plain")["body"]

    def post(url, data, headers):
        req = json.loads(data)
        if req["method"] == "eth_call":
            sel = req["params"][0]["data"][:10]
            return json.dumps(tokenuri if sel == "0xc87b56dd" else ownerof)
        if req["method"] == "eth_getCode":
            # the NFT contract has code (R2 canary); the owner has none
            if req["params"][0].lower() == C:
                return json.dumps(fixture("rpc_getcode_contract")["body"])
            return json.dumps(plain)
        raise AssertionError(req["method"])

    def get(url, headers):
        assert url.startswith("https://gw/ipfs/Qm") and url.endswith("/metadata.json")
        return json.dumps(gw["body"])
    rpcs = chain_rpcs({"ethereum": "https://n"}, http_post=post)
    meta = Erc721Metadata(rpcs=rpcs, gateway="https://gw/ipfs/", http_get=get)("ethereum", C, 1)
    assert meta["name"] and meta.get("image")
    assert ChainEoaCheck(rpcs=rpcs)("ethereum", C, 1) is True


def test_public_rpc_throttle_and_whitelist_errors_are_outages():
    """1rpc: HTTP 429 -32029 'Too Many Requests'; flashbots: HTTP 403
    -32601 'not whitelisted'. Both ChainUnavailable — and the 1rpc body
    is also caught by the throttle rule if it ever arrives with a 200."""
    for name in ("pubrpc_2_tokenuri", "pubrpc_3_tokenuri"):
        if not _is_error_fixture(name):
            pytest.skip(f"{name} answered a success this run")
        rpc = JsonRpc(url="https://n", http_post=replay(name))
        with pytest.raises(ChainUnavailable):
            rpc.eth_call(C, "0xc87b56dd" + "00" * 32)
    body = fixture("pubrpc_2_tokenuri")["body"]
    if isinstance(body, dict) and body.get("error", {}).get("code") == -32029:
        rpc = JsonRpc(url="https://n", http_post=lambda u, d, h: json.dumps(body))
        with pytest.raises(ChainUnavailable):
            rpc.eth_call(C, "0xc87b56dd" + "00" * 32)


def test_plain_eoa_code_is_empty_and_7702_is_still_eoa():
    assert fixture("rpc_getcode_eoa_plain")["body"]["result"] == "0x"
    rpc = ChainRpc(chain="ethereum", eth_call=lambda to, d: "0x",
                   get_code=lambda a: fixture("rpc_getcode_eoa_plain")["body"]["result"])
    assert rpc.is_eoa("0x000000000000000000000000000000000000dead")


def test_cloudflare_internal_error_and_ankr_unauthorized_are_outages_not_burns():
    """cloudflare-eth: -32603 'Internal error' for a revert AND for its own
    trouble — indistinguishable, so never a burn. ankr: -32000 needs a key."""
    for name in ("pubrpc_8_tokenuri", "pubrpc_9_tokenuri"):
        assert _is_error_fixture(name), name
        rpcs = chain_rpcs({"ethereum": "https://n"}, http_post=replay(name))
        f = Erc721Metadata(rpcs=rpcs, gateway="g", http_get=lambda u, h: "{}")
        with pytest.raises(ChainUnavailable):
            f("ethereum", C, 42)


def test_llamarpc_521_is_an_outage():
    rpc = JsonRpc(url="https://n", http_post=replay("pubrpc_7_tokenuri"))
    with pytest.raises(ChainUnavailable):
        rpc.get_code(C)


def test_alchemy_free_tier_block_range_cap_is_http_400_read_as_unavailable():
    """Alchemy free: >10 blocks → HTTP 400 with a JSON-RPC error body
    (-32600 'up to a 10 block range'). urllib raises on the 400 before the
    body is read, so the adapter sees an outage: the block is counted
    FAILED (retried later), never scanned-empty. mint_fetcher asks ONE
    block per call, so this path is a guard, not a code path."""
    doc = fixture("rpc_getlogs_11_blocks")
    assert doc["_meta"]["status"] == 400 and "10 block" in doc["body"]["error"]["message"]
    rpc = JsonRpc(url="https://n", http_post=replay("rpc_getlogs_11_blocks"))
    with pytest.raises(ChainUnavailable):
        rpc.get_logs(from_block=1, to_block=11, topics=[])


# ------------------------------ mints -------------------------------------- #


def test_one_block_of_mints_keeps_erc721_and_drops_erc20():
    """Block 12,965,000: 27 Transfer-from-zero logs, 3 are ERC-721 (4
    topics), 24 are ERC-20 mints (3 topics + data). The 4-topic filter is
    what keeps the registry a list of NFT contracts."""
    rpc = JsonRpc(url="https://n", http_post=replay("rpc_getlogs_mints_one_block"))
    mints = mint_fetcher(rpc)(12_965_000)
    assert len(mints) == 3
    assert len({c for c, _ in mints}) == 3
    assert all(c == c.lower() and c.startswith("0x") and t >= 0 for c, t in mints)
    assert ("0x82c7a8f707110f5fbb16184a5933e9f78a34c6ab", 311) in mints


# ------------------------------ EOA / 7702 --------------------------------- #


def test_eip7702_delegated_account_counts_as_eoa():
    """vitalik.eth's code is '0xef0100' + 20 bytes (EIP-7702 delegation) —
    a person's key, not an escrow contract."""
    code = fixture("rpc_getcode_eoa")["body"]["result"]
    assert code.startswith("0xef0100") and len(code) == 8 + 40
    contract_code = fixture("rpc_getcode_contract")["body"]["result"]

    def get_code(addr):
        return code if addr.lower() == "0x" + "5a7fc11397e9a8ad41bf10bf13f22b0a63f96f6d" else contract_code

    rpc = ChainRpc(chain="ethereum", eth_call=lambda to, data: "0x" + "00" * 12
                   + "5a7fc11397e9a8ad41bf10bf13f22b0a63f96f6d", get_code=get_code)
    assert rpc.is_eoa("0x5a7fc11397e9a8ad41bf10bf13f22b0a63f96f6d")
    assert not rpc.is_eoa(C) and rpc.has_code(C)
    assert ChainEoaCheck(rpcs={"ethereum": rpc})("ethereum", C, 1) is True


# ------------------------------ Rarible ------------------------------------ #


def test_rarible_request_limit_reached_fails_closed_everywhere():
    """429 {'code': 'UNAUTHORIZED', 'message': 'Request limit reached…'}:
    the probe answers None (never 'not on this chain'), the search guard
    reports blind (found=None), the uniqueness check raises → None."""
    doc = fixture("rarible_item_ethereum")
    assert doc["_meta"]["status"] == 429 and doc["body"]["code"] == "UNAUTHORIZED"

    class Err(Exception):
        code = 429

    def get(url, headers):
        raise Err(json.dumps(doc["body"]))
    probe = RaribleChainProbe(http_get=get, api_key="k")
    assert probe(C, 42) is None

    def post(url, body, headers):
        raise Err(json.dumps(fixture("rarible_search_named")["body"]))
    search = RaribleSearch(http_post=post, api_key="k")
    guard = ClueSearchGuard(search=search, retries=0)
    v = guard.check("a lighthouse on black rock", target_item_id=f"ETHEREUM:{C}:42",
                    target_name_onchain="Salt Harbor")
    assert v.ok is False and v.found is None


# ------------------------------ gateways ----------------------------------- #


def test_public_rpcs_that_serve_the_live_token_decode_identically():
    """publicnode, drpc, merkle, mevblocker, nodies (pubrpc 0,1,4,5,6):
    the same ABI string for FND #1 — the rotation list for the live check."""
    from finding_memeland.target.adapters import decode_abi_string
    uris = set()
    for i in (0, 1, 4, 5, 6):
        name = f"pubrpc_{i}_tokenuri"
        if _is_error_fixture(name):
            pytest.skip(f"{name} errored this run")
        uris.add(decode_abi_string(fixture(name)["body"]["result"]))
    assert len(uris) == 1 and next(iter(uris)).startswith("ipfs://")


def test_cloudflare_challenge_page_is_never_metadata():
    """ipfs.io / gateway.ipfs.io / dweb.link / w3s.link / nftstorage.link
    answer a 403 'Just a moment…' JS challenge to non-browsers; the rest
    time out, fail DNS or serve an expired certificate. A 200 with that HTML
    would be the worst case — pinned here as ChainUnavailable, never
    'burned'."""
    html = fixture("gateway_8")["body"]
    assert "Just a moment" in html
    rpcs = chain_rpcs({"ethereum": "https://n"}, http_post=lambda u, b, h: json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": _abi("ipfs://QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN")}))
    f = Erc721Metadata(rpcs=rpcs, gateway="https://gw/ipfs/", http_get=lambda u, h: html)
    with pytest.raises(ChainUnavailable):
        f("ethereum", C, 1)


def _abi(s: str) -> str:
    b = s.encode()
    return ("0x" + (32).to_bytes(32, "big").hex() + len(b).to_bytes(32, "big").hex()
            + b.hex() + "00" * (-len(b) % 32))

"""Production adapters (5/6) — offline, on synthetic payloads pinned to the
standard shapes; fixture-pinned tests join as captures land."""

from __future__ import annotations

import base64
import json
import random

import pytest

from finding_memeland.target.adapters import (
    AnthropicBatchJudge,
    AnthropicVision,
    Erc721Metadata,
    GenericMetadata,
    JsonRpc,
    MarketplaceLinkResolver,
    Provider,
    RaribleChainProbe,
    RotatingLiveCheck,
    RpcError,
    chain_rpc,
    chain_rpcs,
    code_bytes,
    decode_abi_string,
    decode_data_uri,
    gateway_url,
    mint_fetcher,
    sniff_media_type,
)
from finding_memeland.target.claim import TargetRef
from finding_memeland.target.hunt import (
    LIVE_INTACT,
    LIVE_UNAVAILABLE,
    Decoy,
    SealedTarget,
)
from finding_memeland.target.selector import Target, metadata_hash
from finding_memeland.target.sources import ChainUnavailable

C = "0x3b3ee1931dc30c1957379fac9aba94d1c48a5405"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def abi_string(s: str) -> str:
    b = s.encode()
    return ("0x" + (32).to_bytes(32, "big").hex() + len(b).to_bytes(32, "big").hex()
            + b.hex() + "00" * (-len(b) % 32))


class FakeNode:
    """A JSON-RPC endpoint as an http_post callable."""

    def __init__(self, *, uris=None, code=None, logs=None, throttle=False,
                 down=False, burned=()):
        self.uris = uris or {}
        self.burned = set(burned)
        self.code = code or {}
        self.logs = logs or []
        self.throttle = throttle
        self.down = down
        self.calls: list[str] = []

    def __call__(self, url, body, headers):
        if self.down:
            raise ConnectionError("down")
        req = json.loads(body)
        self.calls.append(req["method"])
        if self.throttle:
            return json.dumps({"jsonrpc": "2.0", "id": 1,
                               "error": {"code": 429, "message": "Too many requests"}})
        m, p = req["method"], req["params"]
        if m == "eth_call":
            data = p[0]["data"]
            tid = int(data[10:], 16)
            if data.startswith("0xc87b56dd"):
                if tid in self.uris:
                    return json.dumps({"jsonrpc": "2.0", "id": 1,
                                       "result": abi_string(self.uris[tid])})
                return json.dumps({"jsonrpc": "2.0", "id": 1,
                                   "error": {"code": 3, "message": "execution reverted"}})
            if data.startswith("0x6352211e"):          # ownerOf
                if tid in self.uris and tid not in self.burned:
                    return json.dumps({"jsonrpc": "2.0", "id": 1,
                                       "result": "0x" + "00" * 12 + "ab" * 20})
                return json.dumps({"jsonrpc": "2.0", "id": 1,
                                   "error": {"code": 3, "message": "execution reverted: owner query for nonexistent token"}})
        if m == "eth_getCode":
            return json.dumps({"jsonrpc": "2.0", "id": 1,
                               "result": self.code.get(p[0].lower(), "0x")})
        if m == "eth_getLogs":
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": self.logs})
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": None})


# ------------------------------ JSON-RPC ----------------------------------- #


def test_jsonrpc_revert_is_rpcerror_and_transport_is_chainunavailable():
    rpc = JsonRpc(url="https://n/x", http_post=FakeNode(uris={}))
    with pytest.raises(RpcError):
        rpc.eth_call(C, "0xc87b56dd" + "00" * 32)
    with pytest.raises(ChainUnavailable):
        JsonRpc(url="https://n/x", http_post=FakeNode(down=True)).get_code(C)
    with pytest.raises(ChainUnavailable):
        JsonRpc(url="https://n/x", http_post=FakeNode(throttle=True)).get_code(C)
    with pytest.raises(ChainUnavailable):
        JsonRpc(url="https://n/x", http_post=lambda u, b, h: "<html>rate limited").get_code(C)


def test_chain_rpc_is_bound_to_its_chain_and_empty_urls_are_skipped():
    with pytest.raises(ValueError):
        chain_rpc(chain="", url="https://n", http_post=FakeNode())
    with pytest.raises(ValueError):
        chain_rpc(chain="ethereum", url="", http_post=FakeNode())
    rpcs = chain_rpcs({"ethereum": "https://e", "base": ""}, http_post=FakeNode())
    assert set(rpcs) == {"ethereum"} and rpcs["ethereum"].chain == "ethereum"


# ----------------------------- ABI + URIs ---------------------------------- #


def test_decode_abi_string_round_trip_and_garbage_raises():
    assert decode_abi_string(abi_string("ipfs://QmX/1.json")) == "ipfs://QmX/1.json"
    assert decode_abi_string(abi_string("")) == ""
    for bad in ("0x", "0x00", "0x" + "ff" * 64, "nope"):
        with pytest.raises(ValueError):
            decode_abi_string(bad)


@pytest.mark.parametrize("uri,expect", [
    ("ipfs://QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN", "GW/QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN"),
    ("ipfs://ipfs/QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN/1.json", "GW/QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN/1.json"),
    ("QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN", "GW/QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN"),
    ("https://ipfs.pixura.io/ipfs/QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN", "GW/QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN"),
    ("https://api.example.com/token/1", "https://api.example.com/token/1"),
    ("data:application/json;base64,e30=", None),
    ("ar://tx", None),
])
def test_gateway_url_rewrites_content_addressed_uris_onto_our_gateway(uri, expect):
    assert gateway_url(uri, "GW/") == expect


def test_decode_data_uri_both_encodings():
    doc = {"name": "Salt Harbor", "image": "ipfs://x"}
    b64 = base64.b64encode(json.dumps(doc).encode()).decode()
    assert decode_data_uri(f"data:application/json;base64,{b64}") == doc
    assert decode_data_uri("data:application/json,%7B%22name%22%3A%22x%22%7D") == {"name": "x"}
    assert decode_data_uri("data:application/json;base64,!!!") is None
    assert decode_data_uri("data:text/plain,hello") is None


# --------------------------- Erc721Metadata -------------------------------- #


def fetcher(node, pages, *, chain="ethereum"):
    def get(url, headers):
        if url in pages:
            v = pages[url]
            if isinstance(v, Exception):
                raise v
            return v
        raise FileNotFoundError(url)
    rpcs = chain_rpcs({chain: "https://n"}, http_post=node)
    return Erc721Metadata(rpcs=rpcs, gateway="https://gw/ipfs/", http_get=get)


def test_metadata_resolves_ipfs_via_gateway_and_data_uris_inline():
    meta = {"name": "Salt Harbor", "image": "ipfs://img"}
    node = FakeNode(uris={1: "ipfs://QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN/1",
                          2: "data:application/json;base64,"
                             + base64.b64encode(json.dumps(meta).encode()).decode()})
    f = fetcher(node, {"https://gw/ipfs/QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN/1": json.dumps(meta)})
    assert f("ethereum", C, 1) == meta
    assert f("ethereum", C, 2) == meta


def test_metadata_revert_is_none_but_outages_are_never_none():
    """Só o REVERT do tokenURI é None (queimado). Gateway em baixo, HTML de
    throttle a 200, ABI ilegível: ChainUnavailable — nunca 'queimado'."""
    node = FakeNode(uris={1: "ipfs://QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN"})
    assert fetcher(node, {})("ethereum", C, 99) is None          # revert
    with pytest.raises(ChainUnavailable):
        fetcher(node, {})("ethereum", C, 1)                        # gateway 404
    with pytest.raises(ChainUnavailable):
        fetcher(node, {"https://gw/ipfs/QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN":
                       "<html>slow down</html>"})("ethereum", C, 1)
    bad_abi = lambda u, b, h: json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x1234"})  # noqa: E731
    rpcs = chain_rpcs({"ethereum": "https://n"}, http_post=bad_abi)
    with pytest.raises(ChainUnavailable):
        Erc721Metadata(rpcs=rpcs, gateway="g", http_get=lambda u, h: "{}")("ethereum", C, 1)


def test_keyed_fetcher_excludes_plain_http_uris_generic_reads_them():
    """Filtro estrutural do pool (commitment.py): URI http simples ⇒ None no
    fetcher KEYED (refresh/selector) — nunca entra. O GENÉRICO (live check)
    lê na mesma: é o hash que decide intacto/mutado, não a forma do URI."""
    node = FakeNode(uris={1: "https://api.example.com/token/1"})
    pages = {"https://api.example.com/token/1": json.dumps({"name": "x", "image": "i"})}
    assert fetcher(node, pages)("ethereum", C, 1) is None
    rpcs = chain_rpcs({"ethereum": "https://n"}, http_post=node)
    live = Erc721Metadata(rpcs=rpcs, gateway="g", http_get=lambda u, h: pages[u],
                          content_addressed_only=False)
    assert live("ethereum", C, 1) == {"name": "x", "image": "i"}


def test_metadata_missing_chain_is_loud_r1():
    f = fetcher(FakeNode(uris={1: "ipfs://QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN"}), {})
    with pytest.raises(KeyError):
        f("base", C, 1)


# ------------------------- mint fetcher / code ----------------------------- #


def test_mint_fetcher_keeps_only_four_topic_transfers_from_zero():
    z = "0x" + "0" * 64
    logs = [
        {"address": C.upper(), "topics": [TRANSFER, z, "0x" + "1" * 64, "0x" + "2a".rjust(64, "0")]},
        {"address": C, "topics": [TRANSFER, z, "0x" + "1" * 64], "data": "0x01"},   # ERC-20
        {"address": C, "topics": [TRANSFER, z, "0x" + "1" * 64, "0x" + "2b".rjust(64, "0")], "removed": True},
    ]
    node = FakeNode(logs=logs)
    rpc = JsonRpc(url="https://n", http_post=node)
    assert mint_fetcher(rpc)(12_965_000) == [(C, 42)]
    assert code_bytes(rpc)(C) == b""
    node.code = {C: "0x6001"}
    assert code_bytes(rpc)(C) == b"\x60\x01"


def test_mint_fetcher_asks_one_block_per_call():
    seen = []

    def post(url, body, headers):
        seen.append(json.loads(body)["params"][0])
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": []})
    mint_fetcher(JsonRpc(url="https://n", http_post=post))(100)
    assert seen[0]["fromBlock"] == seen[0]["toBlock"] == hex(100)
    assert seen[0]["topics"] == [TRANSFER, "0x" + "0" * 64]


# ------------------------ per-BATCH rotation (Opus) ------------------------ #


def _providers(n=3):
    return [Provider(name=f"p{i}", rpc_urls={"ethereum": f"https://rpc{i}"},
                     gateway=f"https://gw{i}/ipfs/") for i in range(n)]


class Transport:
    """Records which PROVIDER served each request, by host."""

    def __init__(self, uris, metas, burned=()):
        self.uris, self.metas = uris, metas
        self.burned = set(burned)
        self.hosts: list[str] = []

    def post(self, url, body, headers):
        self.hosts.append(url.split("//")[1].split("/")[0])
        return FakeNode(uris=self.uris, burned=self.burned)(url, body, headers)

    def get(self, url, headers):
        self.hosts.append(url.split("//")[1].split("/")[0])
        cid = url.rsplit("/", 1)[1]
        return json.dumps(self.metas[cid])

    def get_bytes(self, url, headers):
        self.hosts.append(url.split("//")[1].split("/")[0])
        return b"\x89PNG\r\n\x1a\n" + url.encode()


def cid(i: int) -> str:
    """A base58-valid CIDv0 shape (no '0' in base58 — the digit is 1..9)."""
    return "Qm" + "1" * 43 + str(i + 1)


def sealed_with(n_decoys=7):
    metas = {cid(i): {"name": f"Piece {i}", "image": f"ipfs://Qi{i:044d}"}
             for i in range(n_decoys + 1)}
    uris = {i: f"ipfs://{cid(i)}" for i in range(n_decoys + 1)}
    from finding_memeland.target.refresh import content_id
    t = Target(chain="ethereum", contract=C, token_id=0, name="piece 0",
               name_onchain="Piece 0", description="", image="ipfs://Qi0",
               metadata_sha256=metadata_hash(metas[cid(0)]), epoch="e1",
               token_uri=uris[0], content_id=content_id(uris[0]))
    assert t.content_id
    decoys = tuple(Decoy(chain="ethereum", contract=C, token_id=i, image=f"ipfs://Qi{i:044d}")
                   for i in range(1, n_decoys + 1))
    return SealedTarget(target=t, salt="s" * 32, commitment="c" * 64, decoys=decoys), uris, metas


def test_live_check_batch_uses_exactly_one_provider_and_rotates_between_batches():
    """Regra do Opus (06/09): um fornecedor por lote, escolhido uma vez,
    usado nas 8 leituras; rotação ENTRE lotes. O teste conta fornecedores
    distintos por lote e exige 1 — e exige ZERO gateway: o caminho que se
    repete é RPC puro (tokenURI + ownerOf), o CID decide."""
    sealed, uris, metas = sealed_with()
    tr = Transport(uris, metas)
    generic = GenericMetadata(providers=_providers(3), http_get=tr.get,
                              http_post=tr.post, http_get_bytes=tr.get_bytes)
    lc = RotatingLiveCheck(generic=generic, rng=random.Random(0))
    per_batch: list[set[str]] = []
    for _ in range(4):
        tr.hosts.clear()
        v = lc.check(sealed)
        assert v.status == LIVE_INTACT and v.reads == 8
        assert not any(h.startswith("gw") for h in tr.hosts), tr.hosts
        providers = {h[-1] for h in tr.hosts}      # rpc0 → "0"
        assert len(providers) == 1, tr.hosts
        assert len(tr.hosts) == 16                 # 8 tokenURI + 8 ownerOf, no gateway
        per_batch.append(providers)
    assert [next(iter(p)) for p in per_batch] == ["0", "1", "2", "0"]
    assert generic.provider_log == ["p0", "p1", "p2", "p0"]


def test_live_check_compares_content_ids_not_strings():
    """Migração de gateway com o MESMO CID ⇒ intacto (o que aconteceu à
    pixura); CID diferente ⇒ mutado; URI que deixou de ser content-
    addressed ⇒ mutado; ownerOf a reverter com tokenURI ainda a responder
    ⇒ QUEIMADO (há contratos que servem URI de tokens queimados)."""
    from finding_memeland.target.hunt import LIVE_BURNED, LIVE_MUTATED
    sealed, uris, metas = sealed_with()
    cid0 = cid(0)

    def check_with(live_uri, burned=()):
        u = dict(uris)
        u[0] = live_uri
        tr = Transport(u, metas, burned=burned)
        generic = GenericMetadata(providers=_providers(1), http_get=tr.get,
                                  http_post=tr.post, http_get_bytes=tr.get_bytes)
        v = RotatingLiveCheck(generic=generic, rng=random.Random(0)).check(sealed)
        assert not any(h.startswith("gw") for h in tr.hosts)
        return v
    assert check_with(f"https://other-gateway.io/ipfs/{cid0}").status == LIVE_INTACT
    assert check_with(f"{cid0}").status == LIVE_INTACT              # bare CID
    v = check_with(f"ipfs://{cid(8)}")
    assert v.status == LIVE_MUTATED and v.live_token_uri == f"ipfs://{cid(8)}"
    assert check_with("https://api.example.com/token/0").status == LIVE_MUTATED
    assert check_with(uris[0], burned={0}).status == LIVE_BURNED


def test_generic_reads_outside_a_batch_are_refused():
    sealed, uris, metas = sealed_with()
    tr = Transport(uris, metas)
    generic = GenericMetadata(providers=_providers(1), http_get=tr.get,
                              http_post=tr.post, http_get_bytes=tr.get_bytes)
    with pytest.raises(RuntimeError):
        generic("ethereum", C, 0)
    with pytest.raises(RuntimeError):
        generic.fetch_bytes("ipfs://x")
    with generic.batch():
        assert generic("ethereum", C, 0)["name"] == "Piece 0"
        assert generic.fetch_bytes("ipfs://Qi0").startswith(b"\x89PNG")


def test_no_failover_inside_a_batch_provider_down_is_unavailable():
    """Um fornecedor em baixo NÃO passa o lote para outro (partiria o lote):
    a leitura do alvo é UNAVAILABLE (→ hold), e o lote seguinte roda."""
    sealed, uris, metas = sealed_with()
    tr = Transport(uris, metas)
    down = {"rpc0"}

    def post(url, body, headers):
        if url.split("//")[1].split("/")[0] in down:
            raise ConnectionError("down")
        return tr.post(url, body, headers)
    generic = GenericMetadata(providers=_providers(2), http_get=tr.get,
                              http_post=post, http_get_bytes=tr.get_bytes)
    lc = RotatingLiveCheck(generic=generic, rng=random.Random(0))
    assert lc.check(sealed).status == LIVE_UNAVAILABLE      # p0 down
    assert lc.check(sealed).status == LIVE_INTACT           # p1 serves the next batch
    assert generic.provider_log == ["p0", "p1"]


def test_image_batch_shares_the_live_check_provider_rule():
    from finding_memeland.target.clues import describe_image_batched
    sealed, uris, metas = sealed_with()
    tr = Transport(uris, metas)
    generic = GenericMetadata(providers=_providers(3), http_get=tr.get,
                              http_post=tr.post, http_get_bytes=tr.get_bytes)
    with generic.batch():
        text = describe_image_batched(
            target_image_url=sealed.target.image,
            decoy_image_urls=[d.image for d in sealed.decoys],
            fetch_bytes_generic=generic.fetch_bytes,
            describe=lambda b: "a lighthouse", rng=random.Random(0))
    assert text == "a lighthouse"
    assert len(tr.hosts) == 8 and {h for h in tr.hosts} == {"gw0"}


# ------------------------- link resolver ----------------------------------- #


class NotFound(Exception):
    code = 404


def probe_with(exists: dict[str, bool], *, errors=()):
    def get(url, headers):
        assert headers.get("X-API-KEY") == "k"
        item = url.rsplit("/items/", 1)[1]
        chain = item.split(":")[0]
        if chain in errors:
            raise ConnectionError("boom")
        if exists.get(chain):
            return json.dumps({"id": item, "blockchain": chain})
        raise NotFound()
    return RaribleChainProbe(http_get=get, api_key="k")


def test_chain_probe_one_hit_resolves_two_hits_or_an_error_fail_closed():
    assert probe_with({"POLYGON": True})(C, 42) == "polygon"
    assert probe_with({})(C, 42) is None
    assert probe_with({"ETHEREUM": True, "BASE": True})(C, 42) is None
    assert probe_with({"ETHEREUM": True}, errors={"BASE"})(C, 42) is None
    with pytest.raises(ValueError):
        RaribleChainProbe(http_get=lambda u, h: "", api_key="")


def test_link_resolver_probes_chainless_links_and_defers_slugs_to_page_parsers():
    resolver = MarketplaceLinkResolver(chain_probe=lambda c, t: "ethereum")
    assert resolver(f"https://rarible.com/token/{C}:42") == TargetRef("ethereum", C, 42)
    assert resolver(f"https://blur.io/asset/{C}/7") == TargetRef("ethereum", C, 7)
    assert resolver(f"https://x.io/t/{C}?tokenId=9") == TargetRef("ethereum", C, 9)
    # slug-only, no page parser registered → None (fail-closed)
    assert resolver("https://foundation.app/@artist/salt-harbor/1") is None
    # a registered page parser is consulted; its exceptions are None
    ok = MarketplaceLinkResolver(
        chain_probe=lambda c, t: None,
        page_resolvers={"foundation.app": lambda u: TargetRef("ethereum", C, 1)})
    assert ok("https://foundation.app/@artist/salt-harbor/1") == TargetRef("ethereum", C, 1)
    boom = MarketplaceLinkResolver(
        chain_probe=lambda c, t: None,
        page_resolvers={"foundation.app": lambda u: (_ for _ in ()).throw(RuntimeError())})
    assert boom("https://foundation.app/@artist/x/1") is None
    # probe says an unknown chain word → None
    assert MarketplaceLinkResolver(chain_probe=lambda c, t: "solami")(
        f"https://blur.io/asset/{C}/7") is None


# ------------------------- judge + vision ---------------------------------- #


class FakeMessages:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)

        class B:
            def __init__(s, t):
                s.text = t

        class R:
            content = [B(self.text)]
        return R()


class FakeClient:
    def __init__(self, text):
        self.messages = FakeMessages(text)


def _targets(n):
    return [Target(chain="ethereum", contract=C, token_id=i, name=f"piece {i}",
                   name_onchain=f"Piece {i}", description="d", image="ipfs://x",
                   metadata_sha256="m", epoch="e1") for i in range(n)]


def test_batch_judge_parses_verdicts_by_index_and_fails_closed():
    rows = [{"index": 1, "writable": True, "content_ok": True, "reason": "ok"},
            {"index": 0, "writable": False, "content_ok": True, "reason": "vague"}]
    client = FakeClient("```json\n" + json.dumps(rows) + "\n```")
    out = AnthropicBatchJudge(client, "m")(_targets(3))
    assert out[1].writable and out[1].content_ok
    assert out[0].writable is False
    assert out[2] is None                                    # not answered
    assert len(client.messages.calls) == 1                   # ONE call per batch
    sent = json.loads(client.messages.calls[0]["messages"][0]["content"])
    assert [x["title"] for x in sent] == ["Piece 0", "Piece 1", "Piece 2"]
    assert AnthropicBatchJudge(FakeClient("not json"), "m")(_targets(2)) == [None, None]


def test_vision_refuses_non_image_bytes_and_sends_the_right_media_type():
    client = FakeClient("a lighthouse")
    v = AnthropicVision(client, "m")
    assert v(b"<html>throttled</html>") == "" and not client.messages.calls
    assert v(b"\xff\xd8\xff\xe0jpeg") == "a lighthouse"
    block = client.messages.calls[0]["messages"][0]["content"][0]
    assert block["source"]["media_type"] == "image/jpeg"
    assert sniff_media_type(b"RIFF....WEBPVP8") == "image/webp"
    assert sniff_media_type(b"GIF89a") == "image/gif"


def test_vision_content_ok_parses_json_and_is_none_on_trouble():
    v = AnthropicVision(FakeClient('{"content_ok": false, "reason": "gore"}'), "m")
    assert v.content_ok(b"\x89PNG\r\n\x1a\nimg") is False
    assert AnthropicVision(FakeClient("```json\n{\"content_ok\": true}\n```"), "m").content_ok(
        b"\xff\xd8\xffjpeg") is True
    assert AnthropicVision(FakeClient("not json"), "m").content_ok(b"\xff\xd8\xffjpeg") is None
    assert v.content_ok(b"<html>") is None            # not an image: unknown, never ok

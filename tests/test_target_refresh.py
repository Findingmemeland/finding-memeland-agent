"""Snapshot refresh — build pipeline, offline with fakes."""

from __future__ import annotations

import json

import pytest

from finding_memeland.target.refresh import (
    FakeLister,
    OpenSeaContractLister,
    PlatformItem,
    RefreshFailed,
    RefreshJob,
    TokenRead,
    content_id,
    uri_is_content_addressed,
)
from finding_memeland.target.selector import CurationEpoch, metadata_hash

EPOCH = CurationEpoch(epoch_id="e1")


def item(i: int, name: str, platform: str = "plat",
         chain: str = "ethereum") -> PlatformItem:
    return PlatformItem(platform=platform, chain=chain,
                        contract=f"0x{i:040x}", token_id=i, name=name)


URI = "ipfs://QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN/metadata.json"


def meta_for(name: str) -> dict:
    return {"name": name, "image": "ipfs://img", "description": "d"}


def token(meta, uri: str = URI):
    """fetch_token shape: (uri, metadata) — None metadata = unresolvable."""
    return TokenRead(token_uri=uri, metadata=meta)


def make_job(items, *, meta=None, eoa=None, listers=None):
    metas = meta or {}
    return RefreshJob(
        listers=listers or (FakeLister("plat", items),),
        fetch_token=lambda ch, c, t: token(metas.get(t, meta_for("Salt Harbor"))),
        owner_is_eoa=lambda ch, c, t: (eoa or {}).get(t, True),
        now_iso=lambda: "2026-09-04T20:00:00Z",
    )


def test_happy_path_builds_entries_with_chain_metadata():
    items = [item(1, "Salt Harbor #7"), item(2, "Quiet Meridian")]
    metas = {1: meta_for("Salt Harbor #7"), 2: meta_for("Quiet Meridian")}
    snap, report = make_job(items, meta=metas).build(EPOCH)
    assert report.pulled == 2 and report.pool_size == 2
    assert snap.epoch_id == "e1" and snap.built_at.startswith("2026-09-04")
    by_id = {e.token_id: e for e in snap.entries}
    assert by_id[1].name == "Salt Harbor"            # base name
    assert by_id[1].name_onchain == "Salt Harbor #7"  # canonical, from chain
    assert by_id[1].metadata_sha256 == metadata_hash(metas[1])
    assert by_id[1].chain == "ethereum"       # a cadeia viaja com o item


def test_one_word_base_names_die_locally():
    snap, report = make_job([item(1, "Punk #9278"), item(2, "Quiet Meridian")],
                            meta={2: meta_for("Quiet Meridian")}).build(EPOCH)
    assert report.after_name == 1 and report.pool_size == 1


def test_pool_dedupe_kills_every_bearer_case_insensitively():
    items = [item(1, "en garde #1"), item(2, "En Garde #2"),
             item(3, "Quiet Meridian")]
    snap, report = make_job(items,
                            meta={3: meta_for("Quiet Meridian")}).build(EPOCH)
    assert report.after_pool_dedupe == 1
    assert [e.token_id for e in snap.entries] == [3]


def test_chain_and_api_filters_apply_and_unverifiable_is_counted():
    names = {1: "Amber Comet", 2: "Quiet Meridian", 3: "Salt Harbor",
             4: "Velvet Lighthouse"}
    items = [item(i, names[i]) for i in (1, 2, 3, 4)]
    # 1: no image; 2: contract owner; 3: EOA check unverifiable; 4: clean
    metas = {1: {"name": names[1]}, 2: meta_for(names[2]),
             3: meta_for(names[3]), 4: meta_for(names[4])}
    snap, report = make_job(items, meta=metas,
                            eoa={2: False, 3: None}).build(EPOCH)
    assert report.pool_size == 1
    assert snap.entries[0].token_id == 4
    assert report.unverifiable == 1


def test_refresh_has_no_marketplace_uniqueness_collaborator():
    """Opus 06/09 (5/6): a unicidade global custava 60-80k chamadas de
    marketplace por refresh semanal. Saiu daqui — o RefreshJob nem aceita o
    colaborador; a verificação é preguiçosa, no sorteio (hunt.select_judged),
    e o gate leva a taxa amostrada. Um refresh com um nome duplicado NO POOL
    continua a matar os dois portadores (dedupe local, grátis)."""
    with pytest.raises(TypeError):
        RefreshJob(listers=(), fetch_token=lambda ch, c, t: None,
                   owner_is_eoa=lambda ch, c, t: True,
                   name_is_unique=lambda n, ch, c, t: True,
                   now_iso=lambda: "t")
    items = [item(1, "Quiet Meridian"), item(2, "Quiet Meridian")]
    snap, report = make_job(items, meta={1: meta_for("Quiet Meridian"),
                                         2: meta_for("Quiet Meridian")}).build(EPOCH)
    assert snap.entries == [] and report.after_pool_dedupe == 0


def test_unlistable_platform_fails_the_build_not_silently():
    job = make_job([], listers=(FakeLister("dead", [], raises=True),))
    with pytest.raises(RefreshFailed) as e:
        job.build(EPOCH)
    assert "previous" in str(e.value)


@pytest.mark.parametrize("uri,ok", [
    ("ipfs://QmX/1.json", True),
    ("data:application/json;base64,e30=", True),
    ("  IPFS://QmX ", True),                  # scheme case/space tolerant
    # measured in the wild, 2026-09-05 capture:
    ("QmWh59Pwr18bLWeAQp3EbmCHwAt7EUm4RwjcbGHe9TaYDo", True),   # Async: bare CID
    ("https://ipfs.pixura.io/ipfs/QmZzQhdb6Qa44iyc26Lpk8JFmaSt3ART8vTV6V1mofLvQ7",
     True),                                   # SuperRare: gateway URL, CID in path
    ("https://gateway.pinata.cloud/ipfs/QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN",
     True),
    ("https://api.example.com/token/1", False),  # dynamic by nature
    ("http://x/1.json", False),
    ("Qmshort", False),                       # not a real CID
    ("ar://tx123", False),                    # not accepted until measured
    ("", False),
    (None, False),
])
def test_uri_is_content_addressed(uri, ok):
    assert uri_is_content_addressed(uri) is ok


def test_opensea_lister_paginates_and_parses():
    pages = {
        "": {"nfts": [{"identifier": "1", "name": "A B"},
                      {"identifier": "x", "name": "bad id"}], "next": "c2"},
        "c2": {"nfts": [{"identifier": "2", "name": "C D"}], "next": ""},
    }
    seen_urls = []

    def http_get(url, headers):
        assert headers["X-API-KEY"] == "k"
        assert headers["User-Agent"]          # measured: Cloudflare 1010 without
        seen_urls.append(url)
        cursor = url.split("&next=")[1] if "&next=" in url else ""
        return json.dumps(pages[cursor])

    lister = OpenSeaContractLister(http_get=http_get, api_key="k",
                                   contract="0xabc", platform="plat",
                                   chain="base")
    got = list(lister.items())
    assert [(i.token_id, i.name) for i in got] == [(1, "A B"), (2, "C D")]
    assert all(i.chain == "base" for i in got)
    assert "/chain/base/contract/0xabc/nfts" in seen_urls[0]
    assert len(seen_urls) == 2


def test_transport_failures_are_skipped_counted_and_fail_the_build_past_a_share():
    """Um blip de RPC/gateway salta o item (retry no refresh seguinte) e
    conta; uma falha sistemática (>2% E >=20) falha o BUILD — o snapshot
    anterior continua a servir — porque um pool 'honestamente mais pequeno'
    é o que o gate leria como estrato colapsado (06/09)."""
    from finding_memeland.target.sources import ChainUnavailable
    words = ["amber", "quiet", "salt", "velvet", "iron", "pale", "glass",
             "hollow", "silver", "wild"]
    names = {i: f"{words[i % 10]} {words[(i // 10) % 10]} meridian {i}"
             for i in range(1, 101)}                 # 100 bases distintas
    items = [item(i, names[i]) for i in range(1, 101)]
    metas = {i: meta_for(names[i]) for i in range(1, 101)}
    down = set()

    def fetch(ch, c, t):
        if t in down:
            raise ChainUnavailable("gateway 503")
        return token(metas[t])
    job = RefreshJob(listers=(FakeLister("plat", items),), fetch_token=fetch,
                     owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t")
    down.update({3, 7})
    snap, report = job.build(EPOCH)
    assert report.transport == 2 and snap.size() == 98
    down.update(range(10, 40))                       # 32 of 100 → outage
    with pytest.raises(RefreshFailed) as e:
        job.build(EPOCH)
    assert "transport" in str(e.value) and "previous" in str(e.value)


# --------------------------------------------------------------------------- #
# Content-addressed, twice (Opus 06/09) — tokenURI AND image                   #
# --------------------------------------------------------------------------- #


def test_plain_http_token_uri_is_refused_in_the_refresh():
    """Amarra os dois fixes um ao outro: a comparação por CID no live check
    só é completa porque ESTE filtro é mesmo aplicado — para conteúdo
    endereçado por conteúdo, CID igual é conteúdo igual. Se o filtro voltasse
    a ser decorativo, o live check ficava cego a metadata http mutável."""
    items = [item(1, "Quiet Meridian"), item(2, "Salt Harbor")]
    reads = {1: TokenRead("https://api.example.com/token/1", meta_for("Quiet Meridian")),
             2: TokenRead(URI, meta_for("Salt Harbor"))}
    job = RefreshJob(listers=(FakeLister("plat", items),),
                     fetch_token=lambda ch, c, t: reads[t],
                     owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t")
    snap, report = job.build(EPOCH)
    assert [e.token_id for e in snap.entries] == [2]
    assert report.not_content_addressed == 1
    assert snap.entries[0].token_uri == URI
    assert snap.entries[0].content_id == content_id(URI)


def test_mutable_image_is_refused_even_with_a_content_addressed_token_uri():
    """Gémeo do anterior, um nível abaixo (Opus 06/09): o compromisso sela o
    hash da metadata; uma metadata cujo `image` é https mutável deixa o dono
    trocar a obra com tokenURI e hash intactos. Não entra."""
    items = [item(1, "Quiet Meridian"), item(2, "Salt Harbor")]
    bad = dict(meta_for("Quiet Meridian"), image="https://cdn.example.com/1.png")
    reads = {1: TokenRead(URI, bad), 2: TokenRead(URI, meta_for("Salt Harbor"))}
    job = RefreshJob(listers=(FakeLister("plat", items),),
                     fetch_token=lambda ch, c, t: reads[t],
                     owner_is_eoa=lambda ch, c, t: True, now_iso=lambda: "t")
    snap, report = job.build(EPOCH)
    assert [e.token_id for e in snap.entries] == [2]
    assert report.not_content_addressed == 1


@pytest.mark.parametrize("uri,expect", [
    ("ipfs://QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN/metadata.json",
     "ipfs:QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN/metadata.json"),
    ("https://ipfs.pixura.io/ipfs/QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN",
     "ipfs:QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN"),
    ("https://other.gw/ipfs/QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN/",
     "ipfs:QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN"),
    ("ipfs://ipfs/QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN",
     "ipfs:QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN"),
    ("QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN",
     "ipfs:QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN"),
    ("https://api.example.com/token/1", None),
    ("", None),
])
def test_content_id_strips_transport_keeps_cid_and_path(uri, expect):
    """Migração de gateway com o mesmo CID ⇒ mesmo content id (a pixura)."""
    assert content_id(uri) == expect


def test_content_id_of_data_uris_is_their_own_hash():
    a = content_id("data:application/json;base64,e30=")
    b = content_id("data:application/json;base64,e30=")
    c = content_id("data:application/json;base64,eyJhIjoxfQ==")
    assert a == b and a != c and a.startswith("data:")

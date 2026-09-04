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
)
from finding_memeland.target.selector import CurationEpoch, metadata_hash

EPOCH = CurationEpoch(epoch_id="e1")


def item(i: int, name: str, platform: str = "plat") -> PlatformItem:
    return PlatformItem(platform=platform, contract=f"0x{i:040x}",
                        token_id=i, name=name)


def meta_for(name: str) -> dict:
    return {"name": name, "image": "ipfs://img", "description": "d"}


def make_job(items, *, meta=None, eoa=None, unique=None, listers=None):
    metas = meta or {}
    return RefreshJob(
        listers=listers or (FakeLister("plat", items),),
        fetch_metadata=lambda c, t: metas.get(t, meta_for("Salt Harbor")),
        owner_is_eoa=lambda c, t: (eoa or {}).get(t, True),
        name_is_unique=lambda n, c, t: (unique or {}).get(t, True),
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
    # 1: no image; 2: contract owner; 3: uniqueness unverifiable; 4: clean
    metas = {1: {"name": names[1]}, 2: meta_for(names[2]),
             3: meta_for(names[3]), 4: meta_for(names[4])}
    snap, report = make_job(items, meta=metas, eoa={2: False},
                            unique={3: None}).build(EPOCH)
    assert report.pool_size == 1
    assert snap.entries[0].token_id == 4
    assert report.unverifiable == 1


def test_quota_priced_uniqueness_runs_last():
    """A candidate killed by cheap filters must not spend a marketplace call."""
    calls = []

    def unique(n, c, t):
        calls.append(t)
        return True

    items = [item(1, "Punk #1"), item(2, "Quiet Meridian")]
    job = RefreshJob(
        listers=(FakeLister("plat", items),),
        fetch_metadata=lambda c, t: meta_for("Quiet Meridian"),
        owner_is_eoa=lambda c, t: True,
        name_is_unique=unique,
        now_iso=lambda: "t",
    )
    job.build(EPOCH)
    assert calls == [2]


def test_unlistable_platform_fails_the_build_not_silently():
    job = make_job([], listers=(FakeLister("dead", [], raises=True),))
    with pytest.raises(RefreshFailed) as e:
        job.build(EPOCH)
    assert "previous" in str(e.value)


def test_opensea_lister_paginates_and_parses():
    pages = {
        "": {"nfts": [{"identifier": "1", "name": "A B"},
                      {"identifier": "x", "name": "bad id"}], "next": "c2"},
        "c2": {"nfts": [{"identifier": "2", "name": "C D"}], "next": ""},
    }
    seen_urls = []

    def http_get(url, headers):
        assert headers["X-API-KEY"] == "k"
        seen_urls.append(url)
        cursor = url.split("&next=")[1] if "&next=" in url else ""
        return json.dumps(pages[cursor])

    lister = OpenSeaContractLister(http_get=http_get, api_key="k",
                                   contract="0xabc", platform="plat")
    got = list(lister.items())
    assert [(i.token_id, i.name) for i in got] == [(1, "A B"), (2, "C D")]
    assert "/chain/base/contract/0xabc/nfts" in seen_urls[0]
    assert len(seen_urls) == 2

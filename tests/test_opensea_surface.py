"""OpenSea as the marketplace surface of the target game (10/09/2026).

Why: Rarible's public plans are Free = 100 requests a MONTH or Enterprise by
contact; a target hunt spends 40-200 (search guard 2 per clue attempt,
uniqueness 8 per draw batch, chain probe 6 per chainless link). The three
roles are ports; this file pins the OpenSea adapters to the RAW captures of
scripts/capturar_target.py `opensea` (Pedro's Mac, 2026-09-10, approved key,
120 requests / 60 s window) and the wiring choice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finding_memeland.target.adapters import OpenSeaChainProbe
from finding_memeland.target.search_guard import (
    OPENSEA_CHAIN,
    ClueSearchGuard,
    MarketNameUniqueness,
    OpenSeaSearch,
)

FIX = Path(__file__).parent / "fixtures" / "target"
C = "0x3b3ee1931dc30c1957379fac9aba94d1c48a5405"          # Foundation (FND)
TARGET = f"ETHEREUM:{C}:1".upper()                          # FND #1, canonical


def fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


def _text(doc: dict) -> str:
    body = doc["body"]
    return body if isinstance(body, str) else json.dumps(body)


class HttpErr(Exception):
    def __init__(self, code: int, text: str):
        super().__init__(text[:80])
        self.code = code


def replay_search():
    """http_get that answers /search from the three captures by the
    presence of the chain filter and the query, like the live index did."""
    calls: list[str] = []

    def get(url, headers):
        calls.append(url)
        assert headers["X-API-KEY"] == "k"
        assert "asset_types=nft" in url and "limit=50" in url
        if "calendars" in url:
            doc = fixture("opensea_search_clue")
        elif "chains=ethereum" in url:
            doc = fixture("opensea_search_ethereum")
        else:
            doc = fixture("opensea_search_all")
        return _text(doc)
    get.calls = calls
    return get


def replay_items():
    def get(url, headers):
        for slug in ("ethereum", "base", "matic", "arbitrum", "optimism", "zora"):
            if f"/chain/{slug}/" in url:
                doc = fixture(f"opensea_item_{slug}")
                break
        else:
            doc = fixture("opensea_item_bad_chain")
        status = int(doc["_meta"]["status"])
        if status >= 400:
            raise HttpErr(status, _text(doc))
        return _text(doc)
    return get


# ------------------------------- captures ---------------------------------- #


def test_captures_present_and_clean():
    for n in ("search_ethereum", "search_all", "search_clue", "item_ethereum",
              "item_base", "item_bad_chain"):
        doc = fixture(f"opensea_{n}")
        assert "api-key" not in json.dumps(doc).lower()
        assert doc["_meta"]["rate"]["x-ratelimit-limit"] == "120"


def test_search_filtered_by_chain_surfaces_the_target_with_its_chain():
    """The canary of the search guard, offline: FND #1 is among the
    results for its own name on Ethereum, with the id in the game's
    format — the chain read from opensea_url, not configured."""
    s = OpenSeaSearch(http_get=replay_search(), api_key="k")
    ids = s.item_ids("Ancient Future", chain="ETHEREUM")
    assert TARGET in ids
    assert all(i.startswith("ETHEREUM:0X") for i in ids)
    assert len(ids) == 50


def test_search_with_a_clue_sentence_is_empty():
    s = OpenSeaSearch(http_get=replay_search(), api_key="k")
    assert s.item_ids("where calendars run out of pages", chain="ETHEREUM") == set()


def test_unfiltered_search_returns_names_across_chains():
    """The uniqueness view: (id, name) rows on several chains — and the
    measured fact that 'Ancient Future' is NOT unique (8 bearers on
    Ethereum alone, 7 inside the Foundation contract)."""
    s = OpenSeaSearch(http_get=replay_search(), api_key="k")
    rows = s.named_items("Ancient Future")
    ids = {i for i, _ in rows}
    assert TARGET in ids
    chains = {i.split(":")[0] for i in ids}
    assert {"ETHEREUM", "POLYGON", "SONEIUM"} <= chains   # result URLs say 'polygon'
    assert "UNKNOWN" not in chains                    # every capture row placed
    exact = [i for i, n in rows if n.strip().casefold() == "ancient future"]
    assert len(exact) >= 8 and TARGET in exact


def test_guard_and_uniqueness_run_end_to_end_on_the_captures():
    get = replay_search()
    s = OpenSeaSearch(http_get=get, api_key="k")
    guard = ClueSearchGuard(search=s, retries=0)
    v = guard.check("where calendars run out of pages", target_item_id=TARGET,
                    target_name_onchain="Ancient Future")
    assert v.ok is True and v.found is False
    assert len(get.calls) == 2                        # canary + clue
    # a clue that IS the name surfaces the target → rejected
    v = guard.check("Ancient Future", target_item_id=TARGET,
                    target_name_onchain="Ancient Future")
    assert v.ok is False and v.found is True
    u = MarketNameUniqueness(search=s, page_size=50)
    assert u("Ancient Future", "ethereum", C, 1) is False   # measured: not unique
    assert u.stats["not_unique"] == 1


def test_unknown_chain_is_unverifiable_not_absent():
    s = OpenSeaSearch(http_get=replay_search(), api_key="k")
    with pytest.raises(ValueError):
        s.item_ids("x", chain="SOLANA")
    guard = ClueSearchGuard(search=s, retries=0, sleep_s=0)
    v = guard.check("x", target_item_id=f"SOLANA:{C}:1", target_name_onchain="n")
    assert v.ok is False and v.found is None


def test_garbled_or_shapeless_response_raises_for_the_guard():
    s = OpenSeaSearch(http_get=lambda u, h: '{"nope": []}', api_key="k")
    with pytest.raises(ValueError):
        s.item_ids("x", chain="ETHEREUM")
    s = OpenSeaSearch(http_get=lambda u, h: "<html>", api_key="k")
    with pytest.raises(ValueError):
        s.named_items("x")


def test_polygon_maps_to_matic_and_back():
    assert OPENSEA_CHAIN["polygon"] == "matic"
    seen = {}

    def get(url, headers):
        seen["url"] = url
        return json.dumps({"results": [{"type": "nft", "nft": {
            "identifier": "7", "contract": C, "name": "n",
            "opensea_url": f"https://opensea.io/assets/matic/{C}/7"}}]})
    s = OpenSeaSearch(http_get=get, api_key="k")
    assert s.item_ids("n", chain="POLYGON") == {f"POLYGON:{C}:7".upper()}
    assert "chains=matic" in seen["url"]


def test_no_key_refuses_at_construction():
    with pytest.raises(ValueError):
        OpenSeaSearch(http_get=lambda u, h: "", api_key="")
    with pytest.raises(ValueError):
        OpenSeaChainProbe(http_get=lambda u, h: "", api_key="")


# ------------------------------ chain probe --------------------------------- #


def test_chain_probe_finds_ethereum_from_the_captures():
    """200 on ethereum with nft.contract/identifier echoing the ask; 404
    'Item with identifier 1 not found' on the other five → exactly one hit."""
    probe = OpenSeaChainProbe(http_get=replay_items(), api_key="k")
    assert probe(C, 1) == "ethereum"
    assert fixture("opensea_item_base")["_meta"]["status"] == 404
    assert "not found" in fixture("opensea_item_base")["body"]["errors"][0]


def test_chain_probe_treats_400_unrecognized_chain_as_ambiguous():
    doc = fixture("opensea_item_bad_chain")
    assert doc["_meta"]["status"] == 400 and "Unrecognized chain" in doc["body"]["errors"][0]

    def get(url, headers):
        raise HttpErr(400, _text(doc))
    assert OpenSeaChainProbe(http_get=get, api_key="k")(C, 1) is None


def test_chain_probe_rate_limited_is_ambiguous_never_a_miss():
    def get(url, headers):
        if "/chain/ethereum/" in url:
            return _text(fixture("opensea_item_ethereum"))
        raise HttpErr(429, "Too Many Requests")
    assert OpenSeaChainProbe(http_get=get, api_key="k")(C, 1) is None


def test_chain_probe_echo_mismatch_is_not_a_hit():
    def get(url, headers):
        if "/chain/ethereum/" in url:
            return json.dumps({"nft": {"contract": C, "identifier": "2"}})
        raise HttpErr(404, "not found")
    assert OpenSeaChainProbe(http_get=get, api_key="k")(C, 1) is None


# -------------------------------- wiring ----------------------------------- #


def test_wiring_prefers_opensea_and_accepts_rarible_alone():
    from finding_memeland.target.adapters import OpenSeaChainProbe as OSP
    from finding_memeland.target.adapters import RaribleChainProbe as RCP
    from test_target_wiring import build, settings

    w = build(settings(opensea_api_key="ok", rarible_api_key="rk"))
    assert w.market_surface == "opensea"
    assert isinstance(w.ports.resolve_link._probe, OSP)
    w = build(settings(opensea_api_key="", rarible_api_key="rk"))
    assert w.market_surface == "rarible"
    assert isinstance(w.ports.resolve_link._probe, RCP)


def test_config_requires_one_surface_not_rarible_by_name():
    from test_target_wiring import settings

    s = settings(opensea_api_key="", rarible_api_key="")
    missing = " ".join(s.target_missing())
    assert "opensea_api_key or rarible_api_key" in missing
    assert not settings(opensea_api_key="ok", rarible_api_key="").target_missing()
    assert not settings(opensea_api_key="", rarible_api_key="rk").target_missing()

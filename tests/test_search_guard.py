"""Search guard — "no puzzle-phase clue may be a search", mechanically."""

from __future__ import annotations

import json

from finding_memeland.target.search_guard import (
    ClueSearchGuard,
    FakeSearch,
    RaribleSearch,
    SearchGuardVerdict,
)

TARGET = "BASE:0xaaa:7"


def guard(**kw):
    kw.setdefault("retries", 0)
    kw.setdefault("sleep_s", 0.0)
    return ClueSearchGuard(**kw)


def test_clue_that_surfaces_target_is_rejected():
    g = guard(search=FakeSearch({"salt harbor": {TARGET, "BASE:0xbbb:1"}}))
    v = g.check("It rests where the salt harbor keeps its oldest light",
                target_item_id=TARGET)
    assert v == SearchGuardVerdict(ok=False, found=True, detail=v.detail)
    assert "rejected" in v.detail


def test_oblique_clue_passes():
    g = guard(search=FakeSearch({"salt harbor": {TARGET}}))
    v = g.check("A mineral the sea leaves behind, guarding ships",
                target_item_id=TARGET)
    assert v.ok and v.found is False


def test_target_id_match_is_case_insensitive():
    g = guard(search=FakeSearch({"glow": {"base:0xAAA:7"}}))
    v = g.check("a healing glow", target_item_id=TARGET)
    assert not v.ok and v.found is True


def test_unverifiable_fails_closed():
    g = guard(search=FakeSearch(raises=True))
    v = g.check("any clue", target_item_id=TARGET)
    assert not v.ok
    assert v.found is None
    assert "fail-closed" in v.detail


def test_verdict_detail_never_carries_the_clue_or_target_name():
    g = guard(search=FakeSearch({"salt harbor": {TARGET}}))
    for clue in ("salt harbor shining", "a mineral the sea leaves"):
        v = g.check(clue, target_item_id=TARGET)
        assert "salt" not in v.detail.lower()
        assert clue not in v.detail


def test_rarible_search_request_shape_and_parse():
    """The measured shape (2026-08-25): X-API-KEY header, fullText filter."""
    seen = {}

    def http_post(url, body, headers):
        seen["url"], seen["body"], seen["headers"] = url, body, headers
        return json.dumps({"items": [{"id": "BASE:0xaaa:7"},
                                     {"id": "BASE:0xbbb:1"}]})

    s = RaribleSearch(http_post=http_post, api_key="k")
    ids = s.item_ids("some clue text")
    assert ids == {"BASE:0xaaa:7", "BASE:0xbbb:1"}
    assert seen["url"].endswith("/items/search")
    assert seen["headers"]["X-API-KEY"] == "k"
    body = json.loads(seen["body"])
    assert body["filter"]["fullText"]["text"] == "some clue text"
    assert body["filter"]["blockchains"] == ["BASE"]


def test_rarible_search_refuses_empty_key():
    try:
        RaribleSearch(http_post=lambda *a: "", api_key="")
    except ValueError as e:
        assert "api key" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")

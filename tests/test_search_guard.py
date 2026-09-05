"""Search guard — "no puzzle-phase clue may be a search", mechanically —
with the canary (Opus re-review 05/09, P0-4): a guard that cannot see its
own target approves nothing."""

from __future__ import annotations

import json

from finding_memeland.target.search_guard import (
    ClueSearchGuard,
    FakeSearch,
    RaribleSearch,
    SearchGuardVerdict,
)

TARGET = "ETHEREUM:0xaaa:7"
NAME = "Salt Harbor #7"


def guard(**kw):
    kw.setdefault("retries", 0)
    kw.setdefault("sleep_s", 0.0)
    return ClueSearchGuard(**kw)


def check(g, clue, *, target=TARGET, name=NAME):
    return g.check(clue, target_item_id=target, target_name_onchain=name)


def test_clue_that_surfaces_target_is_rejected():
    g = guard(search=FakeSearch({"salt harbor": {TARGET, "ETHEREUM:0xbbb:1"}}))
    v = check(g, "It rests where the salt harbor keeps its oldest light")
    assert v == SearchGuardVerdict(ok=False, found=True, detail=v.detail)
    assert "rejected" in v.detail


def test_oblique_clue_passes_after_canary():
    g = guard(search=FakeSearch({"salt harbor": {TARGET}}))
    v = check(g, "A mineral the sea leaves behind, guarding ships")
    assert v.ok and v.found is False
    assert "canary ok" in v.detail


def test_target_id_match_is_case_insensitive():
    g = guard(search=FakeSearch({"glow": {"ethereum:0xAAA:7"}}))
    v = check(g, "a healing glow", name="Healing Glow")
    assert not v.ok and v.found is True


def test_unverifiable_fails_closed():
    g = guard(search=FakeSearch(raises=True))
    v = check(g, "any clue")
    assert not v.ok
    assert v.found is None
    assert "fail-closed" in v.detail


# --------------------------------------------------------------------------- #
# P0-4 — chain from the target, and the canary                                 #
# --------------------------------------------------------------------------- #


def test_chain_is_derived_from_target_not_configured():
    fake = FakeSearch({"salt harbor": {TARGET}})
    g = guard(search=fake)
    check(g, "a quiet clue")
    assert fake.queries and all(ch == "ETHEREUM" for _, ch in fake.queries)


def test_blind_index_refuses_instead_of_approving():
    """The P0-4 failure, reproduced: the index only knows BASE items, the
    target is on Ethereum. Old guard: 'absent from N results' -> ok=True
    for a clue quoting the NAME verbatim. New guard: canary fails -> refuse."""
    base_only = FakeSearch({"salt harbor": {"BASE:0xccc:9", "BASE:0xddd:2"}})
    g = guard(search=base_only)
    v = check(g, "the salt harbor keeps its light")       # cita o nome!
    assert not v.ok
    assert v.found is None
    assert "canary" in v.detail and "blind" in v.detail


def test_canary_runs_before_the_clue_and_costs_one_query():
    fake = FakeSearch({"salt harbor": {TARGET}})
    g = guard(search=fake)
    check(g, "an oblique clue")
    assert [q for q, _ in fake.queries] == [NAME, "an oblique clue"]


def test_missing_name_or_chainless_id_fails_closed():
    g = guard(search=FakeSearch({"salt harbor": {TARGET}}))
    assert not check(g, "clue", name="").ok
    assert not check(g, "clue", target="0xaaa:7").ok       # sem cadeia


def test_verdict_detail_never_carries_the_clue_target_name_or_chain():
    g = guard(search=FakeSearch({"salt harbor": {TARGET}}))
    for clue in ("salt harbor shining", "a mineral the sea leaves"):
        v = check(g, clue)
        low = v.detail.lower()
        assert "salt" not in low and clue not in v.detail
        assert "ethereum" not in low and "0xaaa" not in low
    v = check(guard(search=FakeSearch()), "x")               # canário cego
    assert "salt" not in v.detail.lower() and "ethereum" not in v.detail.lower()


# --------------------------------------------------------------------------- #
# Real adapter shape                                                           #
# --------------------------------------------------------------------------- #


def test_rarible_search_request_shape_and_parse():
    """The measured shape (2026-08-25): X-API-KEY header, fullText filter;
    the blockchains filter is the per-call chain, upper-cased."""
    seen = {}

    def http_post(url, body, headers):
        seen["url"], seen["body"], seen["headers"] = url, body, headers
        return json.dumps({"items": [{"id": "ETHEREUM:0xaaa:7"},
                                     {"id": "ETHEREUM:0xbbb:1"}]})

    s = RaribleSearch(http_post=http_post, api_key="k")
    ids = s.item_ids("some clue text", chain="ethereum")
    assert ids == {"ETHEREUM:0xaaa:7", "ETHEREUM:0xbbb:1"}
    assert seen["url"].endswith("/items/search")
    assert seen["headers"]["X-API-KEY"] == "k"
    body = json.loads(seen["body"])
    assert body["filter"]["fullText"]["text"] == "some clue text"
    assert body["filter"]["blockchains"] == ["ETHEREUM"]


def test_rarible_search_has_no_chain_of_its_own():
    import inspect
    assert "chain" not in inspect.signature(RaribleSearch.__init__).parameters


def test_rarible_search_refuses_empty_key():
    try:
        RaribleSearch(http_post=lambda *a: "", api_key="")
    except ValueError as e:
        assert "api key" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")

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
# R2 — name uniqueness proves it can see the target before saying "unique"     #
# --------------------------------------------------------------------------- #


def uniq(hits, **kw):
    from finding_memeland.target.search_guard import (
        FakeNameSearch, MarketNameUniqueness)
    kw.setdefault("retries", 0)
    kw.setdefault("sleep_s", 0.0)
    kw.setdefault("page_size", 25)
    return MarketNameUniqueness(search=FakeNameSearch(hits), **kw)


def test_uniqueness_true_when_only_the_target_bears_the_base_name():
    u = uniq({"salt harbor": [(TARGET, "Salt Harbor #7"),
                              ("ETHEREUM:0xbbb:1", "Salt Harbor Lights")]})
    assert u("Salt Harbor", "ethereum", "0xaaa", 7) is True


def test_uniqueness_false_when_another_item_shares_the_base_name():
    u = uniq({"salt harbor": [(TARGET, "Salt Harbor #7"),
                              ("ETHEREUM:0xbbb:1", "salt harbor #12")]})
    assert u("Salt Harbor", "ethereum", "0xaaa", 7) is False


def test_uniqueness_canary_never_true_on_blind_index():
    """Índice que não vê o alvo devolve vazio — sem canário isto era
    'único'. Com canário: None, contado como blind."""
    u = uniq({"salt harbor": [("BASE:0xccc:9", "Salt Harbor Lights")]})
    assert u("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert u.stats["blind"] == 1
    assert uniq({})("Salt Harbor", "ethereum", "0xaaa", 7) is None


def test_uniqueness_is_the_hunters_view_across_chains():
    """A vista do caçador NÃO filtra por cadeia (medido 05/09: a pesquisa
    devolve multi-chain; um homónimo em Solana/Base é um homónimo que o
    caçador encontra e submete). Um filtro à cadeia do alvo tornava o
    gémeo invisível e aprovava por engano — o P0-4 um andar ao lado."""
    u = uniq({"salt harbor": [(TARGET, "Salt Harbor #7"),
                              ("BASE:0xccc:9", "Salt Harbor #9")]})
    assert u("Salt Harbor", "ethereum", "0xaaa", 7) is False
    assert u.stats["not_unique"] == 1


def test_uniqueness_crowded_page_is_counted_separately():
    """Alvo fora do top-N porque o nome tem mais bearers do que a página:
    None (conservador), mas contado como crowded, não como cegueira."""
    rows = [(f"ETHEREUM:0xbbb:{i}", f"Salt Harbor #{i}") for i in range(25)]
    u = uniq({"salt harbor": rows}, page_size=25)
    assert u("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert u.stats["crowded"] == 1 and u.stats["blind"] == 0


def test_uniqueness_transport_failure_is_none():
    from finding_memeland.target.search_guard import (
        FakeNameSearch, MarketNameUniqueness)
    u = MarketNameUniqueness(search=FakeNameSearch(raises=True),
                             page_size=25, retries=0, sleep_s=0.0)
    assert u("Salt Harbor", "ethereum", "0xaaa", 7) is None
    assert u.stats["transport"] == 1


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

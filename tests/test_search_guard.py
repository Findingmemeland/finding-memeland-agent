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


def check(g, clue="a clue about nothing in particular", *,
          target=TARGET, name=NAME):
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


def test_a_failed_canary_is_flagged_blind_and_a_dead_market_is_not():
    """Both come back found=None, and until 17/09 nothing could tell them
    apart — so a piece the index cannot see was retried as if the
    marketplace were merely down. `blind` separates them: the canary is
    about the TARGET, the transport failure is about US."""
    v = check(guard(search=FakeSearch({})))          # canary finds nothing
    assert v.ok is False and v.found is None and v.blind is True
    assert "canary failed" in v.detail

    v2 = check(guard(search=_Boom()))
    assert v2.ok is False and v2.found is None and v2.blind is False
    assert "unverifiable" in v2.detail

    v3 = check(guard(search=FakeSearch({"salt harbor": {TARGET}})),
               "A mineral the sea leaves behind")
    assert v3.ok is True and v3.blind is False


class _Boom:
    def item_ids(self, text, *, chain):
        raise TimeoutError("marketplace down")


# --------------------------------------------------------------------------- #
# The 100-character cap (measured 17/09) — the bug that silenced the guard    #
# --------------------------------------------------------------------------- #

CLUE = ("It sits where the water gives up its salt and the light gives up its "
        "watch, a place named twice over for what it keeps and what it cannot "
        "hold, and the second word is the one that remembers the first")


class _Capped:
    """A marketplace that refuses any query over 100 characters — which is
    what OpenSea actually does: HTTP 400, {"errors": ["Query must not
    exceed 100 characters"]}. Every clue is longer than that."""

    max_query_chars = 100

    def __init__(self, hits=None):
        self.hits = hits or {}
        self.queries: list[str] = []

    def item_ids(self, text, *, chain):
        if self.max_query_chars and len(text) > self.max_query_chars:
            raise ValueError("HTTP Error 400: Query must not exceed 100 characters")
        self.queries.append(text)
        return {i for phrase, ids in self.hits.items()
                if phrase in text.lower() for i in ids}


def test_a_long_clue_is_tested_whole_instead_of_400ing():
    """THE BUG: OpenSea caps a query at 100 chars, every clue is longer,
    so every clue-phase check 400ed and read as "could not verify". The
    guard has been fail-closed — holding hunts — since OpenSea became the
    surface on 10/09. The canary never showed it: a piece NAME is two or
    three words and always fit."""
    m = _Capped({"salt harbor": {TARGET}})
    v = check(guard(search=m), CLUE)
    assert v.ok is True and v.found is False
    assert all(len(q) <= 100 for q in m.queries)
    assert len(m.queries) > 2, "the clue went out in one piece — it cannot have"


def test_every_word_of_the_clue_reaches_the_marketplace():
    """Truncating to 100 would have been one line and a silent weakening:
    the untested tail is exactly where a writer puts the literal
    description of the picture."""
    m = _Capped({"salt harbor": {TARGET}})
    check(guard(search=m), CLUE)
    sent = {w for q in m.queries[1:] for w in q.split()}   # [0] is the canary
    assert set(CLUE.split()) <= sent


def test_the_target_surfacing_in_ANY_window_rejects_the_clue():
    """A phrase that only appears in the tail must still reject. Otherwise
    the windowing would be the truncation it replaced."""
    tail = "remembers the first"
    m = _Capped({"salt harbor": {TARGET}, tail: {TARGET}})
    v = check(guard(search=m), CLUE)
    assert v.ok is False and v.found is True


def test_windows_overlap_so_a_phrase_across_a_cut_is_still_tested():
    """A cut between "the keeper's" and "last light" would let the phrase
    through untested — and a phrase is exactly what an index matches."""
    from finding_memeland.target.search_guard import _windows
    ws = _windows(CLUE, 100)
    assert all(len(w) <= 100 for w in ws)
    for a, b in zip(ws, ws[1:]):
        assert set(a.split()) & set(b.split()), "windows do not overlap"


def test_a_surface_without_a_cap_still_sends_the_clue_in_one_piece():
    """Rarible has no measured cap. The windowing is the marketplace's
    limit, not our policy — where there is no limit there is no splitting,
    and the guard tests the exact artefact the public would see."""
    m = _Capped({"salt harbor": {TARGET}})
    m.max_query_chars = 0
    check(guard(search=m), CLUE)
    assert CLUE in m.queries


def test_windows_handle_the_degenerate_shapes():
    from finding_memeland.target.search_guard import _windows
    assert _windows("", 100) == []
    assert _windows("A short name", 100) == ["A short name"]
    assert [len(w) for w in _windows("x" * 250, 100)] == [100]

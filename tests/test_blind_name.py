"""O índice cego "indexada-sem-marca", pelo nome (01/10). Só medição.

A 30/09, os 5 candidatos cegos de Base eram todos peças que o OpenSea conhece
e não marca — e que a pesquisa pelo nome não devolve. Duas explicações que
pedem respostas opostas:
  · o nome que pesquisamos não é o que o OpenSea tem para a peça (ou ele não
    tem nome nenhum) — o problema é a NOSSA pesquisa;
  · é o mesmo nome — é a pesquisa do OpenSea que não mostra a peça, e um
    jogador que procure o nome também não a encontra.
A guarda continua a devolver None; o nome nunca sai dela — só a contagem.
"""
from __future__ import annotations

import json

import pytest
from test_measure_owner_and_blind import NFT, _deposit, _probe_answering, _Search
from test_opensea_surface import HttpErr, _text, fixture

from finding_memeland.target.search_guard import MarketNameUniqueness


@pytest.mark.parametrize("seen, key", [
    (("clean", "Two Words"), "blind_clean_same"),
    (("clean", "two words"), "blind_clean_same"),               # case does not matter
    (("clean", "Something Unrelated"), "blind_clean_other"),
    (("clean", None), "blind_clean_noname"),
])
def test_a_clean_blind_item_is_split_by_the_name_opensea_holds(seen, key):
    guard = MarketNameUniqueness(search=_Search(), page_size=50,
                                 item_status=lambda c, k, t: seen)
    assert guard("Two Words", "base", NFT, 7) is None           # the answer is unchanged
    assert guard.stats["blind"] == 1 and guard.stats["blind_clean"] == 1
    assert guard.stats[key] == 1
    assert sum(guard.stats[k] for k in ("blind_clean_same", "blind_clean_other",
                                        "blind_clean_noname")) == 1


@pytest.mark.parametrize("seen, key", [
    (("missing", None), "blind_unindexed"), (("flagged", "Two Words"), "blind_flagged"),
    (None, "blind_unknown"),
])
def test_the_other_kinds_are_not_split_by_name(seen, key):
    guard = MarketNameUniqueness(search=_Search(), page_size=50,
                                 item_status=lambda c, k, t: seen)
    guard("Two Words", "base", NFT, 7)
    assert guard.stats[key] == 1
    assert not any(guard.stats[k] for k in ("blind_clean_same", "blind_clean_other",
                                            "blind_clean_noname"))


def test_a_lookup_that_gives_no_name_information_is_as_before():
    """Um `item_status` que só devolve a palavra (sem o nome) não é dividido."""
    guard = MarketNameUniqueness(search=_Search(), page_size=50,
                                 item_status=lambda c, k, t: "clean")
    guard("Two Words", "base", NFT, 7)
    assert guard.stats["blind_clean"] == 1
    assert not any(guard.stats[k] for k in ("blind_clean_same", "blind_clean_other",
                                            "blind_clean_noname"))


@pytest.mark.parametrize("seen, label", [
    (("clean", "Two Words"), "índice-cego:indexada-sem-marca:mesmo-nome 1"),
    (("clean", "Other Name"), "índice-cego:indexada-sem-marca:nome-diferente 1"),
    (("clean", None), "índice-cego:indexada-sem-marca:sem-nome 1"),
])
def test_the_deposit_names_the_split_and_never_the_name(seen, label):
    guard = MarketNameUniqueness(search=_Search(), page_size=50,
                                 item_status=lambda c, k, t: seen)
    out = _deposit(name_is_unique=guard)
    assert f"único 1 ({label})" in out, out
    assert "Other Name" not in out and "Two Words" not in out


def test_opensea_item_seen_returns_the_name_it_holds():
    item = fixture("opensea_item_ethereum")
    nft = json.loads(_text(item))["nft"]
    probe = _probe_answering(_text(item))
    status, name = probe.item_seen("ethereum", nft["contract"], int(nft["identifier"]))
    assert status == "clean" and name == nft["name"]
    # item_status is the same answer without the name
    assert probe.item_status("ethereum", nft["contract"], int(nft["identifier"])) == "clean"


def test_opensea_item_seen_without_a_name_and_on_a_miss():
    item = json.loads(_text(fixture("opensea_item_ethereum")))
    item["nft"] = {**item["nft"], "name": None}
    nft = item["nft"]
    probe = _probe_answering(json.dumps(item))
    assert probe.item_seen("ethereum", nft["contract"], int(nft["identifier"])) == ("clean", None)
    assert _probe_answering(HttpErr(404, "x")).item_seen("base", NFT, 7) == ("missing", None)
    assert _probe_answering(HttpErr(500, "x")).item_seen("base", NFT, 7) is None


def test_production_gives_the_guard_the_lookup_with_the_name():
    from test_target_wiring import FakeRepo, rpc_ok, settings

    from finding_memeland.target.wiring import build_target
    w = build_target(settings(opensea_api_key="ok"), anthropic=object(), repo=FakeRepo(),
                     http_get=lambda u, h: "{}", http_post=rpc_ok,
                     http_get_bytes=lambda u, h: b"")
    lookup = w.finder._name_is_unique._item_status                  # noqa: SLF001
    assert lookup.__name__ == "item_seen"

"""Target selector (Option A) — selection logic, offline with fakes."""

from __future__ import annotations

import pytest

from finding_memeland.target.selector import (
    CurationEpoch,
    FakeSource,
    SelectionRefused,
    Target,
    TargetSelector,
    metadata_hash,
    name_qualifies,
    normalize_name,
)

EPOCH = CurationEpoch(epoch_id="e1")


# --------------------------------------------------------------------------- #
# Base-name normalisation (frozen with the commitment protocol)                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw,base", [
    ("Tiny Punk #9278", "Tiny Punk"),
    ("healing lucid glow 12684", "healing lucid glow"),
    ("we stand, we build #27132", "we stand, we build"),
    ("Base on Paragraph #286711", "Base on Paragraph"),
    ("UnclePump No. 7", "UnclePump"),
    ("name #1 #2", "name"),                 # serials strip repeatedly
    ("Area 51 Dreamer", "Area 51 Dreamer"),  # interior number untouched
    ("42", "42"),                            # stripping everything keeps original
])
def test_normalize_name(raw, base):
    assert normalize_name(raw) == base


def test_name_qualifies_counts_real_words_only():
    assert name_qualifies("healing lucid glow")
    assert not name_qualifies("Token")        # serial gone, one word left
    assert not name_qualifies("X 9")          # no 2+ letter words


def test_metadata_hash_is_canonical_and_order_independent():
    a = {"name": "Salt Harbor", "image": "ipfs://x", "attributes": [1, 2]}
    b = {"image": "ipfs://x", "attributes": [1, 2], "name": "Salt Harbor"}
    assert metadata_hash(a) == metadata_hash(b)
    assert metadata_hash(a) != metadata_hash({**a, "name": "Salt Harbour"})


# --------------------------------------------------------------------------- #
# Selection                                                                    #
# --------------------------------------------------------------------------- #

GOOD_META = {"name": "Whispering Harbor", "image": "ipfs://img",
             "description": "a quiet place"}


CHAIN = "ethereum"


def make_selector(pairs, *, meta=None, eoa=None, unique=None, **kw):
    """Os testes falam em pares (contract, token); a cadeia é EXPLÍCITA
    aqui — o FakeSource já não carimba nenhuma por omissão."""
    meta = meta if meta is not None else {p: GOOD_META for p in pairs}
    eoa = eoa if eoa is not None else {p: True for p in pairs}
    unique = unique if unique is not None else {p: True for p in pairs}
    return TargetSelector(
        source=FakeSource([(CHAIN, c, t) for c, t in pairs]),
        fetch_metadata=lambda ch, c, t: meta.get((c, t)),
        owner_is_eoa=lambda ch, c, t: eoa.get((c, t)),
        name_is_unique=lambda n, ch, c, t: unique.get((c, t)),
        **kw,
    )


def test_selects_first_qualifier_and_builds_target():
    pairs = [("0xAAA", 1), ("0xBBB", 2)]
    target = make_selector(pairs).select(EPOCH)
    assert isinstance(target, Target)
    assert target.contract == "0xaaa"           # lower-cased
    assert target.id() == "ethereum:0xaaa:1"    # cadeia do candidato
    assert target.name == "Whispering Harbor"
    assert target.epoch == "e1"
    assert target.metadata_sha256 == metadata_hash(GOOD_META)


def test_chain_comes_from_the_candidate_not_a_constant():
    """P0-1 (revisão Opus 05/09): a cadeia é dado por candidato."""
    triples = [("ethereum", "0xAAA", 1)]
    meta = {("0xAAA", 1): GOOD_META}
    sel = TargetSelector(
        source=FakeSource(triples),
        fetch_metadata=lambda ch, c, t: meta.get((c, t)),
        owner_is_eoa=lambda ch, c, t: True,
        name_is_unique=lambda n, ch, c, t: True,
    )
    target = sel.select(EPOCH)
    assert target.chain == "ethereum"
    assert target.id() == "ethereum:0xaaa:1"


def test_fake_source_refuses_pairs():
    """Sem forma legada: um fake que carimbasse a cadeia esconderia
    regressões (revisão Opus 05/09)."""
    with pytest.raises(ValueError):
        FakeSource([("0xAAA", 1)])


def test_serial_name_is_normalised_and_kept_onchain():
    pairs = [("0xAAA", 7)]
    meta = {pairs[0]: {"name": "Whispering Harbor #7", "image": "i"}}
    target = make_selector(pairs, meta=meta).select(EPOCH)
    assert target.name == "Whispering Harbor"
    assert target.name_onchain == "Whispering Harbor #7"


@pytest.mark.parametrize("kill", ["meta", "image", "name", "eoa", "unique"])
def test_each_hard_filter_rejects(kill):
    bad, good = ("0xBAD", 1), ("0xGOOD", 2)
    pairs = [bad, good]
    meta = {bad: dict(GOOD_META), good: GOOD_META}
    eoa = {bad: True, good: True}
    unique = {bad: True, good: True}
    if kill == "meta":
        meta[bad] = None
    elif kill == "image":
        meta[bad] = {"name": "Whispering Harbor"}
    elif kill == "name":
        meta[bad] = {"name": "Punk #1", "image": "i"}   # base 'Punk': 1 word
    elif kill == "eoa":
        eoa[bad] = False
    elif kill == "unique":
        unique[bad] = False
    target = make_selector(pairs, meta=meta, eoa=eoa, unique=unique).select(EPOCH)
    assert target.contract == "0xgood"


@pytest.mark.parametrize("field", ["eoa", "unique"])
def test_indeterminate_rejects_fail_closed(field):
    """None (RPC/API unverifiable) rejects in production — never pass-with-
    warning like the measurement scripts."""
    pair = ("0xAAA", 1)
    kw = {"eoa": {pair: None}} if field == "eoa" else {"unique": {pair: None}}
    with pytest.raises(SelectionRefused):
        make_selector([pair], **kw).select(EPOCH)


def test_exhaustion_refuses_selection():
    with pytest.raises(SelectionRefused) as e:
        make_selector([("0xAAA", 1)], meta={}).select(EPOCH)
    assert "fail-closed" in str(e.value)


def test_attempt_budget_caps_the_scan():
    pairs = [(f"0x{i:03x}", i) for i in range(50)]
    selector = make_selector(pairs, meta={}, max_attempts=10)
    with pytest.raises(SelectionRefused) as e:
        selector.select(EPOCH)
    assert "10 candidates" in str(e.value)


def test_refusal_message_never_names_a_candidate():
    """Same discipline as FindabilityRefused: refusal text reaches the
    operator's Telegram, so no candidate name/contract may appear in it."""
    pair = ("0xDEADBEEF", 9)
    try:
        make_selector([pair], unique={pair: None}).select(EPOCH)
    except SelectionRefused as e:
        msg = str(e).lower()
        assert "0xdeadbeef" not in msg
        assert "whispering" not in msg
    else:  # pragma: no cover
        pytest.fail("expected SelectionRefused")

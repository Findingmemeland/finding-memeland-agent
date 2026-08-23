"""Tests for relic_core (package 1) — offline, fakes only.

Run: pytest test_relic_core.py  (in the repo, imports resolve to
finding_memeland.persona.relic etc. — adjust paths to match where the files land;
Fable wires the final import paths on review).
"""

from __future__ import annotations

import json

import pytest

from finding_memeland.persona.relic import (
    Relic,
    RelicState,
    build_relic_commitment,
    ladder_exempt_filter,
    new_identity,
    relic_canonical_id,
    verify_relic_commitment,
)
from finding_memeland.persona.relic_generator import RelicGenerator
from finding_memeland.persona.relic_pool import (
    FakeRelicRepo,
    NullPoolCipher,
    RelicPool,
    _identity_to_json,
)

# --------------------------------------------------------------------------- #
# fakes                                                                        #
# --------------------------------------------------------------------------- #


class _Msg:
    def __init__(self, text):
        self.content = [type("C", (), {"text": text})()]


class FakeAnthropic:
    """Returns queued JSON strings from messages.create()."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

        class _Messages:
            def create(inner, **kw):  # noqa: N805
                self.calls += 1
                return _Msg(self._responses.pop(0))

        self.messages = _Messages()


class FakeNameCheck:
    def __init__(self, blocked=()):
        self.blocked = set(blocked)
        self.seen = []

    def is_available(self, name):
        self.seen.append(name)
        return name not in self.blocked


class ReversibleCipher:
    """A visible, reversible transform so tests can assert the stored blob is NOT
    the plaintext (blind property) while still round-tripping."""

    def encrypt(self, s):
        return "ENC::" + s[::-1]

    def decrypt(self, t):
        assert t.startswith("ENC::")
        return t[len("ENC::"):][::-1]


def _relic_json(name="Rusted Ledgerkeep", desc="kept the books before books kept themselves",
                terms=None, style_ok=True):
    return json.dumps({
        "archetype": "invented crypto-native creature",
        "name": name,
        "description": desc,
        "image_prompt": "a weathered ledger with glowing eyes",
        "solution_terms": terms if terms is not None else ["Cassandra"],
    })


# --------------------------------------------------------------------------- #
# commitment                                                                   #
# --------------------------------------------------------------------------- #


def test_commitment_is_deterministic_and_verifies():
    cid = relic_canonical_id("base", "0xABCdef", 7)
    h = build_relic_commitment(cid, "ABCD2345", "deadbeef")
    assert h == build_relic_commitment(cid, "ABCD2345", "deadbeef")
    assert verify_relic_commitment(cid, "ABCD2345", "deadbeef", h)


def test_canonical_id_lowercases_contract():
    assert relic_canonical_id("base", "0xAbC", 1) == "base:0xabc:1"


def test_commitment_binds_the_specific_nft_not_just_the_code():
    a = build_relic_commitment(relic_canonical_id("base", "0xaa", 1), "CODE2345", "s")
    b = build_relic_commitment(relic_canonical_id("base", "0xbb", 1), "CODE2345", "s")
    assert a != b  # same code, different NFT => different commitment


def test_wrong_code_fails_verification():
    cid = relic_canonical_id("base", "0xaa", 1)
    h = build_relic_commitment(cid, "RIGHT234", "s")
    assert not verify_relic_commitment(cid, "WRONG234", "s", h)


# --------------------------------------------------------------------------- #
# model                                                                        #
# --------------------------------------------------------------------------- #


def test_relic_not_launchable_before_mint():
    r = Relic(id="r1")
    assert r.canonical_id() is None
    assert not r.is_launchable()


def test_relic_launchable_once_minted_and_committed():
    r = Relic(id="r1", state=RelicState.MINTED, chain="base",
              contract="0xaa", token_id="3", commitment="h")
    assert r.canonical_id() == "base:0xaa:3"
    assert r.is_launchable()


def test_new_identity_generates_safe_code_and_salt():
    ident = new_identity(name="Quiet Furnaceling", description="burns nothing",
                         image_prompt="a warm little furnace", solution_terms=["x"])
    assert len(ident.claim_code) == 8
    assert not (set(ident.claim_code) & set("O0I1"))  # ambiguous chars excluded
    assert len(ident.salt) == 32  # token_hex(16)


def test_ladder_exempt_filter_drops_exempt_hunts():
    triples = [(75, 750, True), (50, 500, False), (500, 500, False)]
    kept = list(ladder_exempt_filter(triples))
    assert kept == [(50.0, 500.0), (500.0, 500.0)]  # the exempt surprise win removed


# --------------------------------------------------------------------------- #
# generator                                                                    #
# --------------------------------------------------------------------------- #


def test_generator_happy_path_applies_style_and_builds_identity():
    gen = RelicGenerator(FakeAnthropic([_relic_json()]), "m", FakeNameCheck())
    g = gen.generate(register="medium")
    assert len(g.name.split()) == 2
    assert g.image_style in g.image_prompt or g.image_style  # a style bucket was chosen
    ident = g.to_identity()
    assert len(ident.claim_code) == 8


def test_generator_rejects_three_word_name_then_retries():
    fake = FakeAnthropic([_relic_json(name="The Big Ledger"), _relic_json(name="Rusted Ledger")])
    gen = RelicGenerator(fake, "m", FakeNameCheck())
    g = gen.generate()
    assert g.name == "Rusted Ledger"
    assert fake.calls == 2


def test_generator_rejects_closed_category_names_then_retries():
    """A word from a small CLOSED set can be pointed at AND identified by the same
    clue, so it collapses to a handful of candidates and the hunt dies early —
    measured in mini hunt #1 (2026-08-23): "Uncle Pump" fell on clue 3 in 12
    minutes. Enforced in code, not only in the prompt, so drift cannot revive it."""
    fake = FakeAnthropic([
        _relic_json(name="Uncle Pump"),      # kinship
        _relic_json(name="Maroon Ledger"),   # colour
        _relic_json(name="Rusted Ledger"),   # open field — accepted
    ])
    gen = RelicGenerator(fake, "m", FakeNameCheck())
    g = gen.generate()
    assert g.name == "Rusted Ledger"
    assert fake.calls == 3


def test_closed_category_check_is_case_and_punctuation_insensitive():
    fake = FakeAnthropic([_relic_json(name="tuesday's Gremlin")] * 3)
    gen = RelicGenerator(fake, "m", FakeNameCheck())
    with pytest.raises(RuntimeError, match="failed after"):
        gen.generate()


def test_generator_rejects_googlable_name_then_retries():
    fake = FakeAnthropic([_relic_json(name="Elon Musk"), _relic_json(name="Salted Horizonet")])
    gen = RelicGenerator(fake, "m", FakeNameCheck(blocked={"Elon Musk"}))
    g = gen.generate()
    assert g.name == "Salted Horizonet"


def test_generator_rejects_description_that_leaks_solution():
    leak = _relic_json(desc="this is secretly Cassandra herself", terms=["Cassandra"])
    good = _relic_json(desc="she warned them and they laughed", terms=["Cassandra"])
    fake = FakeAnthropic([leak, good])
    gen = RelicGenerator(fake, "m", FakeNameCheck())
    g = gen.generate()
    assert "cassandra" not in g.description.lower()


def test_generator_gives_up_after_max_attempts():
    fake = FakeAnthropic([_relic_json(name="One")] * 3)  # always 1 word
    gen = RelicGenerator(fake, "m", FakeNameCheck())
    with pytest.raises(RuntimeError, match="failed after"):
        gen.generate()


# --------------------------------------------------------------------------- #
# pool (blind storage + selection)                                             #
# --------------------------------------------------------------------------- #


def test_pool_stores_identity_encrypted_not_plaintext():
    repo = FakeRelicRepo()
    pool = RelicPool(repo, ReversibleCipher())
    ident = new_identity(name="Maroon Ledgerkeep", description="kept the books",
                         image_prompt="ledger", solution_terms=["Cassandra"])
    pool.add(Relic(id="r1"), ident)
    blob = repo.get_identity_ciphertext("r1")
    assert "Maroon Ledgerkeep" not in blob  # name not in the clear
    assert ident.claim_code not in blob      # code not in the clear
    # only the commitment (once minted) is ever public — not tested here as clear text


def test_pool_reveal_round_trips_identity():
    repo = FakeRelicRepo()
    pool = RelicPool(repo, ReversibleCipher())
    ident = new_identity(name="Quiet Furnaceling", description="burns nothing",
                         image_prompt="furnace", solution_terms=["x"])
    pool.add(Relic(id="r1"), ident)
    got = pool.reveal_identity("r1")
    assert got.name == "Quiet Furnaceling"
    assert got.claim_code == ident.claim_code


def test_peek_launchable_picks_oldest_and_refuses_when_empty():
    from datetime import datetime, timedelta, timezone
    repo = FakeRelicRepo()
    pool = RelicPool(repo, NullPoolCipher())
    with pytest.raises(RuntimeError, match="no launchable relic"):
        pool.peek_launchable()

    now = datetime.now(timezone.utc)
    for i, age_days in enumerate((2, 20, 9)):  # r1=2d, r2=20d(oldest), r3=9d
        ident = new_identity(name=f"Name{i} Two", description="d",
                             image_prompt="p", solution_terms=["x"])
        r = Relic(id=f"r{i}")
        pool.add(r, ident)
        pool.mark_minted(f"r{i}", chain="base", contract=f"0x{i}", token_id=str(i),
                         mint_wallet_ref=f"W{i}", image_uri="ipfs://x",
                         commitment="h", minted_at=now - timedelta(days=age_days))
    relic, ident = pool.peek_launchable()
    assert relic.id == "r1"  # minted 20 days ago = oldest = most aged in the stream


def test_mark_minted_sets_commitment_and_state():
    repo = FakeRelicRepo()
    pool = RelicPool(repo, NullPoolCipher())
    ident = new_identity(name="Salted Horizonet", description="d", image_prompt="p",
                         solution_terms=["x"])
    pool.add(Relic(id="r1"), ident)
    cid = relic_canonical_id("base", "0xaa", "5")
    commit = ident.commitment_for(cid)
    pool.mark_minted("r1", chain="base", contract="0xaa", token_id="5",
                     mint_wallet_ref="W1", image_uri="ipfs://x", commitment=commit)
    r = repo.get_relic("r1")
    assert r.state == RelicState.MINTED and r.commitment == commit
    assert r.is_launchable()
    # the reveal can reproduce a verifiable commitment
    assert verify_relic_commitment(r.canonical_id(), ident.claim_code, ident.salt, r.commitment)

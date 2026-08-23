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
from finding_memeland.persona.relic_generator import (
    NAME_DOMAINS,
    RelicGenerator,
    name_words,
)
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
        self.last_kwargs = {}

        class _Messages:
            def create(inner, **kw):  # noqa: N805
                self.calls += 1
                self.last_kwargs = kw
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


def test_enumerable_words_are_flagged_not_rejected():
    """Pedro, 2026-08-23: knowing ONE of the two words gets a player nowhere —
    marketplace search needs both (measured on OpenSea). So an enumerable word is
    not a defect to ban, it is a constraint to hand to the CLUE writer: never
    gesture at its category. The name that lost mini hunt #1 is legal again."""
    fake = FakeAnthropic([_relic_json(name="Uncle Pump")])
    gen = RelicGenerator(fake, "m", FakeNameCheck())
    g = gen.generate(register="cerebral")
    assert g.name == "Uncle Pump"
    assert fake.calls == 1                       # accepted first time, no retry
    assert set(g.enumerable_words) == {"uncle", "pump"}


def test_enumerable_flag_catches_possessives_and_hyphens():
    for name, expected in (
        ("tuesday's Gremlin", {"tuesday"}),
        ("Uncle-Pump Beast", {"uncle", "pump"}),
        ("GREEN Ledger", {"green"}),
    ):
        gen = RelicGenerator(FakeAnthropic([_relic_json(name=name)]), "m", FakeNameCheck())
        assert set(gen.generate(register="medium").enumerable_words) == expected


def test_open_field_names_carry_no_enumerable_flag():
    for name in ("Brackish Lemur", "Smudge Notary", "Sundancer Gremlin", "Redwood Sprite"):
        gen = RelicGenerator(FakeAnthropic([_relic_json(name=name)]), "m", FakeNameCheck())
        assert gen.generate(register="cerebral").enumerable_words == ()


def test_request_never_uses_assistant_prefill():
    """claude-sonnet-4-6 rejects assistant prefill outright (400, measured
    2026-08-23 — 21 failures out of 21). The fake happily accepted it, which is
    the lesson: a fake cannot validate an API contract."""
    fake = FakeAnthropic([_relic_json()])
    RelicGenerator(fake, "m", FakeNameCheck()).generate(register="medium")
    messages = fake.last_kwargs["messages"]
    assert messages[-1]["role"] == "user"
    assert all(m["role"] != "assistant" for m in messages)


def test_preamble_before_the_json_is_tolerated():
    """The model sometimes thinks out loud first; the JSON still has to be found."""
    noisy = "Let me work through this carefully.\n\n**Domain:** meme x history\n\n" + _relic_json()
    fake = FakeAnthropic([noisy])
    g = RelicGenerator(fake, "m", FakeNameCheck()).generate(register="medium")
    assert g.name == "Rusted Ledgerkeep"


def test_a_word_spent_by_an_earlier_relic_cannot_come_back():
    """Measured 2026-08-23: theme-level anti-repetition let the model reuse its
    favourite textures — "brackish" landed in three separate samples, "sensei",
    "hollow", "stale", "soggy" and "glitch" in two each. Two relics sharing a word
    also make marketplace search ambiguous."""
    fake = FakeAnthropic([
        _relic_json(name="Brackish Grimoire"),   # 'brackish' already spent
        _relic_json(name="Trembling Sensei"),
    ])
    gen = RelicGenerator(fake, "m", FakeNameCheck())
    g = gen.generate(register="cerebral", avoid_words={"brackish", "hollow"})
    assert g.name == "Trembling Sensei"
    assert fake.calls == 2


def test_spent_words_are_listed_in_the_prompt():
    fake = FakeAnthropic([_relic_json()])
    gen = RelicGenerator(fake, "m", FakeNameCheck())
    gen.generate(register="medium", avoid_words={"brackish", "sensei"})
    sent = fake.last_kwargs["messages"][0]["content"]
    assert "brackish" in sent and "sensei" in sent


def test_word_reservation_keeps_short_nouns_and_drops_function_words():
    """'rat', 'owl', 'orb' are exactly what a meme name lives on, so 3 letters
    still count; 'the'/'of' must not be reserved or the pool starves."""
    assert name_words("The Ox of War") == {"war"}
    assert name_words("Uncle-Pump's Beast") == {"uncle", "pump", "beast"}
    assert name_words("chrome rat") == {"chrome", "rat"}


def test_every_relic_is_meme_plus_one_rotating_world():
    """MEME is mandatory in every name (Pedro) — the other domain rotates so a
    full lap visits every world exactly once."""
    gen = RelicGenerator(FakeAnthropic([]), "m", FakeNameCheck())
    n = len(NAME_DOMAINS)
    pairs = [gen._pick_domains(i) for i in range(n)]
    assert all(a == "meme" for a, _ in pairs)
    assert {b for _, b in pairs} == set(NAME_DOMAINS)


def test_difficulty_roll_is_weighted_toward_hard():
    gen = RelicGenerator(FakeAnthropic([]), "m", FakeNameCheck())
    rolls = [gen._pick_register() for _ in range(4000)]
    hard = rolls.count("cerebral") / len(rolls)
    easy = rolls.count("accessible") / len(rolls)
    assert 0.62 < hard < 0.78          # target 70%
    assert 0.05 < easy < 0.16          # target 10%


def test_generated_relic_theme_tag_feeds_theme_level_anti_repetition():
    """avoid_recent must carry THEMES: the pool was repeating 'stoic animal that
    never sells' while never repeating a name."""
    fake = FakeAnthropic([_relic_json()])
    gen = RelicGenerator(fake, "m", FakeNameCheck())
    g = gen.generate(register="medium", sequence=0)
    tag = g.theme_tag()
    assert g.name in tag and g.tone in tag
    assert g.domains and len(g.domains) == 2


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


def test_spent_words_collects_names_only_across_the_pool():
    """The hard uniqueness rule reads NAMES only. `avoid_recent` feeds the prompt
    with themes too, but reserving solution terms would burn words no relic ever
    actually spent."""
    repo = FakeRelicRepo()
    pool = RelicPool(repo, ReversibleCipher())
    pool.add(Relic(id="r1"), new_identity(
        name="Brackish Grimoire", description="d", image_prompt="p",
        solution_terms=["Cassandra", "prophecy"]))
    pool.add(Relic(id="r2"), new_identity(
        name="flunking sensei", description="d", image_prompt="p",
        solution_terms=["teacher"]))
    spent = pool.spent_words()
    assert spent == {"brackish", "grimoire", "flunking", "sensei"}
    assert "cassandra" not in spent and "prophecy" not in spent


def test_spent_words_skips_unreadable_rows_instead_of_blocking():
    """A pool that cannot be fully decrypted must still allow a new relic: less
    variety beats a stalled pipeline."""
    repo = FakeRelicRepo()
    pool = RelicPool(repo, ReversibleCipher())
    pool.add(Relic(id="r1"), new_identity(
        name="paper crane", description="d", image_prompt="p", solution_terms=["x"]))
    repo.add_relic(relic=Relic(id="r2"), identity_ciphertext="not-decryptable")
    assert pool.spent_words() == {"paper", "crane"}


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

"""The larder and `/prepare` — the properties that decide whether a hunt runs.

Every test here comes from something that actually happened:
  · a dead image became a "verified" target and killed Hunt #11 (16/09);
  · a 171 MB artwork that vision could never read (measured 16/09);
  · the larder is the next thirty answers — it must never render itself.
"""
from __future__ import annotations

import random

import pytest

from finding_memeland.target.prepare import (
    Candidate, Larder, LarderIntegrityError, LarderStore, Prepared,
    PreparedStore, PrepareRefused, Source, TargetFinder, TargetPreparer,
    enumerable_sources, excluded_ids, used_hmac,
)
from finding_memeland.target.refresh import TokenRead

S = Source("foundation", "ethereum", "0x" + "ab" * 20)
S2 = Source("superrare2", "ethereum", "0x" + "cd" * 20)
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


class World:
    """A fake chain + gateway."""

    def __init__(self, *, default_name="Some Two Words", names=None,
                 dead: set[str] | None = None, sizes=None,
                 size_of: dict[str, int] | None = None,
                 unique: bool = True, eoa: bool = True):
        self.default_name = default_name
        self.names = names or {}
        self.dead = dead or set()
        self.sizes = {S.slug: 1000} if sizes is None else sizes
        self.size_of = size_of or {}
        self._unique = unique
        self._eoa = eoa
        self.probes: list[str] = []
        self.full_reads: list[str] = []
        self.paid_calls = 0

    def total_supply(self, chain, contract):
        for s in (S, S2):
            if s.contract == contract:
                return self.sizes.get(s.slug)
        return self.sizes.get(S.slug)          # the larder's synthetic source

    def token_by_index(self, chain, contract, idx):
        return idx + 1

    def read_token(self, chain, contract, token_id):
        return TokenRead(token_uri=f"ipfs://Qm{token_id}",
                         metadata={"name": self.names.get(token_id, self.default_name),
                                   "image": f"ipfs://img{token_id}",
                                   "description": "d"})

    def probe_image(self, url):
        self.probes.append(url)
        if url in self.dead:
            return None
        return PNG, self.size_of.get(url, 4096)

    def fetch_image(self, url):
        self.full_reads.append(url)
        return None if url in self.dead else PNG

    def owner_is_eoa(self, chain, contract, token_id):
        return self._eoa

    def name_is_unique(self, base, chain, contract, token_id):
        self.paid_calls += 1
        return self._unique

    def describe(self, data):
        return "an artwork"

    def write_clue_one(self, target, description):
        return {"text": "clue", "for": target.id()}


def _finder(world, **kw):
    kw.setdefault("sources", [S])
    return TargetFinder(
        total_supply=world.total_supply, token_by_index=world.token_by_index,
        read_token=world.read_token, probe_image=world.probe_image,
        owner_is_eoa=world.owner_is_eoa, name_is_unique=world.name_is_unique,
        rng=random.Random(7), **kw)


def _preparer(world, finder, **kw):
    return TargetPreparer(
        finder=finder, fetch_image=world.fetch_image,
        describe=world.describe, write_clue_one=world.write_clue_one,
        rng=random.Random(11), **kw)


# --- the larder ----------------------------------------------------------- #


def test_a_dead_image_never_enters_the_larder():
    """Hunt #11: `ipfs://…` is a well-formed URL, not a promise that anyone
    still pins the bytes."""
    world = World(dead={f"ipfs://img{i}" for i in range(1, 2000)})
    larder = Larder()
    tally = _finder(world).fill(larder, want=3, max_draws=20)
    assert larder.size() == 0
    assert tally.image > 0


def test_an_artwork_too_big_for_vision_never_enters():
    world = World(size_of={f"ipfs://img{i}": 200 * 1024 * 1024 for i in range(1, 2000)})
    larder = Larder()
    tally = _finder(world).fill(larder, want=3, max_draws=20)
    assert larder.size() == 0 and tally.too_big > 0


def test_the_paid_call_is_last_so_free_checks_never_spend_quota():
    """A one-word name must not cost a marketplace search."""
    world = World(default_name="Untitled")
    larder = Larder()
    _finder(world).fill(larder, want=2, max_draws=15)
    assert world.paid_calls == 0


def test_fill_stops_at_want_and_never_repeats_a_token():
    world = World()
    larder = Larder()
    _finder(world).fill(larder, want=5, max_draws=200)
    ids = [c.id() for c in larder.candidates]
    assert larder.size() == 5 and len(set(ids)) == 5


def test_the_larder_never_renders_its_contents():
    world = World()
    larder = Larder()
    _finder(world).fill(larder, want=2, max_draws=50)
    assert "0x" not in repr(larder) and "ipfs" not in repr(larder)
    assert "0x" not in repr(larder.candidates[0])
    assert "ready" in repr(larder)


def test_the_store_round_trips_and_fails_closed_on_a_bad_key():
    class Cipher:
        def encrypt(self, s): return "E" + s
        def decrypt(self, s):
            if not s.startswith("E"):
                raise ValueError("bad key")
            return s[1:]

    blob = {}
    store = LarderStore(cipher=Cipher(), read=lambda: blob.get("v"),
                        write=lambda p: blob.__setitem__("v", p))
    world = World()
    larder = Larder()
    _finder(world).fill(larder, want=3, max_draws=50)
    larder.consume(larder.candidates[0].id())
    store.save(larder)
    back = store.load()
    assert back.size() == larder.size() and back.used == larder.used

    blob["v"] = "GARBAGE"
    with pytest.raises(LarderIntegrityError) as e:
        store.load()
    assert "0x" not in str(e.value) and "ipfs" not in str(e.value)


def test_a_used_target_is_never_drawn_again():
    world = World()
    larder = Larder()
    _finder(world).fill(larder, want=3, max_draws=60)
    first = larder.candidates[0].id()
    larder.consume(first)
    assert not any(c.id() == first for c in larder.candidates)
    assert larder.has(first)            # still remembered, so fill skips it


# --- prepare --------------------------------------------------------------- #


def test_prepare_seals_a_target_and_consumes_it():
    world = World()
    finder = _finder(world)
    larder = Larder()
    finder.fill(larder, want=8, max_draws=200)
    before = larder.size()
    prepared, larder = _preparer(world, finder).prepare(larder)
    assert prepared.target.name_onchain
    assert larder.size() == before - 1
    assert larder.has(prepared.target.id())


def test_prepare_re_verifies_and_drops_a_candidate_that_died_in_the_larder():
    """Pins expire between fill and prepare. The stale one is dropped, not
    launched — and the next one is used instead."""
    world = World()
    finder = _finder(world)
    larder = Larder()
    finder.fill(larder, want=8, max_draws=200)
    world.dead = {larder.candidates[0].image}
    prepared, larder = _preparer(world, finder).prepare(larder)
    assert prepared.target.image not in world.dead


def test_the_artwork_is_read_once_and_only_the_target():
    """No decoys anywhere (17/09). What hid the target from a provider was
    never the padding — it is the ROTATION on the live check, which four
    decoys could never match in scale. Here: one read, the target's."""
    world = World()
    finder = _finder(world)
    larder = Larder()
    finder.fill(larder, want=4, max_draws=120)
    prepared, _ = _preparer(world, finder).prepare(larder)
    assert world.full_reads == [prepared.target.image]


def test_a_small_larder_still_prepares_a_hunt():
    """The floor of five went away with the decoys: the critical path can no
    longer refuse for a reason that has nothing to do with the target — the
    failure that lost draw 5 of the 16/09 live run."""
    world = World()
    finder = _finder(world)
    larder = Larder()
    finder.fill(larder, want=1, max_draws=40)
    assert larder.size() == 1
    prepared, larder = _preparer(world, finder).prepare(larder)
    assert prepared.target.name_onchain and larder.size() == 0


def test_an_empty_larder_refuses_and_says_to_fill():
    world = World()
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, _finder(world)).prepare(Larder())
    assert "fill" in str(e.value)


# --- sources --------------------------------------------------------------- #


def test_a_source_that_does_not_enumerate_is_dropped_not_probed():
    def total(chain, contract):
        if contract == S2.contract:
            raise RuntimeError("execution reverted")
        return 1000

    ok, dropped = enumerable_sources(
        [S, S2], total_supply=total, token_by_index=lambda c, k, i: i + 1)
    assert [s.slug for s in ok] == [S.slug] and "superrare2" in dropped


def test_no_source_answers_refuses_loudly_without_drawing():
    world = World(sizes={})
    with pytest.raises(PrepareRefused) as e:
        _finder(world).fill(Larder(), want=1)
    assert "totalSupply" in str(e.value)


# --- used targets: the authority is the hunt rows, not the blob ------------ #

KEY = "a-very-secret-key"


def test_a_restored_backup_cannot_resurrect_a_revealed_target():
    """Fable, 17/09: restore the larder from before a hunt and a target that
    was already named in a reveal comes back. The hunt rows only grow, so
    they are the authority."""
    world = World()
    finder = _finder(world)
    larder = Larder()
    finder.fill(larder, want=6, max_draws=160)
    revealed = larder.candidates[0].id()
    rows = [used_hmac(revealed, KEY)]          # what the hunt row carries

    prep = TargetPreparer(
        finder=finder, fetch_image=world.fetch_image, describe=world.describe,
        write_clue_one=world.write_clue_one, rng=random.Random(3),
        key=KEY, used_hmacs=lambda: rows)
    prepared, larder = prep.prepare(larder)
    assert prepared.target.id() != revealed
    assert not any(c.id() == revealed for c in larder.candidates)


def test_the_fingerprint_is_keyed_so_the_hunt_table_is_not_an_oracle():
    """A bare SHA-256 of chain:contract:tokenId would let anyone hash the
    167k ids and read every past target off the hunt table — and confirm a
    guess about a LIVE one. Same reasoning as the salt in the commitment."""
    tid = "ethereum:0xabc:1"
    assert used_hmac(tid, KEY) != used_hmac(tid, "another-key")
    assert tid not in used_hmac(tid, KEY)
    assert len(used_hmac(tid, KEY)) == 64


def test_excluded_ids_matches_only_what_the_rows_carry():
    world = World()
    larder = Larder()
    _finder(world).fill(larder, want=4, max_draws=120)
    one = larder.candidates[1].id()
    out = excluded_ids(larder.candidates, used_hmacs=[used_hmac(one, KEY)],
                       key=KEY)
    assert out == {one}


# --- the prepared hunt lives in the database, not in memory ---------------- #


class Cipher:
    def encrypt(self, s): return "E" + s

    def decrypt(self, s):
        if not s.startswith("E"):
            raise ValueError("bad key")
        return s[1:]


def _prepared_store(blob: dict) -> PreparedStore:
    return PreparedStore(cipher=Cipher(), read=lambda: blob.get("v"),
                         write=lambda p: blob.__setitem__("v", p))


def test_the_prepared_hunt_survives_a_restart():
    """A restart between the day before and the hour must not lose the
    preparation — and losing it silently is worse, because the operator
    finds out at the announced minute."""
    world = World()
    finder = _finder(world)
    larder = Larder()
    finder.fill(larder, want=4, max_draws=120)
    prepared, _ = _preparer(world, finder).prepare(larder)

    blob = {}
    store = _prepared_store(blob)
    store.save(prepared)
    back = store.load()                      # a fresh process would do this
    assert back is not None
    assert back.target.id() == prepared.target.id()
    assert back.target.metadata_sha256 == prepared.target.metadata_sha256
    assert back.image_description == prepared.image_description
    assert back.clue_one == prepared.clue_one


def test_the_prepared_store_fails_closed_and_names_nothing():
    blob = {"v": "GARBAGE"}
    with pytest.raises(LarderIntegrityError) as e:
        _prepared_store(blob).load()
    assert "0x" not in str(e.value) and "ipfs" not in str(e.value)
    assert "/prepare" in str(e.value)


def test_an_empty_slot_reads_as_nothing_prepared():
    assert _prepared_store({}).load() is None
    assert _prepared_store({"v": ""}).load() is None


def test_the_prepared_never_renders_its_contents():
    world = World()
    finder = _finder(world)
    larder = Larder()
    finder.fill(larder, want=4, max_draws=120)
    prepared, _ = _preparer(world, finder).prepare(larder)
    assert "0x" not in repr(prepared) and "ipfs" not in repr(prepared)

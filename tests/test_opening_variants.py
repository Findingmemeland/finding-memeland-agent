"""A abertura do post de abertura roda (Pedro, 24/11).

O texto de abertura foi o mesmo da #11 à #41, e o alcance do post de abertura
tem descido de hunt para hunt. A primeira parte (a frase de entrada e a
explicação do jogo) passa a sair de dez aberturas na voz do oráculo:
primeira pessoa, minúsculas, seca, com 🐸. É tirada à sorte e nunca é a da
hunt anterior. O bloco das regras fica igual em todas e passa todo para
baixo da pista: como responder, o prémio, a linha dos holders, o "Quote or
repost", o commitment e o "Check pinned for rules".

O id da abertura fica na linha da hunt (hunts.opening_variant), para comparar
o alcance. Uma coluna que ainda não exista nunca impede a hunt de arrancar.
"""
from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from finding_memeland.db.client import Repo
from finding_memeland.target.dryrun import TargetWorld
from finding_memeland.target.templates import (
    BRAND_LINE,
    OPENINGS,
    TARGET_CLUE_ONE_REPLY,
    pick_opening,
    target_clue_one,
    x_weighted_len,
)

COMMIT = "c0ffee" * 10 + "abcd"
URL_RE = re.compile(r"https?://|\bwww\.", re.IGNORECASE)
# pedir interacção é o que o X castiga; e as do filtro do oráculo (filler)
BAIT = ("like", "likes", "retweet", "repost", "follow", "share", "tag", "moon",
        "pump", "100x", "10x", "guaranteed", "buy", "don't miss")


class _First:
    """Um sorteio que tira sempre o primeiro do monte."""

    def choice(self, seq):
        return seq[0]


# --------------------------------------------------------------------------- #
# As aberturas                                                                 #
# --------------------------------------------------------------------------- #


def test_eight_to_ten_openings_with_stable_ids():
    assert 8 <= len(OPENINGS) <= 10
    assert [o.id for o in OPENINGS] == [f"o{i:02d}" for i in range(1, len(OPENINGS) + 1)]
    assert len({o.text for o in OPENINGS}) == len(OPENINGS)
    assert len({o.render(7).split("\n")[0] for o in OPENINGS}) == len(OPENINGS)


@pytest.mark.parametrize("o", OPENINGS, ids=lambda o: o.id)
def test_each_opening_is_the_oracle_and_explains_the_game(o):
    t = o.render(42)
    assert t.startswith("hunt #42") and t.count("#42") == 1
    assert t.count("\U0001F438") == 1                            # com 🐸, uma vez
    bare = re.sub(r"\b(?:NFT|Base)\b", "", t)
    assert bare == bare.lower()                                  # minúsculas
    assert re.search(r"\bi\b", t)                                # primeira pessoa
    # o jogo, para quem chega a frio
    assert BRAND_LINE in t                                       # a frase da marca, igual
    assert "Base" not in t.replace(BRAND_LINE, "")               # o tesouro não é "em Base"
    assert "someone else" in t and "NFT" in t
    low = t.lower()
    assert "clue" in low and "name" in low and "token" in low
    # as regras da casa
    assert "$" not in t and "@" not in t and not URL_RE.search(t)
    assert not [b for b in BAIT if re.search(rf"\b{re.escape(b)}\b", low)]
    assert x_weighted_len(t) <= 280


def test_the_rules_below_the_clue_are_the_same_for_every_opening():
    tails = {target_clue_one(42, "a clue", "200,000,000", COMMIT, opening=o)
             .split("1st clue:", 1)[1] for o in OPENINGS}
    assert tails == {
        "\n\na clue\n\n"
        "found it? reply to this post with its chain:contract:tokenId (or the "
        "marketplace link). the NFT stays with its owner.\n"
        "The first to find it wins 200,000,000 FIND.\n"
        "hold FIND to win the full prize — non-holders win 10%.\n"
        "Quote or repost this post to enter.\n\n"
        f"commitment v2: {COMMIT}\n\n"
        "Check pinned for rules."}
    assert TARGET_CLUE_ONE_REPLY.startswith("found it? reply to this post")


def test_the_full_post_opens_with_the_opening_and_has_no_cashtag():
    """Sem cashtag em lado nenhum da abertura, nem no prémio (Pedro, 25/11):
    5 dos 10 posts que o X escondeu como SPAM_HIGH_RECALL em outubro eram
    aberturas."""
    o = OPENINGS[4]
    post = target_clue_one(42, "a clue", "200,000,000", COMMIT, opening=o)
    assert post.startswith(o.render(42) + "\n\n1st clue:\n\na clue\n\n")
    assert not re.findall(r"\$[A-Za-z]+", post) and "$" not in post
    assert "wins 200,000,000 FIND." in post
    assert "non-holders" not in target_clue_one(42, "c", "1", COMMIT, None, opening=o)


# --------------------------------------------------------------------------- #
# O sorteio                                                                    #
# --------------------------------------------------------------------------- #


def test_the_draw_never_repeats_the_previous_opening():
    for prev in OPENINGS:
        seen = {pick_opening(prev.id, random.Random(s)).id for s in range(400)}
        assert prev.id not in seen and len(seen) == len(OPENINGS) - 1
    assert pick_opening("o01", _First()).id == "o02"


def test_no_previous_or_an_unknown_one_leaves_all_in_the_draw():
    for prev in (None, "", "o99"):
        assert len({pick_opening(prev, random.Random(s)).id for s in range(400)}) == len(OPENINGS)


# --------------------------------------------------------------------------- #
# No arranque da hunt                                                          #
# --------------------------------------------------------------------------- #


def test_go_live_draws_against_the_previous_hunt_and_records_the_id():
    w = TargetWorld()
    w.rig.repo.hunts[0] = {"id": 0, "state": "done", "opening_variant": "o01"}
    w.ports.opening_rng = _First()
    hunt = w.launch()
    (post,) = w.posts()
    assert post.startswith(OPENINGS[1].render(hunt.number) + "\n\n1st clue:")
    assert w.rig.repo.hunts[hunt.id]["opening_variant"] == "o02"
    assert w.rig.repo.hunts[hunt.id]["state"] == "live"
    assert any("LIVE — clue 1 posted" in m and "opening o02" in m for m in w.notices())


def test_hunt_after_hunt_the_opening_never_repeats():
    w = TargetWorld()
    w.ports.opening_rng = random.Random(7)
    used = []
    for _ in range(12):
        hunt = w.launch()
        used.append(w.rig.repo.hunts[hunt.id]["opening_variant"])
        w.rig.repo.hunts[hunt.id]["state"] = "done"
    assert all(a != b for a, b in zip(used, used[1:], strict=False))
    assert len(set(used)) > 3
    assert all(p.startswith(f"hunt #{i}") for i, p in enumerate(w.posts(), start=1))


def test_the_previous_is_the_latest_hunt_not_the_first():
    """Com um sorteio que tira sempre o primeiro do monte, as aberturas têm
    de alternar o01/o02: a "anterior" é a da última hunt, não a da primeira."""
    w = TargetWorld()
    w.ports.opening_rng = _First()
    used = []
    for _ in range(4):
        hunt = w.launch()
        used.append(w.rig.repo.hunts[hunt.id]["opening_variant"])
        w.rig.repo.hunts[hunt.id]["state"] = "done"
    assert used == ["o01", "o02", "o01", "o02"]


def test_a_missing_column_never_stops_the_hunt():
    """Sem a migração: a leitura da anterior e o registo falham, cada um com
    um aviso, e a hunt arranca na mesma."""
    w = TargetWorld()
    repo = w.rig.repo

    def no_column(*_a, **_k):
        raise RuntimeError("column hunts.opening_variant does not exist")

    real_update = repo.update_hunt

    def update(hunt_id, **fields):
        if "opening_variant" in fields:
            no_column()
        return real_update(hunt_id, **fields)

    repo.last_opening_variant = no_column
    repo.update_hunt = update
    hunt = w.launch()
    assert repo.hunts[hunt.id]["state"] == "live" and len(w.posts()) == 1
    notes = " | ".join(w.notices())
    assert "previous opening unknown (RuntimeError)" in notes
    assert "NOT recorded (RuntimeError)" in notes and "opening_variant" in notes
    assert "does not exist" not in notes                 # o tipo, nunca o texto


def test_the_repo_reads_the_last_recorded_opening():
    from test_repo import _FakeDB
    db = _FakeDB()
    assert Repo(db).last_opening_variant() is None
    (q,) = db.log
    assert q["table"] == "hunts" and q["op"] == "select"
    assert ("is", "opening_variant", "null") in q["filters"]

    class _Q:                       # o pedido inteiro: a mais recente, uma linha
        calls: list = []

        def __getattr__(self, name):
            def call(*a, **k):
                _Q.calls.append((name, a, k))
                return self
            return call

        @property
        def not_(self):
            _Q.calls.append(("not_", (), {}))
            return self

        def execute(self):
            return type("R", (), {"data": [{"opening_variant": "o07"}]})()

    class _DB:
        def table(self, name):
            _Q.calls.append(("table", (name,), {}))
            return _Q()

    assert Repo(_DB()).last_opening_variant() == "o07"
    assert ("order", ("id",), {"desc": True}) in _Q.calls and ("limit", (1,), {}) in _Q.calls


def test_the_migration_is_in_the_schema():
    schema = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
    assert "alter table hunts add column if not exists opening_variant text;" in schema.read_text()

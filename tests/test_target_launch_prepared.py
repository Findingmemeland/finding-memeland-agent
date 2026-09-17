"""/launch consumes what /prepare sealed — it no longer draws anything.

Every test here comes from Hunt #11 (16/09), which died doing at launch
what /prepare now does the day before: four `/launch` attempts, ten to
twenty-five minutes each, an audience watching a prompt that had promised
"seconds", and no Clue 1 at the end of it.

The world is the dry-run's (synthetic snapshot, fake chain, fake clue
engine), so these drive the REAL `prepare_target_hunt` and the REAL
`_go_live` — not a copy of them.
"""
from __future__ import annotations

import pytest

from finding_memeland.orchestrator.state_machine import HuntState
from finding_memeland.target.dryrun import TargetWorld, _GuardFound
from finding_memeland.target.hunt import LaunchRefused


def test_launch_publishes_the_clue_written_by_prepare() -> None:
    """The whole point: the clue the operator waited for yesterday is the
    clue that goes out today. The engine is NOT asked for clue 1 again."""
    w = TargetWorld()
    prepared = w.ports.take_prepared()
    asked: list[int] = []
    inner = w.ports.clue_engine

    class _Counting:
        def __getattr__(self, name):
            return getattr(inner, name)

        def next_clue(self, ctx, i, prior):
            asked.append(i)
            return inner.next_clue(ctx, i, prior)

    w.ports.clue_engine = _Counting()
    w.orch._clue_engine = w.ports.clue_engine
    hunt = w.launch()

    assert hunt.state is HuntState.LIVE
    assert 1 not in asked, "clue 1 was regenerated at launch"
    assert prepared.clue_one.text in w.posts()[0]
    assert hunt.integrity_hash == prepared.commitment
    assert hunt.salt == prepared.salt


def test_launch_refuses_when_nothing_was_prepared() -> None:
    """No preparation is a calm refusal with instructions — not a hunt that
    starts drawing while people watch."""
    w = TargetWorld()
    w.ports.take_prepared = lambda: None
    with pytest.raises(LaunchRefused) as e:
        w.launch()
    assert "/prepare" in str(e.value)
    assert not w.posts()
    assert not w.rig.repo.hunts


def test_launch_refuses_an_expired_preparation() -> None:
    """A preparation has a shelf life: the artwork can be sold, re-pinned or
    listed while it sits. Past the TTL the honest answer is 'prepare again',
    not 'publish and hope'."""
    w = TargetWorld()
    w.ports.prepared_max_age_h = 72.0
    p = w.ports.take_prepared()
    object.__setattr__(p, "prepared_at", "2020-01-01T00:00:00+00:00")
    w.reseal(p)          # pelo store: mexer no objecto devolvido já não chega
    with pytest.raises(LaunchRefused) as e:
        w.launch()
    assert "/prepare" in str(e.value)
    assert not w.posts()


def test_the_search_guard_runs_again_at_launch() -> None:
    """The judge and the blind solver are about the clue and the answer,
    both frozen since yesterday. Searchability is about the WORLD, and the
    world moved — so this one guard runs again, and a hit refuses."""
    w = TargetWorld()
    w.guard_verdict = _GuardFound()
    with pytest.raises(LaunchRefused):
        w.launch()
    assert not w.posts()
    assert not w.rig.repo.hunts
    # and the preparation is NOT thrown away by a refusal
    assert w.prepared_slot is not None


def test_a_refused_launch_does_not_consume_the_preparation() -> None:
    """Refuse, fix the cause, launch again — without paying for a second
    /prepare. The slot is emptied only once a hunt row exists."""
    w = TargetWorld()
    w.guard_verdict = _GuardFound()
    with pytest.raises(LaunchRefused):
        w.launch()
    before = w.prepared_slot
    w.guard_verdict = type("Ok", (), {"ok": True, "found": False, "detail": ""})()
    hunt = w.launch()
    assert hunt.state is HuntState.LIVE
    assert hunt.integrity_hash == before.commitment


def test_the_preparation_is_consumed_exactly_once() -> None:
    """A second /launch on the same preparation would publish the same
    target twice. The slot is emptied as soon as the row exists."""
    w = TargetWorld()
    first = w.ports.take_prepared()
    w.launch()
    assert w.prepared_slot is None
    assert first.id() in w.used_ids


def test_the_used_target_is_fingerprinted_on_the_row() -> None:
    """Keyed, not plain: a plain id column would be an enumeration oracle.
    It exists so a restored larder backup can never resurrect a target this
    project has already revealed."""
    w = TargetWorld()
    prepared = w.ports.take_prepared()
    hunt = w.launch()
    row = w.rig.repo.hunts[hunt.id]
    fp = row.get("target_used_hmac")
    assert fp
    assert fp == w.ports.used_hmac(prepared.target.id())


def test_the_sealed_clue_survives_the_store_as_a_draft() -> None:
    """A recusa de 17/09, agora impossível de reintroduzir em silêncio.

    O store grava `clue_one` como dicionário (json) e lê-o de volta; o launch
    quer um rascunho com `.text`. Os dois discordavam em produção e nenhum
    teste podia ver isso, porque o dry-run guardava o objecto Python que ele
    próprio tinha construído e devolvia-o intacto. Agora atravessa a mesma
    string encriptada que atravessa na caixa."""
    w = TargetWorld()
    made = w._make_prepared()
    w.reseal(made)
    back = w.ports.take_prepared()
    assert back is not made, "o dry-run voltou a segurar o objecto"
    assert hasattr(back.clue_one, "text"), "clue_one voltou como dicionário"
    assert back.clue_one.text == made.clue_one.text
    w.launch()
    assert back.clue_one.text in w.posts()[0]


def test_the_slot_is_an_encrypted_blob_not_an_object() -> None:
    """A preparação fica cifrada em repouso. Se um dia alguém "simplificar"
    o store para guardar o objecto, isto cai — e cai aqui, não no dia em que
    o alvo aparecer legível num backup."""
    w = TargetWorld()
    prepared = w.ports.take_prepared()
    blob = w._prepared_blob
    assert isinstance(blob, str) and blob
    assert prepared.target.name_onchain not in blob
    assert str(prepared.target.contract) not in blob


def test_no_operator_message_names_the_target() -> None:
    """The launch talks about counts, hours and verdicts. Never a name."""
    w = TargetWorld()
    prepared = w.ports.take_prepared()
    w.launch()
    name = prepared.target.name_onchain
    assert all(name not in m for m in w.notices())
    assert all(str(prepared.target.token_id) not in m for m in w.notices())

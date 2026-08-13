"""Fase 2 do pré-vestir — o launch consome a pool 'dressed'.

Os cinco contratos do Pedro (13/08):
1. R3 fail-closed antes de ir live (divergência => recusa + alerta + re-dress)
2. R2 — o launch CONSOME o descritor (código/identidade nunca regenerados;
   o hash da Clue 1 é computado sobre o código persistido)
3. Seleção: a persona 'dressed' MAIS ANTIGA; se falhar a R3, recusa —
   nunca substitui em silêncio pela seguinte
4. Pool vazia => recusa limpa; o caminho antigo nunca é fallback
5. Cegueira mantida (código nunca nos relatórios) e dressed -> in_play
   preserva o descritor intacto

Rig: simulação normal com a DBPersonaSource REAL sobre o FakeRepo — a lógica
de seleção testada é a de produção, não uma cópia.
"""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from finding_memeland.content.integrity import compute_integrity_hash
from finding_memeland.orchestrator.simulation import FakeRepo, build_simulation
from finding_memeland.orchestrator.state_machine import HuntState
from finding_memeland.persona.generator import GeneratedPersona
from finding_memeland.persona.source import DBPersonaSource


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)  # FakeClock start


def _identity(name="Cassandra Tired"):
    return GeneratedPersona(
        display_name=name,
        bio="prophecy is a customer-service job",
        avatar_prompt="a tired oracle with an empty coffee cup",
        banner_prompt="a waiting room in ancient troy",
        voice="deadpan, resigned",
        backstory="the prophet nobody believed, now doing gig work",
        solution_terms=["Cassandra", "Troy"],
        archetype="mythological figure",
        findable_post="nobody books a prophet for good news anymore",
    )


def _dressed_row(pid, ref, handle, *, days_dressed, name="Cassandra Tired", code="K7M3P9W2"):
    return dict(
        id=pid, handle=handle, x_user_id=f"uid-{pid}", oauth_ref=ref,
        state="dressed", phone_verified=True,
        account_created_at=NOW - timedelta(days=90),
        persona_identity=asdict(_identity(name)),
        claim_code=code,
        applied_display_name=name,
        applied_bio=f"prophecy is a customer-service job\ncode: {code}",
        locator_post_id=f"loc-{pid}",
        avatar_applied=True, banner_applied=True,
        handle_hint="hint stored at dress time",
        anchor_posts=[
            {"text": "anchor alpha from the descriptor", "tweet_id": "a1"},
            {"text": "anchor beta from the descriptor", "tweet_id": "a2"},
        ],
        dressed_at=NOW - timedelta(days=days_dressed),
    )


class FakeVerifier:
    def __init__(self, mismatches=None, error=None):
        self.mismatches = list(mismatches or [])
        self.error = error
        self.verified_handles = []

    def verify(self, row, token, secret):
        self.verified_handles.append(row["handle"])
        if self.error:
            raise self.error
        return list(self.mismatches)


class BoomGenerator:
    """R2 guard: the launch must NEVER generate an identity."""

    def generate(self, **kw):
        raise AssertionError("generator called during a pre-dressed launch (R2 violation)")


def _rig(repo=None, *, verifier=None, **kw):
    repo = repo or FakeRepo()
    rig = build_simulation(
        repo=repo,
        persona_source=DBPersonaSource(repo, lambda ref: (f"tok-{ref}", f"sec-{ref}")),
        predressed_launch=True,
        launch_verifier=FakeVerifier() if verifier is None else verifier,
        persona_generator=BoomGenerator(),
        **kw,
    )
    return rig


# ---------------------------------------------------------------------------
# 3 — selection: oldest dressed first
# ---------------------------------------------------------------------------
def test_launch_picks_the_oldest_dressed_persona():
    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-new", "08", "@NewerOne", days_dressed=5,
                                    name="Newer Persona", code="AAAA2222"))
    repo.add_persona(**_dressed_row("p-old", "07", "@ExpressoTitgo", days_dressed=30))
    rig = _rig(repo)

    hunt = rig.orchestrator.run_hunt()

    assert hunt.state is HuntState.DONE
    assert hunt.persona.handle == "@ExpressoTitgo"          # 30d > 5d: oldest wins
    assert repo.personas["p-new"]["state"] == "dressed"     # the newer one untouched
    assert repo.personas["p-old"]["state"] == "retired"


# ---------------------------------------------------------------------------
# 4 — empty pool: clean refusal, never the old flow
# ---------------------------------------------------------------------------
def test_empty_pool_refuses_cleanly_and_never_falls_back():
    rig = _rig(FakeRepo())
    with pytest.raises(RuntimeError, match="/dress"):
        rig.orchestrator.run_hunt()
    assert rig.publisher.posts == []          # nothing went public
    assert rig.dresser.dressed is False       # old flow never dressed anyone


def test_ready_personas_do_not_leak_into_the_predressed_path():
    """A 'ready' (undressed, unindexed) persona must never be launched."""
    repo = FakeRepo()
    repo.add_persona(id="p-r", handle="@ReadyOne", x_user_id="9", oauth_ref="09",
                     state="ready", phone_verified=True,
                     account_created_at=NOW - timedelta(days=90))
    rig = _rig(repo)
    with pytest.raises(RuntimeError, match="/dress"):
        rig.orchestrator.run_hunt()
    assert repo.personas["p-r"]["state"] == "ready"


# ---------------------------------------------------------------------------
# 1 — R3 fail-closed
# ---------------------------------------------------------------------------
def test_r3_mismatch_refuses_launch_and_alerts():
    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-old", "07", "@ExpressoTitgo", days_dressed=30))
    verifier = FakeVerifier(mismatches=["display name: X diz 'Someone Else'"])
    rig = _rig(repo, verifier=verifier)

    with pytest.raises(RuntimeError, match="R3 mismatch"):
        rig.orchestrator.run_hunt()

    assert rig.publisher.posts == []                          # no Clue 1
    assert repo.hunts == {}                                   # no hunt row
    assert repo.personas["p-old"]["state"] == "dressed"       # persona stays in the pool
    assert any("R3 FALHOU" in m and "/dress" in m for m in rig.notifier.messages)


def test_r3_failure_never_substitutes_the_next_persona_silently():
    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-old", "07", "@ExpressoTitgo", days_dressed=30))
    repo.add_persona(**_dressed_row("p-new", "08", "@NewerOne", days_dressed=5,
                                    name="Newer Persona", code="AAAA2222"))
    verifier = FakeVerifier(mismatches=["bio: código ausente"])
    rig = _rig(repo, verifier=verifier)

    with pytest.raises(RuntimeError):
        rig.orchestrator.run_hunt()

    assert verifier.verified_handles == ["@ExpressoTitgo"]    # ONLY the oldest was tried
    assert repo.personas["p-new"]["state"] == "dressed"


def test_unverifiable_profile_is_a_refusal_not_a_pass():
    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-old", "07", "@ExpressoTitgo", days_dressed=30))
    rig = _rig(repo, verifier=FakeVerifier(error=ConnectionError("X down")))
    with pytest.raises(RuntimeError, match="verification errored"):
        rig.orchestrator.run_hunt()
    assert rig.publisher.posts == []
    assert repo.personas["p-old"]["state"] == "dressed"


def test_missing_verifier_refuses_predressed_launch():
    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-old", "07", "@ExpressoTitgo", days_dressed=30))
    rig = build_simulation(
        repo=repo,
        persona_source=DBPersonaSource(repo, lambda ref: ("t", "s")),
        predressed_launch=True,
        launch_verifier=None,
        persona_generator=BoomGenerator(),
    )
    with pytest.raises(RuntimeError, match="verifier"):
        rig.orchestrator.run_hunt()


# ---------------------------------------------------------------------------
# 2 — R2: the descriptor is consumed, never regenerated
# ---------------------------------------------------------------------------
def test_descriptor_code_and_identity_are_consumed_not_regenerated():
    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-old", "07", "@ExpressoTitgo", days_dressed=30))
    rig = _rig(repo)  # BoomGenerator: any generate() call fails the test

    hunt = rig.orchestrator.run_hunt()

    assert hunt.claim_code == "K7M3P9W2"                      # the persisted code
    assert hunt.identity.display_name == "Cassandra Tired"
    assert rig.dresser.dressed is False                       # nothing re-dressed
    # The winner (ScriptedDMSource) claimed with the code read from the hunt
    # row — i.e. the DESCRIPTOR code validated end-to-end.
    assert rig.payout.sent and repo.winners


def test_integrity_hash_commits_to_the_persisted_code():
    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-old", "07", "@ExpressoTitgo", days_dressed=30))
    rig = _rig(repo)

    hunt = rig.orchestrator.run_hunt()

    expected = compute_integrity_hash("uid-p-old", "K7M3P9W2", hunt.salt)
    assert hunt.integrity_hash == expected
    assert expected in rig.publisher.posts[0]                 # published in Clue 1


def test_clue_context_reads_anchor_posts_from_the_descriptor():
    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-old", "07", "@ExpressoTitgo", days_dressed=30))
    rig = _rig(repo)
    hunt = rig.orchestrator.run_hunt()
    assert hunt.ctx.anchor_posts == [
        "anchor alpha from the descriptor",
        "anchor beta from the descriptor",
    ]
    assert hunt.ctx.display_name == "Cassandra Tired"
    assert hunt.ctx.findable_post == "nobody books a prophet for good news anymore"


# ---------------------------------------------------------------------------
# Prep window is skipped (its job was done weeks ago, at /dress)
# ---------------------------------------------------------------------------
def test_prep_window_is_skipped_for_predressed_hunts():
    class BoomPostEngine:
        def generate(self, identity, n=3):
            raise AssertionError("prep posts generated in a pre-dressed launch")

    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-old", "07", "@ExpressoTitgo", days_dressed=30))
    rig = _rig(repo, prep_window_h=24, persona_post_engine=BoomPostEngine())

    hunt = rig.orchestrator.run_hunt()

    assert hunt.state is HuntState.DONE
    assert rig.dresser.persona_posts == []                    # no prep posts
    # Clue 1 fired immediately: live_at is within minutes of started_at,
    # not 24h later.
    assert (hunt.live_at - hunt.started_at) < timedelta(hours=1)


# ---------------------------------------------------------------------------
# 5 — blindness + state lifecycle
# ---------------------------------------------------------------------------
def test_launch_notifications_never_contain_the_claim_code():
    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-old", "07", "@ExpressoTitgo", days_dressed=30))
    rig = _rig(repo)
    rig.orchestrator.run_hunt()
    launch_msgs = [m for m in rig.notifier.messages if "selecionada do pool" in m]
    assert launch_msgs, "the launch report must exist"
    assert all("K7M3P9W2" not in m for m in launch_msgs)


def test_in_play_transition_keeps_the_descriptor_intact():
    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-old", "07", "@ExpressoTitgo", days_dressed=30))
    rig = _rig(repo)
    rig.orchestrator.run_hunt()
    row = repo.personas["p-old"]
    assert row["claim_code"] == "K7M3P9W2"
    assert row["applied_display_name"] == "Cassandra Tired"
    assert row["locator_post_id"] == "loc-p-old"
    assert row["handle_hint"] == "hint stored at dress time"


def test_young_dress_warns_but_launches():
    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-y", "07", "@ExpressoTitgo", days_dressed=1))
    rig = _rig(repo)
    hunt = rig.orchestrator.run_hunt()
    assert hunt.state is HuntState.DONE
    assert any("indexação" in m for m in rig.notifier.messages)


# ---------------------------------------------------------------------------
# Crash resume: PREPARING (pre-dressed) returns the persona to the pool
# ---------------------------------------------------------------------------
def test_resume_preparing_predressed_releases_persona_without_undress():
    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-old", "07", "@ExpressoTitgo", days_dressed=30))
    # Simulate a crash right after the hunt row was created + persona in_play.
    repo.set_persona_state("p-old", "in_play")
    hunt_id = repo.create_hunt(
        persona_id="p-old", persona_display_name="Cassandra Tired",
        persona_bio="bio", claim_code="K7M3P9W2", integrity_salt="s",
        integrity_hash="h", prize_fmml=500_000, min_balance_fmml=0,
        holding_hours=0, started_at=NOW, state="preparing",
        persona_identity=asdict(_identity()), hunt_number=6, predressed=True,
    )

    rig = _rig(repo)  # the "restarted process"
    rig.orchestrator.resume_hunts()

    assert repo.personas["p-old"]["state"] == "dressed"       # back in the pool
    assert repo.personas["p-old"]["claim_code"] == "K7M3P9W2"  # dress intact
    assert rig.dresser.retired is False                       # NEVER undressed
    assert repo.hunts[hunt_id]["state"] == "done"             # hunt voided/closed
    assert any("dressed pool" in m for m in rig.notifier.messages)


# ---------------------------------------------------------------------------
# Fase 4 — audit trail: every published clue records its planned facet
# (Hunt #5 post-mortem: the facet distribution looked wrong and there was no
# ground truth to check it against)
# ---------------------------------------------------------------------------
def test_every_recorded_clue_carries_facet_and_obliqueness():
    repo = FakeRepo()
    repo.add_persona(**_dressed_row("p-old", "07", "@ExpressoTitgo", days_dressed=30))
    rig = _rig(repo)
    rig.orchestrator.run_hunt()
    assert repo.clues, "the hunt must have recorded clues"
    for row in repo.clues:
        assert row.get("facet"), row
        assert isinstance(row.get("obliqueness"), float), row
    # Clue 1 comes from the ramp's shuffled head: a name word or the avatar.
    first = next(r for r in repo.clues if r["clue_index"] == 1)
    assert first["facet"] in {"name_word_1", "name_word_2", "avatar"}

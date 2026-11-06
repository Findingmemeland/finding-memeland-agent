"""A pista de língua — pedido do Pedro a 05/11, aprovado a 06/11 (D1 (a)).

O QUE ACONTECEU. Na #31 o nome era alemão e nenhuma pista o disse: as cinco
que saíram davam os significados como conceitos ingleses — queixas justas.

MEDIDO A 05/11, nas pistas das 21 hunts de alvo fechadas (#11–#31): NÃO HAVIA
REGRA. Só a #16 (japonês) apontou para a língua, por iniciativa do modelo; a
#21 (francês) foi ganha à pista 2, antes de qualquer pista o poder fazer.

DECIDIDO (Pedro, 06/11):
  · quando o nome não está em inglês, a PRIMEIRA PISTA DA REVELAÇÃO aponta
    para a língua — de lado, sem o nome da língua, o nome dela na própria
    língua nem o país. Ali não correm o adivinho cego nem a guarda de
    pesquisa: não pode ser recusada por ser fácil;
  · o /prepare mede a língua com UMA chamada, só com o nome; o resultado
    fica selado com a preparação (e com a hunt: um reinício mantém-no);
  · o relatório diz "pista de língua: sim / não / não medida (causa)" —
    nunca qual é a língua; uma falha nunca recusa o /prepare;
  · sem medição, a mesma pista recebe a regra como condição ("se o nome
    não for inglês…");
  · o escritor declara a frase que leva a pista ("language_hint"); uma
    guarda mecânica confirma que a frase está na pista e que nenhuma palavra
    óbvia demais está. Uma recusa gasta uma tentativa, rótulo "language".

Nomes e endereços aqui são sintéticos.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest
from test_format_replies import _restart
from test_target_clues import FakeAnthropic
from test_target_prepare import World, _finder, _preparer

from finding_memeland.content.relic_clues import puzzle_clues_of
from finding_memeland.target.clues import (
    TARGET_SYSTEM_PROMPT,
    TargetClueContext,
    TargetClueEngine,
    build_target_user_message,
    parse_target_clue,
)
from finding_memeland.target.dryrun import TargetWorld
from finding_memeland.target.name_language import (
    LANGUAGE_SYSTEM,
    AnthropicNameLanguage,
    NameLanguage,
    NameLanguageUnmeasured,
    language_clue_of,
    language_hint_errors,
    language_note,
    language_rule_for,
    parse_language,
)
from finding_memeland.target.prepare import Candidate, Larder, PreparedStore
from finding_memeland.target.selector import Target

NAME = "Leise Wolke"                       # synthetic, two content words
GERMAN = NameLanguage(english=False, language="German",
                      obvious=("german", "Deutsch", "Germany", "Deutschland"))
ENGLISH = NameLanguage(english=True)


def _target(name: str = NAME) -> Target:
    return Target(chain="ethereum", contract="0x" + "ab" * 20, token_id=7, name=name,
                  name_onchain=name, description="d", image="ipfs://x",
                  metadata_sha256="ab" * 32, epoch="e1")


def _ctx(lang=GERMAN, name: str = NAME) -> TargetClueContext:
    return TargetClueContext.from_target(_target(name), image_description="a cloud",
                                         name_language=lang)


class _Draft:
    def __init__(self, text, hint=None):
        self.text, self.language_hint = text, hint


# --------------------------------------------------------------------------- #
# The measurement at /prepare                                                  #
# --------------------------------------------------------------------------- #


class _Blk:
    def __init__(self, text):
        self.text = text


class _Answer:
    def __init__(self, text):
        self.content = [_Blk(text)]


class FakeModel:
    """Scripted answers: a dict (sent as JSON), a str (sent as-is) or an
    exception (raised)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

        class _M:
            def create(_s, **kw):
                self.calls.append(kw)
                a = self.answers.pop(0)
                if isinstance(a, BaseException):
                    raise a
                return _Answer(json.dumps(a) if isinstance(a, dict) else a)
        self.messages = _M()


def test_the_measurement_sends_the_name_and_nothing_else():
    model = FakeModel({"english": False, "language": "German", "obvious": ["german"]})
    lang = AnthropicNameLanguage(model, "m")(NAME)
    assert lang == NameLanguage(english=False, language="German", obvious=("german",))
    (call,) = model.calls
    assert call["messages"] == [{"role": "user", "content": f"TITLE: {NAME}"}]
    assert call["system"] == LANGUAGE_SYSTEM


def test_an_english_name_has_nothing_to_ban():
    lang = AnthropicNameLanguage(FakeModel("ok: {\"english\": true}"), "m")("Salt Harbor")
    assert lang == ENGLISH and lang.banned() == ()


class _ApiError(Exception):
    status_code = 529


@pytest.mark.parametrize("answers, kind", [
    (("no json here", "still none"), "ValueError"),
    (({"english": False}, {"english": "no"}), "ValueError"),     # no language / no bool
    ((_ApiError(f"overloaded: {NAME}"), _ApiError(f"overloaded: {NAME}")), "_ApiError HTTP 529"),
])
def test_a_failed_measurement_says_its_kind_and_never_the_name(answers, kind):
    model = FakeModel(*answers)
    with pytest.raises(NameLanguageUnmeasured) as err:
        AnthropicNameLanguage(model, "m", sleep=lambda _s: None)(NAME)
    assert err.value.kind == kind and NAME not in str(err.value)
    assert len(model.calls) == 2                                # two tries


def test_one_blip_is_retried():
    model = FakeModel(_ApiError("x"), {"english": True})
    assert AnthropicNameLanguage(model, "m", sleep=lambda _s: None)(NAME) == ENGLISH


def test_the_banned_words_are_the_language_and_the_obvious_ones_normalised():
    lang = parse_language({"english": False, "language": "French",
                           "obvious": ["français", "France", "french", "", 7, "FRENCH"]})
    assert lang.banned() == ("french", "francais", "france")
    assert len(parse_language({"english": False, "language": "x",
                               "obvious": [f"w{i}" for i in range(30)]}).obvious) == 12


@pytest.mark.parametrize("lang, cause, note", [
    (GERMAN, "", "sim"), (ENGLISH, "", "não"), (None, "ValueError", "não medida (ValueError)"),
])
def test_the_report_words_never_name_the_language(lang, cause, note):
    assert language_note(lang, cause) == note


def _prep(world, **kw):
    return _preparer(world, _finder(world), **kw)


def _store():
    """A PreparedStore over a list, with a cipher that does nothing."""
    blob = [None]
    plain = type("Plain", (), {"encrypt": staticmethod(lambda p: p),
                               "decrypt": staticmethod(lambda p: p)})()
    return PreparedStore(cipher=plain, read=lambda: blob[0],
                         write=lambda b: blob.__setitem__(0, b)), blob


def _one(name="Some Two Words") -> Larder:
    meta = {"name": name, "image": "ipfs://img1", "description": "d"}
    return Larder(candidates=[Candidate(
        chain="ethereum", contract="0x" + "cd" * 20, token_id=1, name=name,
        name_onchain=name, description="d", image="ipfs://img1",
        token_uri="ipfs://Qm1", artist="", metadata=meta)])


def test_prepare_measures_once_with_the_name_and_seals_it():
    """The name the clues cipher — without the serial the chain adds."""
    asked = []

    def measure(name):
        asked.append(name)
        return GERMAN
    world = World(names={1: "Some Two Words #3"})
    prepared, _ = _prep(world, name_language=measure).prepare(_one())
    assert prepared.target.name_onchain == "Some Two Words #3"
    assert asked == ["Some Two Words"]
    assert prepared.name_language == GERMAN and prepared.language_note == "sim"
    store, _blob = _store()
    store.save(prepared)
    assert store.load().name_language == GERMAN


@pytest.mark.parametrize("measure, note", [
    (None, "não medida (sem modelo)"),
    (lambda _n: (_ for _ in ()).throw(NameLanguageUnmeasured("RateLimitError HTTP 429")),
     "não medida (RateLimitError HTTP 429)"),
    (lambda n: (_ for _ in ()).throw(RuntimeError(f"echo {n}")), "não medida (RuntimeError)"),
    (lambda _n: "German", "não medida (resposta-malformada)"),
])
def test_an_unmeasured_language_never_refuses_the_prepare(measure, note):
    prepared, larder = _prep(World(), name_language=measure).prepare(_one())
    assert prepared.name_language is None
    assert prepared.language_note == note
    assert larder.size() == 0                                    # sealed all the same


def test_a_candidate_refused_before_clue_one_costs_no_call():
    asked = []
    world = World()
    real = world.write_clue_one
    tries = []

    def clue_one(target, description):
        tries.append(1)
        if len(tries) == 1:
            raise RuntimeError("guards exhausted")
        return real(target, description)
    world.write_clue_one = clue_one
    larder = _one()
    larder.candidates.append(replace(larder.candidates[0], token_id=2,
                                     image="ipfs://img2", token_uri="ipfs://Qm2"))
    _prep(world, name_language=lambda n: asked.append(n) or ENGLISH).prepare(larder)
    assert len(tries) == 2 and len(asked) == 1


def test_a_preparation_from_before_loads_without_a_measurement():
    store, blob = _store()
    prepared, _ = _prep(World()).prepare(_one())
    store.save(prepared)
    assert "name_language" not in json.loads(blob[0])
    assert store.load().name_language is None


def test_the_prepare_reply_carries_the_note():
    from finding_memeland.target.wiring import TargetWiring

    class _St:
        def load(self):
            return Larder()

        def save(self, obj):
            pass

    class _Prep:
        def prepare(self, larder):
            return type("P", (), {"attempts": 1, "rarity_note": "", "creator_note": "",
                                  "language_note": "sim"})(), larder
    w = TargetWiring(ports=None, pipeline=None, epoch=None, snapshot_store=None,
                     scan_blocks=0, writability_rates={}, uniqueness_rates={},
                     larder_store=_St(), prepared_store=_St(), larder_preparer=_Prep())
    assert w.prepare().endswith(" · pista de língua: sim")


def test_production_wires_the_measurement():
    import inspect

    from finding_memeland.target import wiring
    assert ("name_language=AnthropicNameLanguage(anthropic, s.target_judge_model)"
            in inspect.getsource(wiring))


# --------------------------------------------------------------------------- #
# Launch and restart                                                           #
# --------------------------------------------------------------------------- #


def _launched_with(lang):
    w = TargetWorld()
    w._take_prepared()
    w.reseal(replace(w.prepared_slot, name_language=lang))
    return w, w.launch()


def test_the_launch_carries_the_measurement_and_a_restart_keeps_it():
    w, hunt = _launched_with(GERMAN)
    assert hunt.ctx.name_language == GERMAN
    row = w.rig.repo.hunts[hunt.id]
    sealed = json.loads(w.ports.cipher._cipher.decrypt(row["target_ctx_sealed"]))
    assert sealed["name_language"] == GERMAN.to_doc()
    assert _restart(w, hunt).ctx.name_language == GERMAN


def test_a_hunt_sealed_before_restarts_unmeasured():
    w, hunt = _launched_with(None)
    row = w.rig.repo.hunts[hunt.id]
    sealed = json.loads(w.ports.cipher._cipher.decrypt(row["target_ctx_sealed"]))
    assert "name_language" not in sealed
    assert hunt.ctx.name_language is None and _restart(w, hunt).ctx.name_language is None


# --------------------------------------------------------------------------- #
# The clue                                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name, puzzle", [("Wolke", 4), (NAME, 6), ("Leise Wolke Nacht", 8)])
def test_the_hint_goes_on_the_first_clue_of_the_reveal(name, puzzle):
    ctx = _ctx(name=name)
    assert puzzle_clues_of(ctx) == puzzle
    assert language_clue_of(ctx) == puzzle + 1


def test_the_writer_is_told_on_that_clue_and_on_no_other():
    ctx = _ctx()
    k = language_clue_of(ctx)
    msg = build_target_user_message(ctx, k, ["c"] * (k - 1))
    assert ("LANGUAGE OF THE NAME: the name is in German, not English" in msg
            and "german, deutsch, germany, deutschland" in msg
            and '"language_hint"' in msg)
    for i in (1, k - 1, k + 1):
        assert "LANGUAGE OF THE NAME" not in build_target_user_message(ctx, i, ["c"] * (i - 1))


def test_an_english_name_gets_no_rule_and_an_unmeasured_one_gets_the_condition():
    k = language_clue_of(_ctx())
    assert language_rule_for(_ctx(ENGLISH), k) == ""
    rule = language_rule_for(_ctx(None), k)
    assert rule.startswith("LANGUAGE OF THE NAME: if the name is NOT in English")
    assert "If the name is in English, \"language_hint\" is null." in rule


def test_the_writer_declares_the_phrase():
    spec = TARGET_SYSTEM_PROMPT.format(index=7, obliqueness=0.4, hard_floor=0.7,
                                       phase_rules="")
    assert '"language_hint": "<phrase or null>"' in spec
    assert parse_target_clue(json.dumps({"clue": "c", "language_hint": "where kafka wrote"})
                             ).language_hint == "where kafka wrote"
    assert parse_target_clue(json.dumps({"clue": "c", "language_hint": None})).language_hint is None
    assert parse_target_clue(json.dumps({"clue": "c"})).language_hint is None


# --------------------------------------------------------------------------- #
# The guard                                                                     #
# --------------------------------------------------------------------------- #

GOOD = "the word kafka's readers would know — where kafka wrote, no translation needed"


@pytest.mark.parametrize("text, hint, wrong", [
    (GOOD, "where kafka wrote", None),
    (GOOD, "Where Kafka wrote,", None),                       # case and punctuation
    (GOOD, None, "none was declared"),
    (GOOD, "where goethe wrote", "is not in the clue"),
    ("German readers know it — where kafka wrote", "where kafka wrote", "too plainly"),
    ("the GERMANS say it — where kafka wrote", "where kafka wrote", "too plainly"),
    ("germany's own — where kafka wrote", "where kafka wrote", "too plainly"),
    ("say it in deutsch — where kafka wrote", "where kafka wrote", "too plainly"),
    ("a germane point — where kafka wrote", "where kafka wrote", None),   # a word, not the word
])
def test_the_guard_on_the_designated_clue(text, hint, wrong):
    ctx = _ctx()
    errors = language_hint_errors(_Draft(text, hint), ctx, language_clue_of(ctx))
    if wrong is None:
        assert errors == []
    else:
        assert any(wrong in e for e in errors), errors


def test_accents_and_scripts_are_read_the_same():
    lang = NameLanguage(english=False, language="French", obvious=("français", "日本語"))
    ctx = _ctx(lang)
    k = language_clue_of(ctx)
    for text in ("parlez-vous Francais? — on the seine", "日本語 — on the seine"):
        assert language_hint_errors(_Draft(text, "on the seine"), ctx, k)


def test_no_check_on_other_clues_nor_on_an_english_name():
    ctx = _ctx()
    k = language_clue_of(ctx)
    loud = _Draft("German, plainly", None)
    assert language_hint_errors(loud, ctx, k - 1) == []
    assert language_hint_errors(loud, ctx, k + 1) == []
    assert language_hint_errors(loud, _ctx(ENGLISH), k) == []


def test_unmeasured_only_what_was_declared_is_checked():
    ctx = _ctx(None)
    k = language_clue_of(ctx)
    assert language_hint_errors(_Draft(GOOD, None), ctx, k) == []
    assert language_hint_errors(_Draft(GOOD, "where kafka wrote"), ctx, k) == []
    assert language_hint_errors(_Draft(GOOD, "somewhere else"), ctx, k)


def _draft(text, hint):
    return {"clue": text, "taunt": "still nothing?", "angle": None,
            "image_aspect": None, "claims": [], "language_hint": hint}


def test_the_engine_refuses_counts_it_as_language_and_writes_again():
    ctx = _ctx()
    k = language_clue_of(ctx)
    e = TargetClueEngine(FakeAnthropic([_draft(GOOD, None), _draft(GOOD, "where kafka wrote")]),
                         "model", search_guard=False, truth_judge=False, solver=False)
    d = e.next_clue(ctx, k, ["c"] * (k - 1))
    assert d.language_hint == "where kafka wrote"
    assert e.last_attempt_report == (2, {"language": 1})
    feedback = e._client.calls[1]["messages"][0]["content"]
    assert "LANGUAGE of the name" in feedback

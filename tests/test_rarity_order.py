"""A ordem por raridade (Pedro, 28/10).

MEDIDO a 28/10 nas #17–#26 (46 pesquisas, cada palavra sozinha, sem filtro e
com a cadeia): as três hunts resolvidas na pista 1 ou 2 por uma palavra de
dicionário (#19, #21, #26) tinham, nas duas primeiras pistas, uma palavra
cuja pesquisa SOZINHA chega ao alvo. Decisões do Pedro:

  · no /prepare, cada palavra de conteúdo é pesquisada sozinha; as que
    chegam ao alvo vão para o FIM do puzzle, a mais fácil de encontrar em
    último. O puzzle fica igual — só muda a ordem;
  · D1: "chega ao alvo" conta também com o filtro da cadeia, mas abaixo da
    pesquisa sem filtro;
  · D2: se a pesquisa falhar, fica a ordem de hoje e o relatório diz
    "ordem por raridade: não medida (<causa>)" — nunca recusa o /prepare;
  · uma preparação feita com o código antigo (sem a ordem) continua a
    funcionar depois do push, com a ordem de hoje.
"""
from __future__ import annotations

import inspect
import json
import urllib.error
from dataclasses import replace

import pytest
from test_target_prepare import World, _finder, _preparer

from finding_memeland.content.name_words import content_words
from finding_memeland.content.relic_clues import target_ramp_plan
from finding_memeland.target.clues import TargetClueContext
from finding_memeland.target.dryrun import TargetWorld
from finding_memeland.target.integration import resume_target_hunt
from finding_memeland.target.prepare import (
    Larder,
    PreparedStore,
    RarityUnmeasured,
    rare_last_from,
    rare_last_of,
)
from finding_memeland.target.search_guard import OpenSeaSearch
from finding_memeland.target.selector import Target
from finding_memeland.target.wiring import word_reach_for

# nomes já revelados (públicos) e nomes sintéticos — nunca um alvo vivo
REVEALED = ["The Veiled Firekeeper", "Le souffle coupé", "Comrade Kamala",
            "Golden Intuition", "Royal Highness Flashyflow N", "Chasing the Doge"]


def word_slots(plan) -> list[str]:
    return [f for f, _o in plan if f != "image"]


def art_at(plan) -> list[int]:
    return [i for i, (f, _o) in enumerate(plan, 1) if f == "image"]


# --------------------------------------------------------------------------- #
# 1. O plano                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", REVEALED + ["Whispering Harbor", "Salt Harbor"])
def test_without_an_order_the_plan_is_the_one_of_before(name):
    assert target_ramp_plan(name) == target_ramp_plan(name, rare_last=None) \
        == target_ramp_plan(name, rare_last=())


def test_hunt_26_would_have_opened_on_the_common_word():
    """Medido na base: a pista 1 da #26 foi a palavra 3, Firekeeper, que
    chega ao alvo sozinha. Com ela no fim, a pista 1 é a palavra 2."""
    name = "The Veiled Firekeeper"
    assert target_ramp_plan(name)[0][0] == "name_word_3"
    plan = target_ramp_plan(name, rare_last=(3,))
    assert plan[0][0] == "name_word_2"
    assert word_slots(plan) == ["name_word_2", "name_word_2", "name_word_3", "name_word_3"]


@pytest.mark.parametrize("name, rare", [
    ("The Veiled Firekeeper", (3,)), ("Le souffle coupé", (2, 3)),
    ("Comrade Kamala", (1, 2)), ("Golden Intuition", (2,)),
    ("Royal Highness Flashyflow N", (3,)), ("Whispering Harbor", (2,)),
])
def test_the_puzzle_is_the_same_puzzle_only_the_order_changes(name, rare):
    before, after = target_ramp_plan(name), target_ramp_plan(name, rare_last=rare)
    assert art_at(after) == art_at(before)                       # the art stays put
    assert [o for _f, o in after] == [o for _f, o in before]     # the same curve
    assert sorted(word_slots(after)) == sorted(word_slots(before))   # every piece kept
    assert after[0][0] != "image"                                # clue 1: a word
    rare_facets = [f"name_word_{p}" for p in rare]
    tail = word_slots(after)[-2 * len(rare):]
    assert tail == [f for f in rare_facets for _ in range(2)]    # the rare ones last, in order
    common_before = [f for f in word_slots(before) if f not in rare_facets]
    assert word_slots(after)[:len(common_before)] == common_before   # the rest as seeded


@pytest.mark.parametrize("bad", [(9,), (2, 9), (3, 9), (0,), ("x",)])
def test_an_order_that_does_not_fit_the_name_moves_nothing(bad):
    name = "Chasing the Doge"          # word 2, "the", is not a content word
    try:
        plan = target_ramp_plan(name, rare_last=bad)
    except ValueError:
        pytest.fail("an unreadable order must not break the plan")
    assert plan == target_ramp_plan(name)
    assert target_ramp_plan(name, rare_last=(2,)) == target_ramp_plan(name)


def test_a_repeated_position_counts_once():
    name = "The Veiled Firekeeper"
    assert target_ramp_plan(name, rare_last=(3, 3)) == target_ramp_plan(name, rare_last=(3,))
    three = "Le souffle coupé"
    assert target_ramp_plan(three, rare_last=(2, 2, 3)) == target_ramp_plan(three, rare_last=(2, 3))


# --------------------------------------------------------------------------- #
# 2. Quem vai para o fim                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("reach, rare", [
    # os números de 28/10: (posição sem filtro, posição com a cadeia)
    ({2: (None, None), 3: (35, 2)}, (3,)),                     # #26 Veiled / Firekeeper
    ({1: (None, None), 2: (2, 2), 3: (1, 1)}, (2, 3)),         # #21 Le / souffle / coupé
    ({1: (None, 49), 2: (None, 21)}, (1, 2)),                  # #22 só com a cadeia
    ({1: (None, 8), 2: (None, 19)}, (2, 1)),                   # #25 Historical o mais fácil
    ({1: (None, None), 2: (5, 5)}, (2,)),                      # #19 Intuition
    ({1: (None, None), 3: (None, None)}, ()),                  # #17 nenhuma
    # D1: sem filtro vale mais do que com a cadeia, mesmo numa posição pior
    ({1: (40, None), 2: (None, 1)}, (2, 1)),
])
def test_the_most_findable_word_goes_last(reach, rare):
    assert rare_last_from(reach) == rare


@pytest.mark.parametrize("raw, read", [
    ([3], (3,)), ((2, 3), (2, 3)), (None, ()), ([], ()), ("3", ()), (["x"], ()),
    ([0], ()), ([-1, 2], ()), (5, ()),
])
def test_a_stored_order_is_read_defensively(raw, read):
    assert rare_last_of(raw) == read


# --------------------------------------------------------------------------- #
# 3. No /prepare                                                                #
# --------------------------------------------------------------------------- #

NAME = "Veiled Firekeeper"                      # sintético, a partir de um revelado


class RecordingWorld(World):
    def __init__(self, **kw):
        super().__init__(default_name=NAME, **kw)
        self.clue_one_kwargs: list[dict] = []

    def write_clue_one(self, target, description, **kw):
        self.clue_one_kwargs.append(kw)
        return super().write_clue_one(target, description)


def _prepare(word_reach, world=None):
    world = world or RecordingWorld()
    finder = _finder(world)
    larder = Larder()
    finder.fill(larder, want=1, max_draws=40)
    prepared, _left = _preparer(world, finder, word_reach=word_reach).prepare(larder)
    return world, prepared


def test_the_rare_word_goes_last_and_clue_1_is_written_in_that_order():
    asked = []

    def reach(word, target):
        asked.append(word)
        return {"Veiled": (None, None), "Firekeeper": (35, 2)}[word]
    world, prepared = _prepare(reach)
    assert sorted(asked) == ["Firekeeper", "Veiled"]        # one call a word
    assert prepared.rare_last == (2,)
    assert prepared.rarity_note == "1 de 2 palavras chega ao alvo sozinha — vai para o fim"
    assert world.clue_one_kwargs == [{"rare_last": (2,)}]


def test_no_rare_word_keeps_the_order_and_says_so():
    world, prepared = _prepare(lambda word, target: (None, None))
    assert prepared.rare_last == ()
    assert prepared.rarity_note == "nenhuma das 2 palavras chega ao alvo sozinha — ordem de sempre"
    assert world.clue_one_kwargs == [{}]                    # the call of before


def test_two_rare_words_are_both_counted():
    _w, prepared = _prepare(lambda word, target: (3, 3))
    assert prepared.rare_last == (1, 2)
    assert prepared.rarity_note == "2 de 2 palavras chegam ao alvo sozinhas — vão para o fim"


@pytest.mark.parametrize("error, cause", [
    (RarityUnmeasured("503"), "503"),
    (RarityUnmeasured("timeout"), "timeout"),
    (RuntimeError(f"GET https://api.example/search?query={NAME.split()[1]} failed"),
     "RuntimeError"),
])
def test_a_failed_search_never_refuses_the_prepare(error, cause):
    """D2: a ordem de hoje, e o relatório diz porquê — nunca a palavra."""
    def reach(word, target):
        raise error
    world, prepared = _prepare(reach)
    assert prepared.target.name_onchain                       # sealed all the same
    assert prepared.rare_last == ()
    assert prepared.rarity_note == f"não medida ({cause})"
    assert "Firekeeper" not in prepared.rarity_note and "Veiled" not in prepared.rarity_note
    assert world.clue_one_kwargs == [{}]


def test_without_a_search_port_nothing_is_measured():
    world, prepared = _prepare(None)
    assert (prepared.rare_last, prepared.rarity_note) == (
        (), "não medida (sem pesquisa com posições)")
    assert world.clue_one_kwargs == [{}]


def test_a_name_with_one_content_word_asks_nothing():
    """Um nome de UMA palavra nunca entra na despensa (2+ palavras); o caso é
    uma só palavra de conteúdo."""
    asked = []
    world = RecordingWorld()
    world.default_name = "The Firekeeper"
    _w, prepared = _prepare(lambda word, target: asked.append(word) or (1, 1), world)
    assert asked == []
    assert prepared.rarity_note == "uma palavra só — nada a ordenar"


# --------------------------------------------------------------------------- #
# 4. A preparação guardada — a nova e a antiga                                 #
# --------------------------------------------------------------------------- #


class _Cipher:
    def encrypt(self, s):
        return "E" + s

    def decrypt(self, s):
        return s[1:]


def _store(blob: dict) -> PreparedStore:
    return PreparedStore(cipher=_Cipher(), read=lambda: blob.get("v"),
                         write=lambda p: blob.__setitem__("v", p))


def test_the_order_survives_the_store():
    _w, prepared = _prepare(lambda word, target: {"Veiled": (None, None),
                                                  "Firekeeper": (35, 2)}[word])
    blob: dict = {}
    _store(blob).save(prepared)
    back = _store(blob).load()
    assert (back.rare_last, back.rarity_note) == (prepared.rare_last, prepared.rarity_note)


def test_a_preparation_written_by_the_old_code_still_loads_in_the_old_order():
    """O blob que o código de antes escreve: sem as duas chaves, versão 1."""
    _w, prepared = _prepare(None)
    blob: dict = {}
    _store(blob).save(prepared)
    doc = json.loads(blob["v"][1:])
    doc.pop("rare_last")
    doc.pop("rarity_note")
    blob["v"] = "E" + json.dumps(doc)
    back = _store(blob).load()
    assert back.rare_last == () and back.rarity_note == ""
    assert back.clue_one is not None and back.target.name == prepared.target.name


def test_a_garbled_order_is_no_order_not_an_unreadable_preparation():
    _w, prepared = _prepare(None)
    blob: dict = {}
    _store(blob).save(prepared)
    doc = json.loads(blob["v"][1:])
    doc["rare_last"] = ["Firekeeper"]
    blob["v"] = "E" + json.dumps(doc)
    assert _store(blob).load().rare_last == ()


# --------------------------------------------------------------------------- #
# 5. Lançamento e retoma                                                        #
# --------------------------------------------------------------------------- #


def _launched(rare_last):
    w = TargetWorld()
    prepared = w._take_prepared()
    w.reseal(replace(prepared, rare_last=rare_last))
    hunt = w.launch()
    return w, hunt, prepared.target.name


def _sealed_ctx(w, hunt) -> dict:
    row = w.rig.repo.hunts[hunt.id]
    return json.loads(w.ports.cipher._cipher.decrypt(str(row["target_ctx_sealed"])))  # noqa: SLF001


def test_a_launch_runs_on_the_sealed_order_and_a_restart_rebuilds_it():
    w, hunt, name = _launched((2,))
    ordered = target_ramp_plan(name, rare_last=(2,))
    assert ordered != target_ramp_plan(name)                  # the order matters here
    assert hunt.ctx.clue_facet_plan == ordered
    assert _sealed_ctx(w, hunt)["rare_last"] == [2]
    row = dict(w.rig.repo.hunts[hunt.id])
    resume_target_hunt(w.orch, row, hunt)
    assert hunt.ctx.clue_facet_plan == ordered


def test_an_old_preparation_launches_and_resumes_in_the_order_of_before():
    """O pedido do Pedro: preparada com o código antigo, lançada depois do
    push — a ordem de hoje, com a Clue 1 que já tinha."""
    w, hunt, name = _launched(())
    assert hunt.ctx.clue_facet_plan == target_ramp_plan(name)
    assert "rare_last" not in _sealed_ctx(w, hunt)
    row = dict(w.rig.repo.hunts[hunt.id])
    resume_target_hunt(w.orch, row, hunt)
    assert hunt.ctx.clue_facet_plan == target_ramp_plan(name)


def test_a_hunt_row_from_before_resumes_in_the_order_of_before():
    w, hunt, name = _launched(())
    row = dict(w.rig.repo.hunts[hunt.id])
    row["target_ctx_sealed"] = w.ports.cipher._cipher.encrypt(  # noqa: SLF001
        json.dumps({"image_description": "a picture"}))
    resume_target_hunt(w.orch, row, hunt)
    assert hunt.ctx.clue_facet_plan == target_ramp_plan(name)
    assert hunt.ctx.image_description == "a picture"


# --------------------------------------------------------------------------- #
# 6. A pesquisa e o relatório                                                   #
# --------------------------------------------------------------------------- #

T = Target(chain="base", contract="0x" + "cd" * 20, token_id=7, name="The Veiled Firekeeper",
           name_onchain="The Veiled Firekeeper", description="", image="ipfs://x",
           metadata_sha256="ab" * 32, epoch="e1")
TID = T.id().upper()
OTHER = "BASE:0X" + "EF" * 20 + ":1"


def _row(item_id: str) -> dict:
    chain, contract, ident = item_id.lower().split(":")
    return {"type": "nft", "nft": {"identifier": ident, "contract": contract,
                                   "opensea_url": f"https://opensea.io/assets/{chain}/{contract}/{ident}"}}


def test_ranked_ids_keeps_the_order_and_filters_by_chain():
    urls = []

    def http_get(url, headers):
        urls.append(url)
        return json.dumps({"results": [_row(OTHER), _row(TID)]})
    s = OpenSeaSearch(http_get=http_get, api_key="k")
    assert s.ranked_ids("Firekeeper") == [OTHER, TID]
    assert "chains=" not in urls[-1]
    assert s.ranked_ids("Firekeeper", chain="base") == [OTHER, TID]
    assert urls[-1].endswith("&chains=base")
    with pytest.raises(ValueError):
        s.ranked_ids("Firekeeper", chain="solana")


def test_word_reach_says_where_the_target_sits_with_and_without_the_chain():
    class Market:
        def ranked_ids(self, text, *, chain=None):
            return [OTHER, TID] if chain else [TID]
    assert word_reach_for(Market())("Firekeeper", T) == (1, 2)

    class Nowhere:
        def ranked_ids(self, text, *, chain=None):
            return [OTHER]
    assert word_reach_for(Nowhere())("Veiled", T) == (None, None)


def test_a_failed_search_travels_as_its_kind_never_its_url():
    class Down:
        def ranked_ids(self, text, *, chain=None):
            raise urllib.error.HTTPError(f"https://api/search?query={text}", 503,
                                         "Service Unavailable", None, None)
    with pytest.raises(RarityUnmeasured) as e:
        word_reach_for(Down())("Firekeeper", T)
    assert e.value.kind == "503" and "Firekeeper" not in str(e.value)


def test_a_surface_without_ranked_results_measures_nothing():
    assert word_reach_for(object()) is None


def test_the_report_carries_the_note_and_never_a_word():
    from finding_memeland.target.wiring import TargetWiring

    class _St:
        def load(self):
            return Larder()

        def save(self, obj):
            pass

    class _Prep:
        def prepare(self, larder):
            return type("P", (), {"attempts": 1, "rarity_note": "não medida (503)"})(), larder
    w = TargetWiring(ports=None, pipeline=None, epoch=None, snapshot_store=None,
                     scan_blocks=0, writability_rates={}, uniqueness_rates={},
                     larder_store=_St(), prepared_store=_St(), larder_preparer=_Prep())
    assert w.prepare().endswith(" · ordem por raridade: não medida (503)")


def test_the_clue_one_writer_and_the_preparer_are_wired_to_the_order():
    from finding_memeland.target import wiring
    src = inspect.getsource(wiring)
    assert "rare_last=rare_last or None)" in src
    assert "word_reach=word_reach_for(market))" in src


def test_the_context_carries_the_order_into_its_plan():
    ctx = TargetClueContext.from_target(T, image_description="a picture", rare_last=(3,))
    assert ctx.clue_facet_plan == target_ramp_plan(T.name, rare_last=(3,))
    assert TargetClueContext.from_target(T, image_description="a picture").clue_facet_plan \
        == target_ramp_plan(T.name)


def test_the_words_searched_are_the_content_words_as_typed():
    """A #24 traz aspas; "the" nunca é pesquisada."""
    from finding_memeland.target.prepare import _searched_as
    assert [_searched_as(w.text) for w in content_words('"Lil Peep" Rapperz')] == \
        ["Lil", "Peep", "Rapperz"]
    assert [w.text for w in content_words("Chasing the Doge")] == ["Chasing", "Doge"]

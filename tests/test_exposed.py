"""A lista de queimados (09/10, decisão do Pedro) — no código, "expostos".

A 09/10 encontraram-se catorze ficheiros de dados versionados no repositório
público: uma short-list, as amostras dos primeiros censos e quatro capturas
de medição. Saem do repositório daqui para a frente (`git rm --cached`), mas
o histórico fica — e quem o leu ficou com uma lista. A regra:

    qualquer contrato:tokenId que tenha estado num ficheiro versionado do
    repositório público nunca pode ser depositado nem sorteado.

O que estes testes fixam:
  1. o que conta como "um par escrito num ficheiro" (texto, campos de JSON);
  2. a lista guarda resumos, nunca pares, e tem canário: uma lista que não
     se consegue ler RECUSA, não aprova;
  3. o construtor lê o histórico inteiro e só imprime contagens;
  4. o sorteio e o depósito recusam um exposto antes de qualquer leitura;
  5. o /prepare retira-os da despensa antes de sortear;
  6. o /launch recusa uma preparação que seja de um exposto;
  7. o que está versionado: nenhum ficheiro de candidatos, e os dados que os
     testes precisam (tests/fixtures) estão todos na lista.

"Queimado" já quer dizer outra coisa aqui (ownerOf reverte), por isso a
causa nos relatórios é "exposto". Os pares destes testes são inventados.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_target_launch_prepared import TargetWorld
from test_target_prepare import S, World, _finder, _preparer
from test_target_wiring import FakeRepo, rpc_ok, settings

from finding_memeland import main
from finding_memeland.target import exposed as exposed_mod
from finding_memeland.target.exposed import (
    CANARY,
    DEFAULT_PATH,
    DIGEST_HEX,
    ExposedList,
    ExposedListBlind,
    exposed_digest,
    pairs_in_blob,
    pairs_in_json,
    pairs_in_text,
    render_digests,
)
from finding_memeland.target.hunt import LaunchRefused
from finding_memeland.target.prepare import (
    Candidate,
    Larder,
    PrepareRefused,
    Tally,
)
from finding_memeland.target.probe import ProbeColumn, cause_of
from finding_memeland.target.wiring import TargetWiring, build_target

ROOT = Path(__file__).resolve().parents[1]
A = "0x" + "a1" * 20
B = "0x" + "b2" * 20
OWNER = "0x" + "0e" * 20
PAIRS = {(A, 7), (B, 12)}


# --------------------------------------------------------------------------- #
# 1. O que conta como um par escrito num ficheiro                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", [
    f"base:{A}:7", f"ethereum:{A.upper().replace('0X', '0x')}:7", f"{A}:7", f"{A} : 7",
    f"https://opensea.io/item/ethereum/{A}/7", f"https://rarible.com/token/{A}:7",
    f"https://etherscan.io/nft/{A}/7", f"https://etherscan.io/token/{A}?a=7",
    f"https://x.example/c/{A}?chain=1&tokenId=7", f"see {A}/7.",
    f"ETHEREUM:{A}:7",
])
def test_a_pair_in_running_text(text):
    assert pairs_in_text(text) == {(A, 7)}


@pytest.mark.parametrize("text", [
    A, f"the contract {A} has many pieces", f"{A}:x7", "0x" + "ab" * 32 + ":7",
    f"{A[:-1]}:7", "", "nothing here", f"owner {OWNER}",
])
def test_a_contract_without_a_token_is_not_a_pair(text):
    assert pairs_in_text(text) == set()


def test_a_json_object_with_a_contract_and_a_token():
    for key in ("tokenId", "token_id", "identifier", "TokenID"):
        doc = [{"contract": A, key: 7, "owner": OWNER, "name": "x"},
               {"contract": B, key: "12", "owner": OWNER}]
        assert pairs_in_json(doc) == PAIRS
    assert pairs_in_json({"asset_contract": {"address": A}, "token_id": 7}) == {(A, 7)}


def test_the_token_fields_below_a_contract_are_that_contract_s():
    doc = {"platform": {"contract": A, "tokens": [{"tokenId": 7, "tokenURI": "ipfs://x"},
                                                  {"tokenId": 8, "status": {"ok": True}}]},
           "other": {"contract": B, "tokens": [{"tokenId": 12}]}}
    assert pairs_in_json(doc) == {(A, 7), (A, 8), (B, 12)}


def test_a_two_element_list_is_a_pair():
    doc = {"h": {"len": 3, "contracts": [A, B], "mints": 9, "examples": [[A, 7], [B, 12]]}}
    assert pairs_in_json(doc) == PAIRS


def test_an_owner_is_never_taken_for_a_contract():
    doc = [{"owner": OWNER, "creator": OWNER, "wallet": OWNER, "tokenId": 7},
           {"owner": OWNER, "contract": A, "tokenId": 7}]
    assert pairs_in_json(doc) == {(A, 7)}


@pytest.mark.parametrize("doc", [
    {"contract": A}, {"contracts": [A, B]}, {"contract": A, "tokenId": None},
    {"contract": A, "tokenId": True}, {"contract": A, "tokenId": -1},
    {"contract": "not an address", "tokenId": 7}, {"tokenId": 7}, [], {}, 7, "x",
])
def test_what_is_not_a_piece_gives_nothing(doc):
    assert pairs_in_json(doc) == set()


def test_a_file_exposes_its_text_and_its_fields():
    raw = json.dumps({"items": [{"contract": A, "tokenId": 7}],
                      "note": f"also {B}:12"}).encode()
    assert pairs_in_blob(raw) == PAIRS
    assert pairs_in_blob(f"base:{A}:7\nbase:{B}:12\n".encode()) == PAIRS
    assert pairs_in_blob(b"\x89PNG\r\n\x1a\n\x00\x00" + f"{A}:7".encode()) == set()
    assert pairs_in_blob(b"{ not json " + f"{A}:7".encode()) == {(A, 7)}


# --------------------------------------------------------------------------- #
# 2. A lista: resumos, nunca pares — e com canário                              #
# --------------------------------------------------------------------------- #


def test_the_digest_is_of_contract_and_token_whatever_the_case_or_the_type():
    d = exposed_digest(A, 7)
    assert len(d) == DIGEST_HEX == 16 and int(d, 16) >= 0
    assert d == exposed_digest(A.upper().replace("0X", "0x"), "7") == exposed_digest(f" {A} ", 7)
    assert d != exposed_digest(A, 8) and d != exposed_digest(B, 7)


def test_a_list_knows_what_it_was_given_and_nothing_else():
    lst = ExposedList.of(PAIRS)
    assert lst.has(A, 7) and lst.has(B.upper().replace("0X", "0x"), "12")
    assert not lst.has(A, 8) and not lst.has(B, 7) and not lst.has(OWNER, 7)
    assert len(lst) == 3 and lst.sees                       # the two, and the canary


def test_the_file_holds_digests_and_never_a_pair():
    text = render_digests(PAIRS)
    assert A[2:] not in text and "0x" not in text
    body = [ln for ln in text.splitlines() if not ln.startswith("#")]
    assert body == sorted(body) and len(body) == 3
    assert all(len(ln) == 16 and int(ln, 16) >= 0 for ln in body)
    assert exposed_digest(*CANARY) in body and "# entries: 3" in text


def test_a_list_never_shows_what_it_holds():
    lst = ExposedList.of(PAIRS)
    for text in (repr(lst), str(lst), lst.state()):
        assert "0x" not in text and exposed_digest(A, 7) not in text
    assert lst.state() == "2 expostos" and repr(lst) == "ExposedList(3 digests)"


@pytest.mark.parametrize("content", [None, "", "# only a header\n",
                                     exposed_digest(A, 7) + "\n"])
def test_a_list_without_its_canary_is_blind_and_refuses_to_answer(tmp_path, content):
    """A regra 8: uma guarda que aprova por NÃO encontrar precisa de provar
    que vê. Ficheiro em falta, vazio ou cortado nunca lê como "nada exposto"."""
    path = tmp_path / "exposed.txt"
    if content is not None:
        path.write_text(content)
    lst = ExposedList.load(path)                    # loading never raises
    assert lst.sees is False
    with pytest.raises(ExposedListBlind) as e:
        lst.has(A, 7)
    assert "0x" not in str(e.value)
    assert "ilegível" in lst.state() and "BLIND" in repr(lst)


def test_a_written_file_reads_back(tmp_path):
    path = tmp_path / "exposed.txt"
    path.write_text(render_digests(PAIRS))
    lst = ExposedList.load(path)
    assert lst.sees and lst.has(A, 7) and not lst.has(A, 8)


# --------------------------------------------------------------------------- #
# 3. O construtor: o histórico inteiro, e só contagens                          #
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                        "HOME": str(repo)})


@pytest.fixture
def history(tmp_path):
    """Um repositório com uma short-list que foi versionada e depois saiu,
    um JSON que mudou de conteúdo, e um ficheiro de código com um exemplo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "alvos.txt").write_text(f"base:{A}:7\n")
    (repo / "dados.json").write_text(json.dumps([{"contract": B, "tokenId": 12}]))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "um")
    (repo / "dados.json").write_text(json.dumps([{"contract": B, "tokenId": 13}]))
    (repo / "x.py").write_text(f'EXAMPLE = "{A}:99"\n')
    _git(repo, "rm", "-q", "--cached", "alvos.txt")
    _git(repo, "add", "dados.json", "x.py")
    _git(repo, "commit", "-q", "-m", "dois")
    return repo


def _builder():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_exposed_list", ROOT / "scripts" / "build_exposed_list.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_scan_reads_every_version_of_every_file(history):
    pairs, by_path, n_blobs = _builder().scan(history)
    assert pairs == {(A, 7), (B, 12), (B, 13), (A, 99)}     # removed, replaced, code
    assert by_path == {"alvos.txt": 1, "dados.json": 2, "x.py": 1}
    assert n_blobs == 4


def test_it_writes_digests_and_prints_counts_only(history, tmp_path, capsys):
    out = tmp_path / "exposed.txt"
    assert _builder().main(["--repo", str(history), "--out", str(out)]) == 0
    said = capsys.readouterr().out
    assert "4 par(es) distintos" in said and "dados.json" in said
    assert "0x" not in said and A[2:10] not in said and B[2:10] not in said
    lst = ExposedList.load(out)
    assert lst.sees and len(lst) == 5
    assert all(lst.has(c, t) for c, t in [(A, 7), (B, 12), (B, 13), (A, 99)])
    assert "0x" not in out.read_text()


def test_check_says_whether_the_written_file_covers_the_history(history, tmp_path, capsys):
    b = _builder()
    out = tmp_path / "exposed.txt"
    assert b.main(["--check", "--repo", str(history), "--out", str(out)]) == 1   # no file
    b.main(["--repo", str(history), "--out", str(out)])
    assert b.main(["--check", "--repo", str(history), "--out", str(out)]) == 0
    out.write_text(render_digests({(A, 7)}))                                   # incomplete
    assert b.main(["--check", "--repo", str(history), "--out", str(out)]) == 1
    assert "0x" not in capsys.readouterr().out


def test_the_list_only_grows(history, tmp_path):
    """O que já lá estava fica — mesmo que um dia o histórico seja outro."""
    out = tmp_path / "exposed.txt"
    out.write_text(render_digests({(OWNER, 5)}))
    _builder().main(["--repo", str(history), "--out", str(out)])
    lst = ExposedList.load(out)
    assert lst.has(OWNER, 5) and lst.has(A, 7) and len(lst) == 6


# --------------------------------------------------------------------------- #
# 4. O sorteio e o depósito recusam um exposto antes de qualquer leitura        #
# --------------------------------------------------------------------------- #


class _Counting(World):
    reads = 0

    def read_token(self, chain, contract, token_id):
        type(self).reads += 1
        return super().read_token(chain, contract, token_id)


def test_a_deposit_refuses_an_exposed_token_and_spends_nothing():
    _Counting.reads = 0
    world = _Counting()
    lst = ExposedList.of({(S.contract, 5)})
    larder = Larder()
    rep = _finder(world, is_exposed=lst.has).deposit(
        larder, [f"ethereum:{S.contract}:5", f"ethereum:{S.contract}:6"])
    assert rep.added == 1 and [c.token_id for c in larder.candidates] == [6]
    assert rep.rejected.exposed == 1 and _Counting.reads == 1     # only the other one
    report = rep.render()
    assert "exposto 1" in report and S.contract[2:10] not in report


def test_the_chain_does_not_matter_the_contract_and_the_token_do():
    lst = ExposedList.of({(S.contract, 5)})
    rep = _finder(World(), is_exposed=lst.has,
                  sources=[S]).deposit(Larder(), [f"base:{S.contract}:5"],
                                       chain_ok=lambda c: True)
    assert rep.added == 0 and rep.rejected.exposed == 1


def test_the_draw_skips_them_too():
    world = World()
    everything = ExposedList.of({(S.contract, n) for n in range(1, 1001)})
    larder = Larder()
    tally = _finder(world, is_exposed=everything.has).fill(larder, want=2, max_draws=12)
    assert larder.size() == 0 and tally.exposed == 12 and world.paid_calls == 0
    assert "exposto 12" in tally.render()
    assert cause_of(Tally(exposed=1)) == ("exposto", None)


def test_every_cause_the_checks_can_name_is_shown_by_the_probe():
    """Uma causa contada é uma causa mostrada. As três que entraram a 09/10
    (nome-longo, sem-identidade, exposto) eram contadas pela sonda e ficavam
    fora da linha — os números deixavam de somar as peças testadas."""
    counters = ("metadata", "name", "long_name", "no_identity", "exposed", "image",
                "too_big", "owner", "unique")
    col = ProbeColumn()
    for field in counters:
        cause = cause_of(Tally(**{field: 1}))
        assert cause[0] != "outro", field             # each has a name of its own
        col.add(cause)
    col.add(cause_of(Tally(not_ca={"arweave": 1})))
    col.add(cause_of(Tally(bad_meta={"pin-morto": 1})))
    col.add(cause_of(Tally(unavailable=1, unavailable_kinds={"rpc": 1})))
    col.add(None)                                         # one that passed
    line = col.render()
    for group in ("nome-longo 1", "sem-identidade 1", "exposto 1", "nome 1", "único 1"):
        assert group in line, (group, line)
    shown = sum(int(part.split(" (")[0].rsplit(" ", 1)[1]) for part in line.split(", ")
                if part.split(" (")[0].rsplit(" ", 1)[-1].isdigit())
    assert col.passed + shown == 13                       # nothing counted is missing


def test_a_cause_the_probe_was_never_told_about_is_still_shown():
    col = ProbeColumn()
    col.add(("uma-causa-nova", None))
    col.add(("uma-causa-nova", "tipo"))
    assert col.render() == "uma-causa-nova 2 (tipo 1)"


def test_a_blind_list_lets_nothing_in_and_says_it_is_ours():
    world = World()
    blind = ExposedList()                                     # no canary
    larder = Larder()
    rep = _finder(world, is_exposed=blind.has).deposit(larder, [f"ethereum:{S.contract}:5"])
    assert rep.added == 0 and larder.size() == 0 and world.paid_calls == 0
    assert rep.rejected.exposed == 0
    assert rep.rejected.unavailable_kinds == {"lista-de-expostos": 1}
    assert "indisponível-NOSSO 1 (lista-de-expostos 1)" in rep.render()


def test_without_a_list_the_finder_is_what_it_was():
    larder = Larder()
    rep = _finder(World()).deposit(larder, [f"ethereum:{S.contract}:5"])
    assert rep.added == 1 and rep.rejected.exposed == 0


# --------------------------------------------------------------------------- #
# 5. O /prepare retira-os da despensa antes de sortear                          #
# --------------------------------------------------------------------------- #


def _stored(token_id: int, contract: str = S.contract) -> Candidate:
    return Candidate(chain="ethereum", contract=contract, token_id=token_id,
                     name="Some Two Words", name_onchain="Some Two Words",
                     description="d", image=f"ipfs://img{token_id}",
                     token_uri="ipfs://x", artist="",
                     metadata={"name": "Some Two Words"})


def test_prepare_retires_the_exposed_ones_and_seals_another():
    world = World()
    lst = ExposedList.of({(S.contract, 5), (S.contract, 6)})
    larder = Larder()
    for tid in (5, 6, 7):
        larder.add(_stored(tid))
    said: list[str] = []
    prepared, left = _preparer(world, _finder(world, is_exposed=lst.has),
                               notify=said.append).prepare(larder)
    assert prepared.target.token_id == 7 and prepared.attempts == 1    # no attempt wasted
    assert left.size() == 0
    (line,) = [m for m in said if "ficheiros públicos" in m]
    assert line.startswith("prepare: 2 alvo(s) da despensa")
    assert all(S.contract[2:10] not in m for m in said)


def test_a_retired_target_is_never_drawn_again_and_is_not_the_last_one_used():
    larder = Larder()
    larder.add(_stored(1, contract=A))
    larder.consume(larder.candidates[0].id())               # the previous hunt
    last = larder.last_contract()
    larder.add(_stored(5))
    larder.retire(larder.candidates[0].id())
    assert larder.size() == 0 and larder.has(f"ethereum:{S.contract}:5")
    assert larder.last_contract() == last                   # still the previous hunt's


def test_a_larder_that_was_all_exposed_says_so():
    world = World()
    lst = ExposedList.of({(S.contract, 5)})
    larder = Larder()
    larder.add(_stored(5))
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, _finder(world, is_exposed=lst.has)).prepare(larder)
    assert "expostos" in str(e.value) and larder.size() == 0


def test_a_blind_list_stops_prepare_with_the_larder_whole():
    world = World()
    larder = Larder()
    for tid in (5, 6):
        larder.add(_stored(tid))
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, _finder(world, is_exposed=ExposedList().has)).prepare(larder)
    assert larder.size() == 2 and "despensa INTACTA" in str(e.value)
    # the cause is named: the list, not "gateway/RPC"
    assert "lista de expostos" in str(e.value) and "ExposedListBlind" in str(e.value)
    assert world.paid_calls == 0 and world.full_reads == []


# --------------------------------------------------------------------------- #
# 6. O /launch recusa uma preparação que seja de um exposto                     #
# --------------------------------------------------------------------------- #


def test_a_preparation_of_an_exposed_token_is_never_launched():
    """Uma preparação selada antes de a lista existir."""
    w = TargetWorld()
    w.ports.is_exposed = lambda contract, token_id: True
    with pytest.raises(LaunchRefused) as e:
        w.launch()
    assert not w.posts() and not w.rig.repo.hunts            # nothing published
    assert "ficheiro público do repositório" in str(e.value)
    assert "0x" not in str(e.value)


def test_a_blind_list_refuses_the_launch_too():
    w = TargetWorld()
    w.ports.is_exposed = ExposedList().has
    with pytest.raises(LaunchRefused) as e:
        w.launch()
    assert not w.posts() and "ExposedListBlind" in str(e.value)


def test_a_preparation_that_is_not_exposed_launches_as_ever():
    w = TargetWorld()
    asked: list[tuple] = []
    w.ports.is_exposed = lambda contract, token_id: asked.append((contract, token_id)) or False
    hunt = w.launch()
    t = hunt.target.target
    assert asked == [(t.contract, t.token_id)] and w.posts()


# --------------------------------------------------------------------------- #
# 7. Produção, e o que está (e não está) versionado                             #
# --------------------------------------------------------------------------- #


def _wiring():
    return build_target(settings(harvest_canary_ethereum="100:1"), anthropic=object(),
                        repo=FakeRepo(), http_get=lambda u, h: "{}", http_post=rpc_ok,
                        http_get_bytes=lambda u, h: b"")


def test_production_wires_the_list_everywhere_a_target_can_come_from():
    w = _wiring()
    assert isinstance(w.exposed, ExposedList) and w.exposed.sees
    assert w.finder._is_exposed == w.exposed.has                   # noqa: SLF001
    assert w.ports.is_exposed == w.exposed.has
    assert w.probes["manifold"]._finder._is_exposed == w.exposed.has   # noqa: SLF001
    assert w.finder.is_exposed(*CANARY) is True


def test_status_says_how_many_and_how_many_are_in_the_larder():
    class _Store:
        def __init__(self, larder):
            self._larder = larder

        def load(self):
            return self._larder

    lst = ExposedList.of({(S.contract, 5), (B, 12)})
    larder = Larder()
    for tid in (5, 6):
        larder.add(_stored(tid))
    kw = dict(ports=None, pipeline=None, epoch=None, snapshot_store=None, scan_blocks=0,
              writability_rates={}, uniqueness_rates={})
    line = TargetWiring(exposed=lst, larder_store=_Store(larder), **kw).exposed_line()
    assert line == ("lista de queimados: 2 expostos · 1 na despensa "
                    "(saem no próximo /prepare)")
    assert TargetWiring(exposed=lst, larder_store=_Store(Larder()), **kw).exposed_line() == (
        "lista de queimados: 2 expostos · nenhum na despensa")
    blind = TargetWiring(exposed=ExposedList(), larder_store=_Store(larder), **kw)
    assert "ilegível" in blind.exposed_line() and "0x" not in blind.exposed_line()
    assert TargetWiring(**kw).exposed_line() == "lista de queimados: não ligada"
    assert "target_wiring.exposed_line()" in open(main.__file__, encoding="utf-8").read()


def test_the_committed_list_is_readable_and_holds_only_digests():
    text = DEFAULT_PATH.read_text(encoding="utf-8")
    lst = ExposedList.load()
    assert lst.sees and len(lst) > 1000
    assert "0x" not in text
    body = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert body == sorted(set(body)) and all(len(ln) == 16 for ln in body)
    assert f"# entries: {len(body)}" in text
    assert exposed_mod.DEFAULT_PATH.name == "exposed.txt"


def test_every_pair_in_the_fixtures_the_tests_keep_is_on_the_list():
    """tests/fixtures tem respostas reais capturadas (pesquisas, páginas de
    plataformas). Ficam versionadas — os testes precisam delas — e por isso
    mesmo cada peça que lá está nunca pode ser alvo."""
    lst = ExposedList.load()
    found = missing = 0
    for path in sorted((ROOT / "tests" / "fixtures").rglob("*")):
        if path.is_file():
            for contract, token_id in pairs_in_blob(path.read_bytes()):
                found += 1
                missing += not lst.has(contract, token_id)
    assert found > 50 and missing == 0, f"{missing} de {found} pares em falta"


CANDIDATE_FILES = ("alvos_", "candidatos_", "escrevibilidade_resultados.json",
                   "familias_eth.json", "metadata_eth_raw.json", "opensea_search_raw.json")


def test_the_candidate_files_on_this_machine_are_all_on_the_list():
    """Na máquina do operador os ficheiros continuam lá (só deixaram de ser
    versionados). Onde existirem e tiverem estado no repositório, tudo o que
    têm está na lista. Falha com uma CONTAGEM, nunca com um par."""
    lst = ExposedList.load()
    files = [p for p in ROOT.iterdir() if p.is_file() and p.name.startswith(CANDIDATE_FILES)
             and p.name != "alvos_eth.txt"]                # never versioned: not exposed
    if not files:
        pytest.skip("os ficheiros de candidatos não estão nesta máquina")
    missing = sum(1 for p in files for c, t in pairs_in_blob(p.read_bytes())
                  if not lst.has(c, t))
    assert missing == 0, f"{missing} par(es) de ficheiros locais fora da lista"


def _tracked() -> list[str] | None:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.splitlines() or None


def test_no_candidate_file_is_versioned():
    tracked = _tracked()
    if tracked is None:
        pytest.skip("não é um checkout git")
    still = [p for p in tracked if "/" not in p and p.startswith(CANDIDATE_FILES)]
    assert still == [], f"{len(still)} ficheiro(s) de candidatos ainda versionados"


def test_the_ignore_file_covers_every_one_of_them():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("alvos_*.txt", "candidatos_*.json", "escrevibilidade_resultados.json",
                    "familias_eth.json", "metadata_eth_raw.json", "opensea_search_raw.json"):
        assert pattern in lines, pattern


def test_the_builder_agrees_with_the_committed_list():
    """Com o histórico à mão (a máquina do operador), a lista gravada cobre
    tudo o que o histórico expõe. Noutro sítio não há histórico: salta."""
    if _tracked() is None:
        pytest.skip("não é um checkout git")
    try:
        pairs, _by_path, _n = _builder().scan(ROOT)
    except subprocess.CalledProcessError:
        pytest.skip("histórico git indisponível")
    lst = ExposedList.load()
    missing = sum(1 for c, t in pairs if not lst.has(c, t))
    assert missing == 0, f"{missing} par(es) do histórico fora da lista — corre o construtor"

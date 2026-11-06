"""Posições DeFi fora da despensa — pedido do Pedro depois da hunt #34.

O QUE ACONTECEU. O tesouro da #34 foi uma posição de liquidez da Uniswap V3
(ethereum, token 104937, "Uniswap - 0.05% - EURT/WETH - 2319.3<>2359.1"). Os
jogadores queixaram-se, com razão: é um recibo financeiro, não uma peça de
colecção. Nada a parava — o nome tem duas palavras com letras, a metadata e a
imagem estão na cadeia (data:, aceite como o IPFS), uma posição é única por
construção, e cada posição criada é um mint ERC-721 que a colheita por blocos
encontra.

MEDIDO A 06/11 (RPC público, sem chave): os 25 contratos da lista responderam
com o name()/symbol() do próprio protocolo; os nomes dos tokens têm a forma
"Uniswap - 0.3% - A/B - x<>y" (Uniswap V3/V4 e Sushi V3) ou "CL - A/B - x<>y"
(Aerodrome); as descrições dizem "This NFT represents a liquidity position /
payment stream / stream…". O tokenURI da #34 (13 005 caracteres) está em
fixtures/target/hunt34_tokenuri.txt, tal como a cadeia o devolveu.

DECIDIDO (Pedro): nunca entram (o depósito — todos os caminhos do /harvest e
do /fill) e nunca são sorteadas no /prepare, mesmo já estando na despensa;
quantas há mostra-se antes de se apagar, e só se apagam depois de ele
confirmar: `/purge_defi` conta, `/purge_defi confirm` retira.
"""
from __future__ import annotations

import base64
import inspect
import json
import re
from pathlib import Path

import pytest
from test_target_prepare import World, _finder, _preparer

from finding_memeland.target.adapters import RpcError, collection_reader
from finding_memeland.target.defi import (
    BY_COLLECTION,
    BY_DESCRIPTION,
    BY_TOKEN_NAME,
    DEFI_CONTRACTS,
    DefiDetector,
    collection_signal,
    known_contract,
    token_signal,
)
from finding_memeland.target.harvest import HARVEST_CHAINS
from finding_memeland.target.prepare import (
    Candidate,
    Larder,
    LarderStore,
    PrepareRefused,
    Tally,
)
from finding_memeland.target.refresh import TokenRead
from finding_memeland.target.sources import ChainRpc, ChainUnavailable

FIXTURE = Path(__file__).parent / "fixtures" / "target" / "hunt34_tokenuri.txt"
HUNT34_ID = "ethereum:0xc36442b4a4522e871399cd717abdd847ab11fe88:104937"
NPM = HUNT34_ID.split(":")[1]
HUNT34_URI = FIXTURE.read_text()
HUNT34_META = json.loads(base64.b64decode(HUNT34_URI.split(",", 1)[1]))
HUNT34_NAME = "Uniswap - 0.05% - EURT/WETH - 2319.3<>2359.1"

ART = "0x" + "d7" * 20            # synthetic: sorts after the position manager
S_CONTRACT = "0x" + "ab" * 20     # the World fake's own source contract
OTHER = "0x" + "a1" * 20          # synthetic: an unknown DeFi deployment


def _cand(contract: str, tid: int, meta: dict, chain: str = "ethereum") -> Candidate:
    return Candidate(chain=chain, contract=contract, token_id=tid,
                     name=str(meta.get("name")), name_onchain=str(meta.get("name")),
                     description=str(meta.get("description") or ""),
                     image=str(meta.get("image") or ""),
                     token_uri=f"ipfs://Qm{tid}", artist="", metadata=meta)


def _art(contract: str = ART, tid: int = 1) -> Candidate:
    return _cand(contract, tid, {"name": "Salt Harbor", "image": f"ipfs://img{tid}",
                                 "description": "a quiet place by the sea"})


HUNT34 = _cand(NPM, 104937, HUNT34_META)


class _FirstOfPool:
    def randrange(self, n):
        return 0


class Collections:
    """collection_of(chain, contract) over a dict; counts the reads."""

    def __init__(self, names=None, *, down=()):
        self.names = dict(names or {})
        self.down = set(down)
        self.calls = []

    def __call__(self, chain, contract):
        self.calls.append(contract)
        if contract in self.down:
            raise ChainUnavailable("rpc down")
        return self.names.get(contract, ("Salt Harbor Editions", "SALT"))


# --------------------------------------------------------------------------- #
# The real case and the signals                                                #
# --------------------------------------------------------------------------- #


def test_the_fixture_is_the_token_of_hunt_34():
    assert len(HUNT34_URI) == 13_005 and HUNT34_URI.startswith("data:application/json;base64,")
    assert HUNT34_META["name"] == HUNT34_NAME
    assert HUNT34_META["image"].startswith("data:image/svg+xml;base64,")


def test_every_signal_catches_hunt_34():
    assert known_contract("ethereum", NPM) == "uniswap-v3"
    assert known_contract("ethereum", NPM.upper().replace("0X", "0x")) == "uniswap-v3"
    assert token_signal(HUNT34_META) == BY_TOKEN_NAME
    assert token_signal({"description": HUNT34_META["description"]}) == BY_DESCRIPTION
    assert collection_signal("Uniswap V3 Positions NFT-V1", "UNI-V3-POS") == BY_COLLECTION


@pytest.mark.parametrize("name", [
    HUNT34_NAME,
    "Uniswap - 0.3% - USDC/HLX - 0.060479<>0.062696",      # V3, measured 06/11
    "Uniswap - 0.05% - USDC/WETH - MIN<>MAX",              # V4
    "Uniswap - 0.3% - UNI/USDC - 0.14195<>MAX",            # V3 on Base
    "CL - USDC/ETH - 2126.0<>2445.5",                      # Aerodrome Slipstream
])
def test_the_measured_position_names(name):
    assert token_signal({"name": name}) == BY_TOKEN_NAME


@pytest.mark.parametrize("description", [
    HUNT34_META["description"],
    "This NFT represents a liquidity position in an Aerodrome Finance USDC-ETH pool.",
    "This NFT represents a payment stream in a Sablier V2 Lockup Linear contract.",
    "This NFT represents a stream in a Sablier Lockup contract. The owner of this NFT",
])
def test_the_measured_descriptions(description):
    assert token_signal({"name": "x", "description": description}) == BY_DESCRIPTION


@pytest.mark.parametrize("name, symbol", [
    ("Uniswap V3 Positions NFT-V1", "UNI-V3-POS"), ("Uniswap v4 Positions NFT", "UNI-V4-POSM"),
    ("Pancake V3 Positions NFT-V1", "PCS-V3-POS"),
    ("SushiSwap V3 Positions NFT-V1", "SUSHI-V3-POS"),
    ("Slipstream Position NFT v1", "AERO-CL-POS"), ("veNFT", "veNFT"),
    ("Sablier V2 Lockup Linear NFT", "SAB-V2-LOCKUP-LIN"), ("Sablier Lockup NFT", "SAB-LOCKUP"),
    ("TokenVestingPlans", "TVP"), ("VotingTokenLockupPlans", "VTLP"),
    ("Some Collection", "UNI-V3-POS"),                      # the symbol alone is enough
])
def test_the_measured_collection_names(name, symbol):
    assert collection_signal(name, symbol) == BY_COLLECTION


@pytest.mark.parametrize("meta", [
    {"name": "Salt Harbor", "description": "a quiet place by the sea"},
    {"name": "Night - Day"}, {"name": "Rock/Paper - Scissors"}, {"name": "A <> B"},
    {"name": "Liquid Dreams", "description": "a painting about liquidity and loss"},
    {"name": "Positions of the Moon", "description": "this nft represents a feeling"},
    {"name": "Streams - 2021 - North/South"},
])
def test_art_is_not_a_position(meta):
    assert token_signal(meta) is None


@pytest.mark.parametrize("name, symbol", [
    ("Positions of the Moon", "MOON"), ("Sablier", "HOURGLASS"), ("Le Sablier", "SAB"),
    ("Salt Harbor Editions", "SALT"), ("Proof of Position", "POSE"), ("", ""),
])
def test_art_collections_are_not_positions(name, symbol):
    assert collection_signal(name, symbol) is None


def test_the_known_contracts_are_the_25_measured_on_the_larders_chains():
    assert set(DEFI_CONTRACTS) <= set(HARVEST_CHAINS)
    assert {c: len(v) for c, v in DEFI_CONTRACTS.items()} == {"ethereum": 16, "base": 9}
    for chain in DEFI_CONTRACTS.values():
        for addr in chain:
            assert re.fullmatch(r"0x[0-9a-f]{40}", addr), addr
    labels = {label for chain in DEFI_CONTRACTS.values() for label in chain.values()}
    assert labels == {"uniswap-v3", "uniswap-v4", "pancakeswap-v3", "sushiswap-v3",
                      "sablier", "hedgey", "aerodrome-cl", "veaero"}


# --------------------------------------------------------------------------- #
# The detector                                                                  #
# --------------------------------------------------------------------------- #


def test_the_collection_is_read_once_and_a_failed_read_is_no_verdict():
    names = Collections({OTHER: ("Pancake V3 Positions NFT-V1", "PCS-V3-POS")}, down={ART})
    d = DefiDetector(collection_of=names)
    assert d("ethereum", OTHER) == BY_COLLECTION and d("ethereum", OTHER) == BY_COLLECTION
    assert names.calls.count(OTHER) == 1
    assert d("ethereum", ART) is None and d("ethereum", ART) is None
    assert names.calls.count(ART) == 2 and d.unread == 2         # read again, counted
    assert d("ethereum", NPM) == "uniswap-v3" and NPM not in names.calls   # known: no read


# --------------------------------------------------------------------------- #
# Never in: the deposit (every way into the larder)                            #
# --------------------------------------------------------------------------- #


class World34(World):
    """The fake chain, with hunt #34's token as the chain answers it."""

    reads = 0

    def read_token(self, chain, contract, token_id):
        self.reads += 1
        if contract.lower() in (NPM, OTHER):
            return TokenRead(token_uri=HUNT34_URI, metadata=dict(HUNT34_META))
        return super().read_token(chain, contract, token_id)


def test_hunt_34_never_enters_the_larder():
    world = World34()
    names = Collections()
    finder = _finder(world, defi=DefiDetector(collection_of=names))
    larder = Larder()
    rep = finder.deposit(larder, [HUNT34_ID])
    assert larder.size() == 0
    assert rep.rejected.defi == 1 and rep.rejected.defi_kinds == {"uniswap-v3": 1}
    assert "posição-DeFi 1 (uniswap-v3 1)" in rep.rejected.causes()
    assert world.paid_calls == 0 and world.probes == []      # nothing else was asked
    assert world.reads == 0 and names.calls == []            # known: not even a read


def test_an_unknown_deployment_is_caught_by_its_metadata():
    world = World34()
    finder = _finder(world, defi=DefiDetector())
    larder = Larder()
    rep = finder.deposit(larder, [f"ethereum:{OTHER}:5"])
    assert larder.size() == 0 and rep.rejected.defi_kinds == {BY_TOKEN_NAME: 1}
    assert world.paid_calls == 0


def test_a_neutral_name_is_caught_by_the_collection_after_the_free_checks():
    world = World()                                      # an art-looking name
    names = Collections({S_CONTRACT: ("Slipstream Position NFT v1", "AERO-CL-POS")})
    finder = _finder(world, defi=DefiDetector(collection_of=names))
    larder = Larder()
    rep = finder.deposit(larder, [f"ethereum:{S_CONTRACT}:7"])
    assert larder.size() == 0 and rep.rejected.defi_kinds == {BY_COLLECTION: 1}
    assert world.paid_calls == 0 and world.probes == []


def test_art_still_goes_in():
    world = World()
    finder = _finder(world, defi=DefiDetector(collection_of=Collections()))
    larder = Larder()
    finder.deposit(larder, [f"ethereum:{S_CONTRACT}:7"])
    assert larder.size() == 1


def test_without_a_detector_hunt_34_went_in():
    """O que aconteceu antes: as cinco verificações deixavam-na entrar."""
    world = World34()
    larder = Larder()
    _finder(world).deposit(larder, [HUNT34_ID])
    assert larder.size() == 1


def test_the_tally_names_the_cause():
    t = Tally()
    t.note_defi("uniswap-v3")
    t.note_defi("uniswap-v3")
    t.note_defi(BY_COLLECTION)
    assert t.causes() == "posição-DeFi 3 (nome-da-colecção 1, uniswap-v3 2)"


# --------------------------------------------------------------------------- #
# Never drawn: /prepare                                                         #
# --------------------------------------------------------------------------- #


def _prepare(larder, *, defi, notes=None):
    world = World()
    prep = _preparer(world, _finder(world), defi=defi,
                     notify=(notes.append if notes is not None else None))
    prep._rng = _FirstOfPool()
    return prep.prepare(larder)


def test_hunt_34_is_never_drawn_and_stays_in_the_larder():
    notes = []
    larder = Larder(candidates=[HUNT34, _art()])
    prepared, after = _prepare(larder, defi=DefiDetector(collection_of=Collections()),
                               notes=notes)
    assert prepared.target.contract == ART
    assert [c.id() for c in after.candidates] == [HUNT34_ID]      # kept, not spent
    assert HUNT34_ID not in after.used
    assert ("prepare: 1 posição(ões) DeFi (uniswap-v3 1) na despensa — fora do "
            "sorteio; ficam até /purge_defi confirm") in notes


def test_without_the_detector_the_draw_took_it():
    """A #34: o primeiro do sorteio era a posição."""
    prepared, _ = _prepare(Larder(candidates=[HUNT34, _art()]), defi=None)
    assert prepared.target.id() == HUNT34_ID


def test_a_contract_caught_by_its_collection_costs_no_attempt_and_stays():
    notes = []
    names = Collections({OTHER: ("veNFT", "veNFT")})
    neutral = _cand(OTHER, 3, {"name": "Lock Thirty", "image": "ipfs://img3"})
    larder = Larder(candidates=[neutral, _art()])
    prepared, after = _prepare(larder, defi=DefiDetector(collection_of=names), notes=notes)
    assert prepared.target.contract == ART and prepared.attempts == 1
    assert [c.id() for c in after.candidates] == [neutral.id()]
    assert any("pelo nome da colecção" in n for n in notes)
    assert all("0x" not in n for n in notes)


def test_prepare_on_one_chain_counts_that_chains_positions():
    base_position = _cand("0x03a520b32c04bf3beef7beb72e919cf822ed34f1", 9,   # uniswap-v3, Base
                          {"name": "x", "image": "ipfs://img9"}, chain="base")
    larder = Larder(candidates=[HUNT34, base_position])
    world = World()
    prep = _preparer(world, _finder(world), defi=DefiDetector(collection_of=Collections()))
    prep._rng = _FirstOfPool()
    with pytest.raises(PrepareRefused) as err:
        prep.prepare(larder, chain="base")
    assert "1 posição(ões) DeFi (uniswap-v3 1) fora do sorteio" in str(err.value)
    assert larder.size() == 2


def test_only_positions_left_is_a_refusal_with_the_larder_whole():
    larder = Larder(candidates=[HUNT34])
    with pytest.raises(PrepareRefused) as err:
        _prepare(larder, defi=DefiDetector(collection_of=Collections()))
    assert "1 posição(ões) DeFi (uniswap-v3 1) fora do sorteio" in str(err.value)
    assert "0x" not in str(err.value)
    assert larder.size() == 1 and larder.used == []


# --------------------------------------------------------------------------- #
# /purge_defi — count first, retire only on confirm                            #
# --------------------------------------------------------------------------- #


class _Plain:
    @staticmethod
    def encrypt(p):
        return p

    @staticmethod
    def decrypt(p):
        return p


def _wiring(larder: Larder, names: Collections):
    from finding_memeland.target.wiring import TargetWiring
    blob = [None]
    store = LarderStore(cipher=_Plain(), read=lambda: blob[0],
                        write=lambda b: blob.__setitem__(0, b))
    store.save(larder)
    w = TargetWiring(ports=None, pipeline=None, epoch=None, snapshot_store=None,
                     scan_blocks=0, writability_rates={}, uniqueness_rates={},
                     larder_store=store, defi=DefiDetector(collection_of=names))
    return w, store


def test_purge_counts_first_and_retires_nothing():
    names = Collections({OTHER: ("Pancake V3 Positions NFT-V1", "PCS-V3-POS")})
    neutral = _cand(OTHER, 3, {"name": "Thirty Days", "image": "ipfs://img3"})
    w, store = _wiring(Larder(candidates=[HUNT34, neutral, _art()]), names)
    reply = w.purge_defi()
    assert reply == ("posições DeFi na despensa: 2 de 3 (nome-da-colecção 1, "
                     "uniswap-v3 1) — nada foi retirado. Para as retirar: "
                     "/purge_defi confirm")
    assert store.load().size() == 3


def test_purge_confirm_retires_them_for_good():
    names = Collections({OTHER: ("Pancake V3 Positions NFT-V1", "PCS-V3-POS")})
    neutral = _cand(OTHER, 3, {"name": "Thirty Days", "image": "ipfs://img3"})
    w, store = _wiring(Larder(candidates=[HUNT34, neutral, _art()],
                              used=["ethereum:0x" + "e5" * 20 + ":9"]), names)
    reply = w.purge_defi(confirm=True)
    assert reply == ("retiradas 2 posição(ões) DeFi da despensa (nome-da-colecção 1, "
                     "uniswap-v3 1) · despensa 1")
    after = store.load()
    assert [c.contract for c in after.candidates] == [ART]
    # retired like the exposed: never deposited again, never "the last one used"
    assert set(after.used[:2]) == {HUNT34_ID, neutral.id()}
    assert after.used[-1] == "ethereum:0x" + "e5" * 20 + ":9"
    assert after.has(HUNT34_ID)


def test_purge_says_counts_only():
    names = Collections(down={ART})
    w, _ = _wiring(Larder(candidates=[HUNT34, _art()]), names)
    reply = w.purge_defi()
    assert "nome da colecção por ler (RPC NOSSO): 1 contrato(s)" in reply
    for secret in ("0x", "104937", "Uniswap - ", "EURT", "Salt Harbor"):
        assert secret not in reply


def test_purge_with_nothing_to_retire():
    w, store = _wiring(Larder(candidates=[_art()]), Collections())
    assert w.purge_defi(confirm=True) == "posições DeFi na despensa: 0 de 1 — nada a retirar"
    assert store.load().size() == 1


def test_the_command_is_wired_and_guarded():
    from finding_memeland import main
    from finding_memeland.telegram.approval_queue import TELEGRAM_COMMANDS
    src = inspect.getsource(main.build_agent)
    body = src[src.index("def _purge_defi("):src.index("actions = {")]
    assert body.index('if word not in ("", "confirm")') < body.index("_target_job(")
    assert '_target_job("purge_defi",' in body
    assert "target_wiring.purge_defi(confirm=word == \"confirm\")" in body
    assert '"purge_defi": _purge_defi' in src and "purge_defi" in TELEGRAM_COMMANDS


def test_production_wires_one_detector_everywhere():
    from finding_memeland.target import wiring
    src = inspect.getsource(wiring.build_target)
    assert "defi = DefiDetector(collection_of=collection_reader(rpcs))" in src
    assert src.count("defi=defi") == 4       # finder, probe finder, preparer, wiring


# --------------------------------------------------------------------------- #
# name() / symbol() on the chain (adapters)                                    #
# --------------------------------------------------------------------------- #


def _abi_string(text: str) -> str:
    raw = text.encode()
    pad = (32 - len(raw) % 32) % 32
    return ("0x" + (32).to_bytes(32, "big").hex() + len(raw).to_bytes(32, "big").hex()
            + (raw + b"\x00" * pad).hex())


def _rpc(answers) -> ChainRpc:
    def eth_call(to, data):
        a = answers[data]
        if isinstance(a, BaseException):
            raise a
        return a
    return ChainRpc(chain="ethereum", eth_call=eth_call, get_code=lambda _a: "0x")


def test_collection_reader_reads_name_and_symbol():
    rpc = _rpc({"0x06fdde03": _abi_string("Uniswap V3 Positions NFT-V1"),
                "0x95d89b41": _abi_string("UNI-V3-POS")})
    assert collection_reader({"ethereum": rpc})("ethereum", NPM) == (
        "Uniswap V3 Positions NFT-V1", "UNI-V3-POS")


def test_an_old_bytes32_name_and_a_revert_are_the_contracts_answer():
    rpc = _rpc({"0x06fdde03": "0x" + b"OLDNAME".ljust(32, b"\x00").hex(),
                "0x95d89b41": RpcError(3, "execution reverted", revert=True)})
    assert collection_reader({"ethereum": rpc})("ethereum", NPM) == ("OLDNAME", "")


@pytest.mark.parametrize("answer", [
    ChainUnavailable("timeout"), RpcError(-32603, "Internal error", revert=False)])
def test_a_failed_read_is_ours(answer):
    rpc = _rpc({"0x06fdde03": answer, "0x95d89b41": answer})
    with pytest.raises(ChainUnavailable):
        collection_reader({"ethereum": rpc})("ethereum", NPM)
    with pytest.raises(ChainUnavailable):
        collection_reader({})("base", NPM)

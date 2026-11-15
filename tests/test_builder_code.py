"""O Base Builder Code em cada payout — pedido do Pedro a 15/11 (ERC-8021).

O sufixo vai no FIM do calldata da transferência ERC-20 do prémio. NÃO foi
escrito à mão (a documentação contradiz-se sobre a ordem dos bytes): gerado a
15/11 pelo verificador builder-code-checker.vercel.app (Encode, Schema 0,
código bc_lcripzqu) e confirmado contra o código de `Attribution.toDataSuffix`
do ox (ox/erc8021, a biblioteca que a documentação da Base indica):
codes ∥ codesLength (1 byte) ∥ schemaId (1 byte) ∥ 0x8021 × 8.

MEDIDO NA BASE a 15/11 (RPC público, nada assinado nem enviado), com a
transferência do payout da #34: com e sem o sufixo, o token devolve true e o
eth_simulateV1 dá o mesmo evento Transfer(from, to, valor) — o do payout real.
O token é um clone mínimo que passa o calldata inteiro, por delegatecall, a uma
implementação fixa; a implementação lê só os primeiros 4 + 64 bytes. O sufixo
custa gás (~460 a mais, medido), e o payout da #34 correu com 378 de folga: o
gás tem de ser estimado com o calldata final — é o que estes testes prendem.

A cadeia aqui é falsa e local; a chave é sintética (sem fundos). Nenhum teste
toca na rede nem na carteira do prémio.
"""
from __future__ import annotations

import inspect

import pytest
import rlp
from eth_abi import decode as abi_decode
from web3 import Web3
from web3.providers.base import BaseProvider

from finding_memeland.chain import payout as payout_module
from finding_memeland.chain.payout import (
    BUILDER_CODE,
    BUILDER_CODE_SUFFIX,
    PayoutEngine,
    with_builder_code,
)

TOKEN = "0x" + "46" * 20                                 # synthetic token
WINNER = "0x" + "5e" * 20                                # synthetic winner
TEST_KEY = "0x" + "11" * 32                              # synthetic, no funds
TRANSFER = bytes.fromhex("a9059cbb")                     # transfer(address,uint256)
MARKER = bytes.fromhex("80218021802180218021802180218021")


def _parse_8021(data: bytes) -> tuple[int, list[str]]:
    """ERC-8021 read from the end, as ox's `Attribution.fromData` does:
    marker (16), schema (1), codes length (1), codes. A check, never the
    source of the bytes."""
    assert data[-16:] == MARKER, "no ERC-8021 marker at the end"
    schema, length = data[-17], data[-18]
    codes = data[-18 - length:-18].decode("ascii")
    return schema, codes.split(",")


class FakeBase(BaseProvider):
    """A chain that answers what the payout asks. eth_estimateGas charges
    the calldata like the chain does (16 a non-zero byte, 4 a zero one), so
    a longer calldata needs more gas — what the real suffix costs."""

    def __init__(self):
        super().__init__()
        self.estimates: list[str] = []
        self.sent: list[bytes] = []

    def is_connected(self, show_traceback: bool = False) -> bool:
        return True

    def make_request(self, method, params):
        if method == "eth_chainId":
            result = hex(8453)
        elif method == "eth_gasPrice":
            result = hex(5_000_000)
        elif method == "eth_getTransactionCount":
            result = hex(7)
        elif method == "eth_estimateGas":
            data = params[0]["data"]
            self.estimates.append(data)
            raw = bytes.fromhex(data[2:])
            result = hex(21_000 + 23_000 + sum(16 if b else 4 for b in raw))
        elif method == "eth_sendRawTransaction":
            self.sent.append(bytes.fromhex(params[0][2:]))
            result = "0x" + "ab" * 32
        elif method == "eth_getTransactionReceipt":
            result = {"status": "0x1", "transactionHash": "0x" + "ab" * 32,
                      "transactionIndex": "0x0", "blockHash": "0x" + "cd" * 32,
                      "blockNumber": "0x1", "from": "0x" + "00" * 20, "to": TOKEN,
                      "cumulativeGasUsed": "0x1", "gasUsed": "0x1", "logs": [],
                      "logsBloom": "0x" + "00" * 256, "contractAddress": None,
                      "effectiveGasPrice": "0x1", "type": "0x0"}
        else:
            raise AssertionError(f"unexpected RPC {method}")
        return {"jsonrpc": "2.0", "id": 1, "result": result}


def _pay(amount: int = 20_000_000) -> tuple[FakeBase, list]:
    chain = FakeBase()
    engine = PayoutEngine(web3=Web3(chain), token_address=TOKEN, hot_wallet_key=TEST_KEY,
                          per_hunt_cap=1_000_000_000, decimals=18)
    engine.send_prize(hunt_id=34, to_wallet=WINNER, amount_fmml=amount)
    (raw,) = chain.sent
    nonce, gas_price, gas, to, value, data, v, r, s = rlp.decode(raw)
    return chain, [int.from_bytes(gas, "big"), "0x" + to.hex(), data]


# --------------------------------------------------------------------------- #
# The suffix                                                                   #
# --------------------------------------------------------------------------- #


def test_the_suffix_is_the_one_the_tools_generated():
    """Do verificador (Encode, Schema 0) a 15/11, palavra a palavra."""
    assert BUILDER_CODE == "bc_lcripzqu"
    assert BUILDER_CODE_SUFFIX == "62635f6c637269707a71750b0080218021802180218021802180218021"


def test_the_check_reads_oxs_own_example_the_same_way():
    """The parser of these tests reads ox's documented example as ox does
    ("baseapp", schema 0) — so the order it checks is the library's."""
    example = bytes.fromhex("dddddddd62617365617070070080218021802180218021802180218021")
    assert _parse_8021(example) == (0, ["baseapp"])


def test_the_suffix_reads_back_as_our_code_on_the_canonical_registry():
    suffix = bytes.fromhex(BUILDER_CODE_SUFFIX)
    assert len(suffix) == len(BUILDER_CODE) + 1 + 1 + 16 == 29
    assert _parse_8021(suffix) == (0, [BUILDER_CODE])


def test_it_goes_on_once():
    data = "0xa9059cbb" + "00" * 64
    once = with_builder_code(data)
    assert once == data + BUILDER_CODE_SUFFIX
    assert with_builder_code(once) == once
    assert with_builder_code(bytes.fromhex(data[2:])) == once
    assert with_builder_code(data[2:]) == once


# --------------------------------------------------------------------------- #
# The payout, end to end (the real _submit_transfer, a fake chain)             #
# --------------------------------------------------------------------------- #


def test_the_payout_calldata_ends_with_the_suffix():
    _chain, (_gas, to, data) = _pay()
    assert to == TOKEN
    assert data.hex().endswith(BUILDER_CODE_SUFFIX)
    assert _parse_8021(data) == (0, [BUILDER_CODE])


def test_the_transfer_is_the_same_transfer():
    """What the token reads — the selector and the two words after it — is
    exactly the transfer of before; the suffix is all that follows."""
    _chain, (_gas, _to, data) = _pay(amount=20_000_000)
    assert data[:4] == TRANSFER
    to, value = abi_decode(["address", "uint256"], data[4:68])
    assert (to.lower(), value) == (WINNER, 20_000_000 * 10**18)
    assert data[68:] == bytes.fromhex(BUILDER_CODE_SUFFIX)
    assert len(data) == 4 + 64 + 29


def test_the_gas_is_estimated_with_the_suffix():
    """The #34 payout ran with 378 gas to spare and the suffix costs ~460:
    an estimate made without it would send a payout that runs out of gas."""
    chain, (gas, _to, data) = _pay()
    without, with_it = chain.estimates[0], chain.estimates[-1]
    assert not without.endswith(BUILDER_CODE_SUFFIX)        # web3's own, first
    assert with_it == "0x" + data.hex()                     # then the final calldata
    cost = sum(16 if b else 4 for b in bytes.fromhex(BUILDER_CODE_SUFFIX))
    assert gas == int(_estimate_of(with_it), 16)
    assert gas - int(_estimate_of(without), 16) == cost == 452


def _estimate_of(data: str) -> str:
    raw = bytes.fromhex(data[2:])
    return hex(21_000 + 23_000 + sum(16 if b else 4 for b in raw))


def test_the_transaction_is_signed_once_and_by_the_payout_key():
    from eth_account import Account
    chain, _ = _pay()
    assert len(chain.sent) == 1
    sender = Account.recover_transaction("0x" + chain.sent[0].hex())
    assert sender == Account.from_key(TEST_KEY).address


def test_the_cap_still_comes_first():
    chain = FakeBase()
    engine = PayoutEngine(web3=Web3(chain), token_address=TOKEN, hot_wallet_key=TEST_KEY,
                          per_hunt_cap=100, decimals=18)
    with pytest.raises(ValueError):
        engine.send_prize(hunt_id=34, to_wallet=WINNER, amount_fmml=101)
    assert chain.sent == [] and chain.estimates == []


def test_only_the_payout_changed():
    """O caminho do pagamento, e só ele: o resto do módulo é o de antes."""
    src = inspect.getsource(payout_module.PayoutEngine._submit_transfer)
    assert src.count("with_builder_code(") == 1 and src.count("estimate_gas(") == 1

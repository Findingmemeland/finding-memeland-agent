"""ManifoldMinter._send against a REAL EVM (py-evm via eth-tester), with a
minimal stand-in for Manifold's implementation. Proves the three-step mint end
to end: the deployed runtime is the 298 Manifold bytes verbatim, mintBase lands
the token on the relic wallet with the pinned URI, renounceOwnership freezes the
metadata (setTokenURI reverts), and the trophy transfer works through the proxy.

Skipped when eth-tester/py-evm are not installed (they are not runtime deps):
    pip install "eth-tester[py-evm]"
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("eth_tester")
web3 = pytest.importorskip("web3")

from finding_memeland.persona.relic_mint import (  # noqa: E402
    MANIFOLD_ABI,
    ManifoldMinter,
    load_manifold_artifact,
)

FIXTURE = Path(__file__).parent / "fixtures" / "FakeManifoldImpl.json"


class _Wallets:
    """Key resolver over an eth-tester account (its key is a known test key)."""

    def __init__(self, address, key):
        self._address, self._key = address, key

    def signing_key(self, wallet_ref):
        return self._address, self._key


class _Pinner:
    def pin(self, data: bytes, *, name: str = "relic.png") -> str:
        return "ipfs://bafyMETADATA"


@pytest.fixture
def chain():
    from eth_tester import EthereumTester, PyEVMBackend
    from web3 import EthereumTesterProvider, Web3

    backend = PyEVMBackend()
    tester = EthereumTester(backend)
    w3 = Web3(EthereumTesterProvider(tester))
    key = backend.account_keys[0]
    return w3, w3.eth.accounts[0], key.to_bytes()


def test_manifold_three_step_mint_on_a_real_evm(chain):
    w3, wallet, key = chain
    fake = json.loads(FIXTURE.read_text())
    deploy = w3.eth.contract(abi=fake["abi"], bytecode=fake["bytecode"]).constructor()
    impl = w3.eth.wait_for_transaction_receipt(deploy.transact({"from": wallet})).contractAddress

    art = load_manifold_artifact()
    # The stand-in lives at a local address, so the override is explicit here.
    minter = ManifoldMinter(
        web3=w3, wallets=_Wallets(wallet, key), pinner=_Pinner(), artifact=art,
        implementation=impl, allow_implementation_override=True,
    )
    res = minter.deploy_and_mint(
        name="Maroon Ledger", symbol="MLDG", description="kept the books\n\ncode: ABCDEFGH",
        image_uri="ipfs://bafyIMG", attributes='[{"trait_type":"maker","value":"x"}]',
        provenance_hash="0x" + "00" * 32, wallet_ref="RW01",
    )

    # 1. the deployed code IS the Manifold proxy runtime, verbatim, and the
    #    EIP-1967 slot holds the implementation address (not our own copy)
    assert w3.eth.get_code(res.contract) == bytes.fromhex(art["manifold_runtime"][2:])
    slot = w3.eth.get_storage_at(res.contract, ManifoldMinter.IMPLEMENTATION_SLOT)
    assert "0x" + bytes(slot).hex()[-40:] == impl.lower()
    # 2. the token exists, on the relic wallet, with the pinned URI
    proxy = w3.eth.contract(address=res.contract, abi=fake["abi"])
    assert res.token_id == "1"
    assert proxy.functions.ownerOf(1).call() == wallet
    assert proxy.functions.tokenURI(1).call() == "ipfs://bafyMETADATA"
    assert proxy.functions.name().call() == "Maroon Ledger"
    # 3. ownership renounced -> metadata frozen
    ownable = w3.eth.contract(address=res.contract, abi=MANIFOLD_ABI)
    assert int(ownable.functions.owner().call(), 16) == 0
    with pytest.raises(Exception, match="Must be owner or admin"):
        proxy.functions.setTokenURI(1, "evil").transact({"from": wallet})
    # 4. the trophy still moves (ERC-721 transfer from the relic wallet)
    winner = w3.eth.accounts[1]
    w3.eth.wait_for_transaction_receipt(
        proxy.functions.safeTransferFrom(wallet, winner, 1).transact({"from": wallet})
    )
    assert proxy.functions.ownerOf(1).call() == winner


def test_manifold_minter_refuses_an_implementation_without_code(chain):
    w3, wallet, key = chain
    art = load_manifold_artifact()
    minter = ManifoldMinter(
        web3=w3, wallets=_Wallets(wallet, key), pinner=_Pinner(), artifact=art,
        implementation="0x" + "11" * 20, allow_implementation_override=True,
    )
    with pytest.raises(RuntimeError, match="has no code"):
        minter.deploy_and_mint(
            name="X Y", symbol="XY", description="d", image_uri="ipfs://i", attributes="[]",
            provenance_hash="0x" + "00" * 32, wallet_ref="RW01",
        )

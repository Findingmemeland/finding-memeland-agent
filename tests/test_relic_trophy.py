"""Tests for the trophy transfer (package 4) — offline, fakes only.

The properties that matter: never send twice, retry transient failures, and NEVER
let a trophy problem look like a lost prize (the $FIND is already paid — a failure
must alert with manual-send details).
"""

from __future__ import annotations

import pytest

from finding_memeland.chain.relic_trophy import (
    FakeNFTTransfer, TrophyTransferFailed, transfer_trophy,
)
from finding_memeland.persona.relic import Relic, RelicState


def _minted_relic():
    return Relic(id="r1", state=RelicState.IN_PLAY, chain="base", contract="0xaa",
                 token_id="1", mint_wallet_ref="W1", commitment="h")


class _Notifier:
    def __init__(self): self.messages = []
    def notify(self, text): self.messages.append(text)


def test_transfer_sends_to_the_winner():
    port = FakeNFTTransfer()
    res = transfer_trophy(relic=_minted_relic(), to_wallet="0xWINNER", transfer_port=port)
    assert res.delivered and res.tx_hash
    assert port.sent[0] == {"contract": "0xaa", "token_id": "1",
                            "to": "0xWINNER", "wallet_ref": "W1"}


def test_transfer_is_idempotent_when_already_owned():
    """A retry after an unseen receipt must not send a second transaction."""
    port = FakeNFTTransfer()
    port.owners[("0xaa", "1")] = "0xwinner"          # already there (different case)
    res = transfer_trophy(relic=_minted_relic(), to_wallet="0xWINNER", transfer_port=port)
    assert res.already_owned and res.delivered and res.tx_hash is None
    assert port.sent == []                            # nothing sent


def test_transfer_retries_transient_failures():
    port = FakeNFTTransfer(fail_times=2)
    res = transfer_trophy(relic=_minted_relic(), to_wallet="0xWINNER",
                          transfer_port=port, attempts=3)
    assert res.delivered and len(port.sent) == 1


def test_exhausted_retries_alert_with_manual_send_details():
    port = FakeNFTTransfer(fail_times=99)
    note = _Notifier()
    with pytest.raises(TrophyTransferFailed) as e:
        transfer_trophy(relic=_minted_relic(), to_wallet="0xWINNER",
                        transfer_port=port, notifier=note, attempts=2)
    msg = str(e.value)
    # the operator must see: prize is safe + everything needed to send by hand
    assert "prize WAS paid" in msg
    assert "0xaa" in msg and "token 1" in msg and "W1" in msg and "0xWINNER" in msg
    assert note.messages and "TROPHY NOT DELIVERED" in note.messages[0]


def test_unreadable_owner_does_not_block_the_send():
    class _Flaky(FakeNFTTransfer):
        def owner_of(self, contract, token_id):
            raise RuntimeError("rpc down")
    port = _Flaky()
    res = transfer_trophy(relic=_minted_relic(), to_wallet="0xWINNER", transfer_port=port)
    assert res.delivered and len(port.sent) == 1


def test_relic_without_coordinates_is_refused():
    with pytest.raises(TrophyTransferFailed, match="no on-chain coordinates"):
        transfer_trophy(relic=Relic(id="r1"), to_wallet="0xW", transfer_port=FakeNFTTransfer())


def test_missing_wallet_is_refused():
    with pytest.raises(TrophyTransferFailed, match="winner wallet missing"):
        transfer_trophy(relic=_minted_relic(), to_wallet="", transfer_port=FakeNFTTransfer())


def test_notifier_failure_never_masks_the_transfer_error():
    class _BadNotifier:
        def notify(self, text): raise RuntimeError("telegram down")
    port = FakeNFTTransfer(fail_times=99)
    with pytest.raises(TrophyTransferFailed, match="TROPHY NOT DELIVERED"):
        transfer_trophy(relic=_minted_relic(), to_wallet="0xW",
                        transfer_port=port, notifier=_BadNotifier(), attempts=1)

from finding_memeland.chain.payout import PayoutEngine


class _CapturingEngine(PayoutEngine):
    """Overrides the on-chain submit so we can test cap + scaling without web3."""

    def __init__(self, **kw):
        super().__init__(web3=None, token_address="0xtoken", hot_wallet_key="k", **kw)
        self.submitted = None

    def _submit_transfer(self, to_checksummed, base_amount):
        self.submitted = {"to": to_checksummed, "base_amount": base_amount}
        return "0xfeedbeef"


def test_scales_whole_tokens_to_base_units():
    eng = _CapturingEngine(per_hunt_cap=1_000_000, decimals=18)
    r = eng.send_prize(hunt_id=1, to_wallet="0xabc", amount_fmml=500_000)
    assert eng.submitted["base_amount"] == 500_000 * 10**18
    assert r.amount_fmml == 500_000
    assert r.tx_hash == "0xfeedbeef"


def test_enforces_per_hunt_cap():
    eng = _CapturingEngine(per_hunt_cap=100_000, decimals=18)
    try:
        eng.send_prize(hunt_id=1, to_wallet="0xabc", amount_fmml=100_001)
    except ValueError:
        assert eng.submitted is None  # never attempted the transfer
        return
    raise AssertionError("expected ValueError when exceeding the cap")


def test_rejects_nonpositive_amount():
    eng = _CapturingEngine(per_hunt_cap=100_000)
    for bad in (0, -5):
        try:
            eng.send_prize(hunt_id=1, to_wallet="0xabc", amount_fmml=bad)
        except ValueError:
            continue
        raise AssertionError("expected ValueError for non-positive amount")


def test_amount_at_cap_is_allowed():
    eng = _CapturingEngine(per_hunt_cap=100_000, decimals=6)
    r = eng.send_prize(hunt_id=1, to_wallet="0xabc", amount_fmml=100_000)
    assert eng.submitted["base_amount"] == 100_000 * 10**6
    assert r.amount_fmml == 100_000

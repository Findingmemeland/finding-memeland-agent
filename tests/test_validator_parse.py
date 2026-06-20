from finding_memeland.dm.validator import parse_dm

ADDR = "0x52908400098527886E0F7030069857D2E4169EE7"


def test_parses_wallet_and_code():
    p = parse_dm("dm1", "u1", f"my code is ABCDEFGH and wallet {ADDR}")
    assert p.wallet == ADDR
    assert p.claim_code == "ABCDEFGH"


def test_missing_wallet_is_none():
    p = parse_dm("dm2", "u1", "ABCDEFGH but I forgot my address")
    assert p.wallet is None
    assert p.claim_code == "ABCDEFGH"


def test_address_not_mistaken_for_code():
    p = parse_dm("dm3", "u1", ADDR)
    assert p.wallet == ADDR
    assert p.claim_code is None

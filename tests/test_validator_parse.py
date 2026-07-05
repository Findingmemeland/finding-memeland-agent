from types import SimpleNamespace

from finding_memeland.dm.validator import DMValidator, parse_dm

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


def test_eight_letter_word_does_not_shadow_real_code():
    # "personal" and "treasure" are 8 chars; the real code appears later.
    p = parse_dm("dm4", "u1", f"my personal treasure! code AB2CD3EF wallet {ADDR}")
    assert "AB2CD3EF" in p.claim_candidates
    assert p.claim_candidates[0] == "PERSONAL"  # first token still recorded


class _ChainOK:
    def has_continuous_holding(self, **_):
        return True


class _XOK:
    def has_reshared(self, **_):
        return True


def _human_profile(_x_id):
    return {"name": "Ana", "handle": "ana_hunts", "bio": "meme lover", "automated": False}


def _hunt(code="AB2CD3EF"):
    return SimpleNamespace(
        claim_code=code, min_balance_fmml=0, holding_hours=24, reshare_post_id="1"
    )


def test_validator_accepts_code_after_shadowing_word():
    v = DMValidator(chain=_ChainOK(), x_client=_XOK(), profile_lookup=_human_profile)
    dm = parse_dm("dm5", "u1", f"absolute banger! code AB2CD3EF wallet {ADDR}")
    res = v.validate(dm, _hunt())
    assert res.won and res.outcome == "won"


def test_validator_still_rejects_wrong_code():
    v = DMValidator(chain=_ChainOK(), x_client=_XOK(), profile_lookup=_human_profile)
    dm = parse_dm("dm6", "u1", f"code WRONGONE wallet {ADDR}")
    res = v.validate(dm, _hunt())
    assert not res.won and res.outcome == "bad_code"

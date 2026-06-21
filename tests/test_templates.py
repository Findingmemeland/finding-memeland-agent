from finding_memeland.content.templates import WinnerData, winner_announcement


def _data(persona_handle, winner_handle):
    return WinnerData(
        hunt_n=1, winner_handle=winner_handle, time_to_win="2h 10m",
        prize_amount="500,000", tx_link="0xtx", persona_handle=persona_handle,
        persona_user_id="100", claim_code="ABCDEFGH", salt="s",
    )


def test_handles_not_double_prefixed_when_already_at():
    out = winner_announcement(_data("@hidden_one", "@winner_one"))
    assert "@@" not in out
    assert "@hidden_one" in out
    assert "@winner_one" in out


def test_handles_prefixed_when_missing_at():
    out = winner_announcement(_data("hidden_two", "winner_two"))
    assert "@hidden_two" in out
    assert "@winner_two" in out
    assert "@@" not in out


def test_reveals_integrity_ingredients():
    out = winner_announcement(_data("@h", "@w"))
    assert "100" in out and "ABCDEFGH" in out and "salt" in out.lower()

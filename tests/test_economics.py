from finding_memeland.economics import fdv_from_price, suggested_prize


def test_floor_below_100k_fdv():
    assert suggested_prize(15_000) == 200
    assert suggested_prize(30_000) == 200
    assert suggested_prize(100_000) == 200  # joint: 1% of $20k fund = $200


def test_scales_between_100k_and_250k():
    p = suggested_prize(175_000)  # midway-ish
    assert 200 < p < 500
    assert round(p) == 350  # 0.002 * 175k


def test_cap_at_and_above_250k():
    assert suggested_prize(250_000) == 500  # joint: 1% of $50k fund = $500
    assert suggested_prize(1_000_000) == 500
    assert suggested_prize(50_000_000) == 500


def test_continuous_no_jump_at_joints():
    # just below/above the joints shouldn't jump more than a cent
    assert abs(suggested_prize(99_999) - suggested_prize(100_001)) < 1
    assert abs(suggested_prize(249_999) - suggested_prize(250_001)) < 1


def test_fdv_from_price():
    assert round(fdv_from_price(0.00001, 100_000_000_000)) == 1_000_000


def test_zero_or_negative_fdv_returns_floor():
    assert suggested_prize(0) == 200
    assert suggested_prize(-5) == 200

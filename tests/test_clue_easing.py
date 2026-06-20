from finding_memeland.content.clue_engine import obliqueness_for


def test_obliqueness_decreases_monotonically():
    vals = [obliqueness_for(i) for i in range(1, 8)]
    assert vals[0] == 1.0
    assert all(a > b for a, b in zip(vals, vals[1:]))


def test_roughly_30_percent_easier_each_step():
    assert abs(obliqueness_for(2) - 0.70) < 1e-9
    assert abs(obliqueness_for(3) - 0.49) < 1e-9

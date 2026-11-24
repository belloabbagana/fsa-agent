from fsa.analyse.comps import position, quartiles

CFG = {"min_n_for_quartiles": 5, "near_band_pct": 15, "abs_band_when_median_near_zero": 0.5}


def test_small_group_uses_the_median_band():
    assert position(100.0, [90.0, 110.0], CFG)[0] == "near"            # median 100
    assert position(130.0, [90.0, 110.0], CFG)[0] == "above"
    assert position(70.0, [90.0, 110.0], CFG)[0] == "below"
    pos, method, small = position(100.0, [100.0], CFG)
    assert pos == "near" and small and "indicative" in method           # one peer only


def test_large_group_uses_the_quartiles():
    peers = [10.0, 20.0, 30.0, 40.0, 50.0]                              # Q1 = 20, Q3 = 40
    assert position(15.0, peers, CFG)[0] == "below"
    assert position(30.0, peers, CFG)[0] == "near"
    assert position(45.0, peers, CFG)[0] == "above"
    assert position(45.0, peers, CFG)[2] is False


def test_no_peers_is_not_a_position():
    assert position(5.0, [], CFG)[0] == "no peers"


def test_a_median_near_zero_uses_an_absolute_band():
    assert position(0.3, [0.0, 0.1], CFG)[0] == "near"                  # 15% of ~0 would be silly
    assert position(5.0, [0.0, 0.1], CFG)[0] == "above"


def test_quartiles():
    assert quartiles([1.0, 2.0, 3.0, 4.0, 5.0]) == (2.0, 3.0, 4.0)
    assert quartiles([4.0]) == (None, 4.0, None)

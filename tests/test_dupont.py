"""Five-stage DuPont: the stages multiply to ROE, the split of a change adds up exactly, and the explanation cannot carry an unsupplied number."""
import math

from fsa.analyse import dupont as dp


def year(fy, tax, interest, margin, turn, mult, owners=1.0):
    legs = {"tax_burden": tax, "interest_burden": interest, "operating_margin": margin, "asset_turnover": turn, "equity_multiplier": mult, "owners_share": owners}
    roe = math.prod(v / 100 if k == "operating_margin" else v for k, v in legs.items()) * 100
    return dp.finish(fy, roe, legs) | {"context": {"core_operating_margin": 30.0}, "accounts": {"PL_REVENUE": 1000.0}}


A, B = year(2024, 0.70, 0.85, 40.0, 0.60, 2.5), year(2025, 0.66, 0.90, 35.0, 0.50, 2.8, 0.98)


def test_stages_multiply_to_roe():
    assert abs(A["identity_gap_pp"]) < 1e-9 and abs(B["identity_gap_pp"]) < 1e-9


def test_change_split_adds_up_exactly():
    c = dp.decompose(A, B)
    assert c["status"] == "ok" and abs(sum(c["contributions_pp"].values()) - c["delta_roe_pp"]) < 1e-9 and abs(c["residual_pp"]) < 1e-9
    assert c["contributions_pp"]["tax_burden"] < 0 < c["contributions_pp"]["interest_burden"]       # 0.70 to 0.66 falls; 0.85 to 0.90 rises


def test_split_is_order_independent_and_not_defined_for_negative_stage():
    swapped = dp.decompose(B, A)
    assert abs(sum(swapped["contributions_pp"].values()) - swapped["delta_roe_pp"]) < 1e-9
    neg = year(2025, 0.66, -0.9, 35.0, 0.5, 2.8)
    assert dp.decompose(A, neg)["status"] == "N/M"


def test_leverage_effect():
    assert abs(A["roe_before_leverage"] * A["legs"]["equity_multiplier"] - A["roe"]) < 1e-9 and A["leverage_adds_pp"] > 0


def test_commentary_with_a_number_not_supplied_is_rejected():
    data = {"ticker": "T", "fiscal_year": 2025, "years": [A, B], "changes": [dp.decompose(A, B)]}
    good = {"summary": f"ROE was {dp.fwr.show(B['roe'])}% in 2025.", "years": {"2025": "Asset turnover was 0.50."}, "to_examine": []}
    assert dp.check_commentary(data, good) == []
    bad = {"summary": "ROE was 99.9% in 2025.", "years": {"2025": "ok"}, "to_examine": []}
    assert any("numbers not in the supplied figures" in p for p in dp.check_commentary(data, bad))
    assert any("banned" in p for p in dp.check_commentary(data, {"summary": "Management may have manipulated margins.", "years": {"2025": "x"}, "to_examine": []}))
    assert any("underscore" in p for p in dp.check_commentary(data, {"summary": "The tax_burden fell.", "years": {"2025": "x"}, "to_examine": []}))
